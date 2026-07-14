"""Trip when the provider says we are burning through its rate-limit window.

Codex reports `rate_limits.primary.used_percent` alongside every token count. That
number beats anything we can compute: the provider owns it, it is already
expressed against the plan's real quota, and it survives token-schema churn
untouched.

Providers that do not report it get an abstention, not a false zero.
"""

from __future__ import annotations

from typing import ClassVar

from agentguard.conditions.base import Condition, EvalContext, Trip, register
from agentguard.events import EventType


@register
class RateLimitPressure(Condition):
    id: ClassVar[str] = "rate_limit_pressure"
    phases: ClassVar[frozenset[EventType]] = frozenset({EventType.PRE_TOOL_USE, EventType.STOP})

    def __init__(self, max_percent: float = 80.0) -> None:
        self.max_percent = float(max_percent)

    def evaluate(self, ctx: EvalContext) -> Trip | None:
        used = ctx.rate_limit_percent
        if self.max_percent <= 0 or used is None:
            return None
        if used <= self.max_percent:
            return None
        return Trip(self.id, f"rate-limit pressure: {used:.1f}% used > {self.max_percent:.1f}%")
