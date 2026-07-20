"""Copilot CLI: best-effort, and usually unknown.

Copilot does track tokens — `/usage` prints per-model totals — but it exposes
them to neither the hook payload nor any documented file. github/copilot-cli#2947
asks for programmatic access and is still open.

The one lead is `transcript_path`, which arrives on the Stop payload and only
there. The Stop handler refreshes the ledger, which caches the path, so later
PreToolUse calls can reuse it. The transcript's schema is undocumented, so this
returns `None` far more often than not, and Copilot is in practice guarded by the
provider-agnostic conditions (`step_budget`, `time_budget`, `repeat_loop`,
`gate_failures`). That is a documented limitation, not an oversight.

The same reader backs Copilot in VS Code (`tokens/vscode.py`), which faces the
same wall.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from runbreaker.events import HookEvent
from runbreaker.tokens.base import (
    UNKNOWN,
    Cache,
    ProviderUsage,
    TokenSource,
    as_int,
    first_hit,
    incremental_sum,
)

USAGE_PATHS = ("usage", "message.usage", "tokenUsage", "token_usage")
TOTAL_PATHS = ("total_tokens", "totalTokens")
#: Both the OpenAI/Anthropic naming (`input_tokens`) and VS Code's (`promptTokens`,
#: `completionTokens`). A record uses one scheme, so summing them cannot double count.
COUNTED_FIELDS = (
    "input_tokens",
    "output_tokens",
    "inputTokens",
    "outputTokens",
    "promptTokens",
    "completionTokens",
)


def usage_tokens(record: dict[str, Any]) -> int | None:
    # Prefer an explicit usage object; otherwise read the fields off the record
    # itself, since some transcripts put the counts at the top level.
    usage = first_hit(record, USAGE_PATHS)
    scope = usage if isinstance(usage, dict) else record
    explicit = as_int(first_hit(scope, TOTAL_PATHS))
    if explicit is not None:
        return explicit
    counted = [as_int(scope.get(field)) for field in COUNTED_FIELDS]
    if all(value is None for value in counted):
        return None
    return sum(value for value in counted if value is not None)


class CopilotTokenSource(TokenSource):
    id: ClassVar[str] = "copilot"

    def read(self, event: HookEvent, cache: Cache) -> ProviderUsage:
        path = event.transcript_path or cache.get("transcript_path")
        if not path:
            return UNKNOWN
        cache["transcript_path"] = path
        return ProviderUsage(
            total_tokens=incremental_sum(Path(path), cache, self.id, usage_tokens)
        )
