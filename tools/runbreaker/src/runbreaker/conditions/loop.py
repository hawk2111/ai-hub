"""Trip when the agent is stuck repeating itself.

A run that calls the *same tool with the same arguments* over and over — or bounces
between two identical calls (edit, test, edit, test, with the edit never changing) —
is looping, not progressing. The step budget would eventually catch it, but only
after hundreds of wasted calls; this catches the pattern directly.

Detection is exact, not heuristic: two calls match only when their fingerprints
match (`HookEvent.fingerprint`), which means same tool and same input. A retry that
changes even one argument is not a loop, so ordinary edit/test cycles that make
progress never trip.

Handles two shapes, matching what a stuck agent actually does:
* period 1 — the identical call, `threshold` times in a row;
* period 2 — two distinct calls alternating, `threshold` full cycles.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.conditions.base import Condition, EvalContext, Trip, register
from runbreaker.events import EventType


def _is_periodic(seq: tuple[str, ...], period: int) -> bool:
    return all(seq[i] == seq[i % period] for i in range(len(seq)))


@register
class LoopGuard(Condition):
    id: ClassVar[str] = "repeat_loop"
    phases: ClassVar[frozenset[EventType]] = frozenset({EventType.PRE_TOOL_USE})

    def __init__(self, threshold: int = 5) -> None:
        self.threshold = int(threshold)

    def evaluate(self, ctx: EvalContext) -> Trip | None:
        if self.threshold <= 0:  # 0 disables
            return None
        recent = ctx.recent_tools
        for period in (1, 2):
            window = period * self.threshold
            if len(recent) < window:
                continue
            tail = recent[-window:]
            # A period-2 tail that only holds one distinct call is really period 1,
            # already caught above; requiring `period` distinct values avoids a
            # double count and keeps the reported reason honest.
            if _is_periodic(tail, period) and len(set(tail)) == period:
                shape = "identical call" if period == 1 else "A-B-A-B cycle"
                return Trip(
                    self.id,
                    f"loop detected: {shape} repeated {self.threshold}x "
                    f"({tail[-period:]})",
                )
        return None
