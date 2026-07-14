"""Trip when a run exceeds its tool-call budget.

The only condition that works unconditionally on all three providers: counting
hook invocations needs nothing from the host CLI. Where tokens are unreadable
(Copilot), this is the whole safety net.
"""

from __future__ import annotations

from typing import ClassVar

from agentguard.conditions.base import Condition, EvalContext, Trip, register
from agentguard.events import EventType


@register
class StepBudget(Condition):
    id: ClassVar[str] = "step_budget"
    phases: ClassVar[frozenset[EventType]] = frozenset({EventType.PRE_TOOL_USE})

    def __init__(self, max_steps: int = 250) -> None:
        self.max_steps = int(max_steps)

    def evaluate(self, ctx: EvalContext) -> Trip | None:
        if self.max_steps <= 0:  # 0 disables
            return None
        if ctx.steps <= self.max_steps:
            return None
        return Trip(self.id, f"step budget exceeded: {ctx.steps}/{self.max_steps} tool calls")
