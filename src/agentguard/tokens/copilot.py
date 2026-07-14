"""Copilot CLI: best-effort, and usually unknown.

Copilot does track tokens — `/usage` prints per-model totals — but it exposes
them to neither the hook payload nor any documented file. github/copilot-cli#2947
asks for programmatic access and is still open.

The one lead is `transcript_path`, which arrives on the Stop payload and only
there. The Stop handler refreshes the ledger, which caches the path, so later
PreToolUse calls can reuse it. The transcript's schema is undocumented, so this
returns `None` far more often than not, and Copilot is in practice guarded by
`step_budget` alone. That is a documented limitation, not an oversight.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from agentguard.events import HookEvent
from agentguard.tokens.base import (
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
COUNTED_FIELDS = ("input_tokens", "output_tokens", "inputTokens", "outputTokens")


def usage_tokens(record: dict[str, Any]) -> int | None:
    usage = first_hit(record, USAGE_PATHS)
    if not isinstance(usage, dict):
        return None
    explicit = as_int(first_hit(usage, TOTAL_PATHS))
    if explicit is not None:
        return explicit
    counted = [as_int(usage.get(field)) for field in COUNTED_FIELDS]
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
            total_tokens=incremental_sum(Path(path), cache, "copilot", usage_tokens)
        )
