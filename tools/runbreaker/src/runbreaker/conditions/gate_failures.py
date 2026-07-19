"""Trip after N consecutive red quality gates.

Evaluated at Stop, right after the gate records its failure, so the breaker opens
in the same turn that earned it.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.conditions.base import Condition, EvalContext, Trip, register
from runbreaker.events import EventType


@register
class GateFailures(Condition):
    id: ClassVar[str] = "gate_failures"
    phases: ClassVar[frozenset[EventType]] = frozenset({EventType.STOP})

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = int(threshold)

    def evaluate(self, ctx: EvalContext) -> Trip | None:
        if self.threshold <= 0 or ctx.consecutive_fails < self.threshold:
            return None
        return Trip(self.id, f"{ctx.consecutive_fails} consecutive failed quality gates")
