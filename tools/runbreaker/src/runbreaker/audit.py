"""Append-only audit trail for trips and denials.

A safety device nobody can account for after the fact is hard to trust. This is
the cheapest possible ledger: one JSON object per line, rotated once it grows past
a megabyte.

Never raises. Losing an audit line is acceptable; wedging a tool call is not.
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

MAX_BYTES = 1024 * 1024


def record(
    path: Path,
    *,
    event: str,
    decision: str,
    session_id: str = "",
    provider: str = "",
    tool: str = "",
    reason: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "event": event,
        "decision": decision,
        "provider": provider,
        "session_id": session_id,
        "tool": tool,
        "reason": reason,
        "detail": detail or {},
    }
    with suppress(OSError, ValueError, TypeError):
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def _rotate(path: Path) -> None:
    with suppress(OSError):
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_suffix(path.suffix + ".1"))


def _read_one(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return entries
    for line in text.splitlines():
        with suppress(ValueError):
            obj = json.loads(line)
            if isinstance(obj, dict):
                entries.append(obj)
    return entries


def read(path: Path) -> list[dict[str, Any]]:
    """Every audit entry, oldest first — the rotated `.1` file then the live one."""
    return _read_one(path.with_suffix(path.suffix + ".1")) + _read_one(path)


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate audit entries into counts by outcome and by provider."""
    by_outcome: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    for entry in entries:
        outcome = f"{entry.get('event', '?')}/{entry.get('decision', '?')}"
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        provider = str(entry.get("provider") or "")
        if provider:
            by_provider[provider] = by_provider.get(provider, 0) + 1
    return {
        "total": len(entries),
        "by_outcome": dict(sorted(by_outcome.items())),
        "by_provider": dict(sorted(by_provider.items())),
    }
