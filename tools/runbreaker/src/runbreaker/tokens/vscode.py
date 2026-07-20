"""Copilot in VS Code: token usage from a session transcript, counted correctly.

VS Code records usage differently from the OpenAI/Anthropic per-request shape, so it
needs its own accounting. In a real session transcript, `promptTokens` is the size of
the **whole growing context** sent each turn (35857 → 59317 → … → 82018), not that
turn's new input; `completionTokens` is per-turn output. Summing every record — what
Copilot CLI's reader does — would count the context over and over and overcount wildly.
The honest total is **max(promptTokens) + sum(completionTokens)**: the current context
window plus everything generated. That mirrors what VS Code's UI calls "cumulative
context window token usage".

Where the numbers come from, in order:

1. `RUNBREAKER_VSCODE_USAGE_LOG` — an opt-in pointer to the session log. VS Code only
   records usage when agent session logging is on
   (`github.copilot.chat.agentDebugLog.fileLogging.enabled`) and never hands that path
   to a hook, so a power user points us at it. A file is used as-is; a directory or a
   glob resolves to the **most recently modified** match (the active session).
2. Otherwise the hook's own `transcript_path` — but VS Code sends it only on Stop, and
   its format is "not a stable hook API", so this usually yields nothing.

Either way, unreadable or unrecognized input returns `None` (unknown) — never a guess.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Any, ClassVar

from runbreaker.events import HookEvent
from runbreaker.tokens.base import (
    UNKNOWN,
    Cache,
    ProviderUsage,
    TokenSource,
    as_int,
    incremental_fold,
)

USAGE_LOG_ENV = "RUNBREAKER_VSCODE_USAGE_LOG"
#: Real transcripts bury the counts at unstable, varying paths (`v[*].result.metadata`,
#: `v.metadata`, or the top level), so we search each record recursively for these field
#: names rather than trusting one shape.
_PROMPT_FIELDS = frozenset({"promptTokens", "prompt_tokens", "inputTokens", "input_tokens"})
_COMPLETION_FIELDS = frozenset(
    {"completionTokens", "completion_tokens", "outputTokens", "output_tokens"}
)


def _collect(obj: Any, fields: frozenset[str], out: list[int]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in fields:
                number = as_int(value)
                if number is not None:
                    out.append(number)
            _collect(value, fields, out)
    elif isinstance(obj, list):
        for value in obj:
            _collect(value, fields, out)


def _fold(state: dict[str, int], record: dict[str, Any]) -> None:
    prompts: list[int] = []
    completions: list[int] = []
    _collect(record, _PROMPT_FIELDS, prompts)
    _collect(record, _COMPLETION_FIELDS, completions)
    if prompts:  # promptTokens is the whole context each turn — peak, don't add up
        state["prompt_max"] = max(state.get("prompt_max", 0), *prompts)
    if completions:  # completionTokens is per-turn output — accumulate
        state["completion_sum"] = state.get("completion_sum", 0) + sum(completions)


def _match(pattern: str, session_id: str) -> Path | None:
    """Resolve the knob to one file.

    A single file is used as-is. A directory or glob is expanded and then, so that
    concurrent VS Code windows do not read each other's usage, we prefer the file
    named for *this* session (VS Code names each session log by its id) before
    falling back to the most recently modified match.
    """
    base = Path(pattern).expanduser()
    if base.is_file():
        return base
    globbed = f"{base}/**/*.jsonl" if base.is_dir() else str(base)
    try:
        # glob.glob, not Path.glob: the pattern is an arbitrary (often absolute) user
        # string with `**`, which Path.glob cannot take without splitting out a base.
        matches = (Path(m) for m in glob.glob(globbed, recursive=True))  # noqa: PTH207
        files = [p for p in matches if p.is_file()]
        if not files:
            return None
        if session_id:
            for path in files:
                if path.stem == session_id:
                    return path
        return max(files, key=lambda p: p.stat().st_mtime)
    except OSError:
        return None


def _resolve_path(event: HookEvent, cache: Cache) -> Path | None:
    configured = os.environ.get(USAGE_LOG_ENV)
    if configured:
        return _match(configured, event.session_id)
    # Fall back to the hook-provided transcript (Stop only), caching it for reuse.
    path = event.transcript_path or cache.get("transcript_path")
    if path:
        cache["transcript_path"] = str(path)
        return Path(path)
    return None


class VSCodeTokenSource(TokenSource):
    id: ClassVar[str] = "vscode"

    def read(self, event: HookEvent, cache: Cache) -> ProviderUsage:
        path = _resolve_path(event, cache)
        if path is None:
            return UNKNOWN
        state = incremental_fold(path, cache, self.id, _fold)
        if not state:  # unreadable, or nothing recognized yet
            return UNKNOWN
        # promptTokens is the current context (peak); completionTokens is total output.
        # Cost stays blended-on-total: VS Code bills in credits, not per-model tokens.
        total = state.get("prompt_max", 0) + state.get("completion_sum", 0)
        return ProviderUsage(total_tokens=total)
