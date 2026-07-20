"""Claude Code: sum `message.usage` across the transcript JSONL, split per model.

Usage is reported per assistant message, so the session total is a sum. Cache
*reads* are excluded on purpose: at roughly a tenth of input price and recounted
every turn, they inflate the total until a token budget stops meaning anything.
Fresh tokens are the runaway signal.

Each message also carries `message.model`, and input and output bill at different
rates, so we accumulate `(input, output)` per model — that is what makes
`cost_budget` accurate here rather than a single blended guess. `input` folds in
`cache_creation_input_tokens` (a fresh write, billed like input); `cache_read` is
excluded as above.

Known upstream limitation: transcripts can be missing the final `message_stop`
(anthropics/claude-code#27361), so output tokens are a lower bound. The
`token_budget` condition trips at a fraction of the limit partly for this reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from runbreaker.events import HookEvent
from runbreaker.tokens.base import (
    UNKNOWN,
    Cache,
    ModelUsage,
    ProviderUsage,
    TokenSource,
    as_int,
    first_hit,
    incremental_fold,
)

USAGE_PATHS = ("message.usage", "usage")
MODEL_PATHS = ("message.model", "model")
INPUT_FIELDS = ("input_tokens", "cache_creation_input_tokens")
COUNTED_FIELDS = (*INPUT_FIELDS, "output_tokens")


def usage_tokens(record: dict[str, Any]) -> int | None:
    """One message's counted tokens (input + cache-creation + output), or None.

    Kept as the per-message sum; `_fold` splits the same fields per model for cost.
    """
    usage = first_hit(record, USAGE_PATHS)
    if not isinstance(usage, dict):
        return None
    counted = [as_int(usage.get(field)) for field in COUNTED_FIELDS]
    if all(value is None for value in counted):
        return None
    return sum(value for value in counted if value is not None)


def _fold(state: dict[str, int], record: dict[str, Any]) -> None:
    usage = first_hit(record, USAGE_PATHS)
    if not isinstance(usage, dict):
        return
    # Record when a usage field is *present*, even if zero: a genuine 0 must read as 0,
    # not "unknown". Absent-entirely stays unknown (the record carried no usage).
    if all(usage.get(f) is None for f in COUNTED_FIELDS):
        return
    inp = sum(as_int(usage.get(f)) or 0 for f in INPUT_FIELDS)
    out = as_int(usage.get("output_tokens")) or 0
    model = str(first_hit(record, MODEL_PATHS) or "unknown")
    state[f"i:{model}"] = state.get(f"i:{model}", 0) + inp
    state[f"o:{model}"] = state.get(f"o:{model}", 0) + out


def by_model(state: dict[str, int]) -> list[ModelUsage]:
    acc: dict[str, list[int]] = {}
    for key, value in state.items():
        io, _, model = key.partition(":")
        pair = acc.setdefault(model, [0, 0])
        if io == "i":
            pair[0] += int(value)
        elif io == "o":
            pair[1] += int(value)
    return [ModelUsage(model, i, o) for model, (i, o) in acc.items()]


class ClaudeTokenSource(TokenSource):
    id: ClassVar[str] = "claude"

    def read(self, event: HookEvent, cache: Cache) -> ProviderUsage:
        path = event.transcript_path or cache.get("transcript_path")
        if not path:
            return UNKNOWN
        cache["transcript_path"] = path
        state = incremental_fold(Path(path), cache, "claude", _fold)
        if not state:  # unreadable, or no usage seen yet
            return UNKNOWN
        models = by_model(state)
        total = sum(m.input_tokens + m.output_tokens for m in models)
        return ProviderUsage(total_tokens=total, by_model=tuple(models))
