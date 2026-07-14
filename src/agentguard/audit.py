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
