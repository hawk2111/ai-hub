"""Claude Code: sum `message.usage` across the transcript JSONL.

Usage is reported per assistant message, so the session total is a sum. Cache
*reads* are excluded on purpose: at roughly a tenth of input price and recounted
every turn, they inflate the total until a token budget stops meaning anything.
Fresh tokens are the runaway signal.

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
    ProviderUsage,
    TokenSource,
    as_int,
    first_hit,
    incremental_sum,
)

USAGE_PATHS = ("message.usage", "usage")
COUNTED_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens")


def usage_tokens(record: dict[str, Any]) -> int | None:
    usage = first_hit(record, USAGE_PATHS)
    if not isinstance(usage, dict):
        return None
    counted = [as_int(usage.get(field)) for field in COUNTED_FIELDS]
    if all(value is None for value in counted):
        return None
    return sum(value for value in counted if value is not None)


class ClaudeTokenSource(TokenSource):
    id: ClassVar[str] = "claude"

    def read(self, event: HookEvent, cache: Cache) -> ProviderUsage:
        path = event.transcript_path or cache.get("transcript_path")
        if not path:
            return UNKNOWN
        cache["transcript_path"] = path
        total = incremental_sum(Path(path), cache, "claude", usage_tokens)
        return ProviderUsage(total_tokens=total)
