"""Trip when a run has been going for too long in wall-clock time.

Provider-agnostic like `step_budget`: the elapsed time since a session first
appeared needs nothing from the host CLI. Useful as a backstop for an unattended
overnight run that is neither burning steps fast nor reporting tokens — a slow
loop that a step or token budget would take hours to catch.

Evaluated at PreToolUse so the breaker opens *before* the next mutation, and at
Stop so a run that idles past the deadline between turns still surfaces.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.conditions.base import Condition, EvalContext, Trip, register
from runbreaker.events import EventType


@register
class TimeBudget(Condition):
    id: ClassVar[str] = "time_budget"
    phases: ClassVar[frozenset[EventType]] = frozenset({EventType.PRE_TOOL_USE, EventType.STOP})

    def __init__(self, max_minutes: float = 0) -> None:
        self.max_minutes = float(max_minutes)

    @property
    def max_seconds(self) -> float:
        return self.max_minutes * 60

    def evaluate(self, ctx: EvalContext) -> Trip | None:
        if self.max_minutes <= 0:  # 0 disables
            return None
        if ctx.elapsed_seconds <= self.max_seconds:
            return None
        return Trip(
            self.id,
            f"time budget exceeded: {ctx.elapsed_seconds / 60:.1f} min "
            f"> {self.max_minutes:g} min",
        )
