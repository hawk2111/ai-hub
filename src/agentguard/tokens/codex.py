"""Codex CLI: read the newest `token_count` record from the session rollout.

Two things differ from Claude and both are easy to get wrong:

* Codex totals are **cumulative**, so we take the last record rather than
  summing — summing would multiply-count the whole session.
* The rollout filename embeds a timestamp *before* the session id
  (`rollout-2026-05-21T21-18-45-<uuid>.jsonl`), so the glob needs a wildcard on
  both sides.

The same record carries `rate_limits`, a provider-computed percentage of the
plan's window. That number is authoritative and immune to token-schema drift,
which is why `rate_limit_pressure` prefers it over raw token counts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from agentguard.events import HookEvent
from agentguard.tokens.base import (
    UNKNOWN,
    Cache,
    ProviderUsage,
    TokenSource,
    as_float,
    as_int,
    first_hit,
    iter_tail_json_lines,
)

TOTAL_PATHS = (
    "payload.info.total_token_usage.total_tokens",
    "payload.info.total_tokens",
    "info.total_token_usage.total_tokens",
)
RATE_LIMIT_PATHS = (
    "payload.rate_limits.primary.used_percent",
    "rate_limits.primary.used_percent",
)
TYPE_PATHS = ("payload.type", "type")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def find_rollout(session_id: str) -> Path | None:
    if not session_id:
        return None
    matches = list(codex_home().glob(f"sessions/**/rollout-*-{session_id}.jsonl"))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


class CodexTokenSource(TokenSource):
    id: ClassVar[str] = "codex"

    def read(self, event: HookEvent, cache: Cache) -> ProviderUsage:
        path = self._resolve(event, cache)
        if path is None:
            return UNKNOWN

        last: dict[str, Any] | None = None
        for record in iter_tail_json_lines(path):
            if first_hit(record, TYPE_PATHS) == "token_count":
                last = record
        if last is None:
            return UNKNOWN

        return ProviderUsage(
            total_tokens=as_int(first_hit(last, TOTAL_PATHS)),
            rate_limit_percent=as_float(first_hit(last, RATE_LIMIT_PATHS)),
        )

    def _resolve(self, event: HookEvent, cache: Cache) -> Path | None:
        cached = cache.get("rollout_path")
        if cached and Path(cached).exists():
            return Path(cached)
        try:
            found = find_rollout(event.session_id)
        except OSError:
            return None
        if found is not None:
            cache["rollout_path"] = str(found)
        return found
