"""Circuit-breaker state machine.

When the breaker is OPEN, file mutations are denied: the run drops to read-only
and surfaces to a human instead of looping on a broken task or burning budget.
Reads, tests and `agentguard reset` stay available, so there is no deadlock.

Two rules carried over from the prototype, both deliberate:

* `record_success()` clears the consecutive-fail counter but never *closes* an
  open breaker. Closing is a human (or launcher) decision; a single green gate
  is not evidence that whatever tripped it is fixed.
* Counting failures and deciding to trip are separate. `record_fail()` only
  counts; the `gate_failures` condition owns the threshold. That keeps every
  trip decision in one registry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from agentguard.state import Store

NAME = "breaker"

CORRUPT_REASON = "state unreadable — treated as open"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _closed() -> dict[str, Any]:
    return {"state": "closed", "fails": 0, "reason": "", "tripped_at": ""}


@dataclass(frozen=True)
class BreakerStatus:
    state: Literal["open", "closed"]
    fails: int
    reason: str
    tripped_at: str
    corrupt: bool = False

    @property
    def is_open(self) -> bool:
        return self.state == "open"


def _status_of(data: dict[str, Any], *, corrupt: bool = False) -> BreakerStatus:
    return BreakerStatus(
        state="open" if data.get("state") == "open" else "closed",
        fails=int(data.get("fails", 0)),
        reason=str(data.get("reason", "")),
        tripped_at=str(data.get("tripped_at", "")),
        corrupt=corrupt,
    )


class Breaker:
    def __init__(self, store: Store) -> None:
        self._store = store

    def status(self) -> BreakerStatus:
        draft = self._store.read(NAME, _closed)
        if draft.corrupt:
            # A truncated breaker file most likely came from a crash mid-write,
            # during exactly the kind of chaotic run the breaker exists to stop.
            # Reading it as "closed" would be a fail-open hole in a safety device.
            return BreakerStatus("open", 0, CORRUPT_REASON, "", corrupt=True)
        return _status_of(draft.data)

    def trip(self, reason: str) -> BreakerStatus:
        with self._store.update(NAME, _closed) as draft:
            draft.data.update(
                state="open",
                reason=reason or "manual trip",
                tripped_at=_now(),
            )
            return _status_of(draft.data)

    def reset(self) -> BreakerStatus:
        with self._store.update(NAME, _closed) as draft:
            draft.data = _closed()
            return _status_of(draft.data)

    def record_fail(self) -> int:
        """Count one failed quality gate. Returns the consecutive-fail total."""
        with self._store.update(NAME, _closed) as draft:
            if draft.corrupt or draft.data.get("state") == "open":
                draft.write = False
                return int(draft.data.get("fails", 0))
            draft.data["fails"] = int(draft.data.get("fails", 0)) + 1
            return int(draft.data["fails"])

    def record_success(self) -> None:
        """Clear the consecutive-fail counter. Never closes an open breaker."""
        with self._store.update(NAME, _closed) as draft:
            if draft.corrupt or draft.data.get("state") == "open" or not draft.data.get("fails"):
                draft.write = False
                return
            draft.data["fails"] = 0
