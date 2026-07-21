"""Trip when a run's estimated spend crosses a dollar ceiling.

The dollar figure is computed centrally from token usage and the `[cost]` rate table
(`budget.compute_cost`): exact per-model input/output rates where the source split
usage by model (Claude, Codex), and a blended fallback otherwise. This condition owns
only the ceiling.

For backward compatibility, if `[cost]` is not configured a legacy `price_per_mtok`
set on the condition is applied to the token count as a single blended rate.

Either way it inherits token accounting's honesty: unknown usage — or no rate at all —
abstains rather than reading as "$0 spent". The estimate is still approximate (token
ledgers are, and VS Code/Copilot bill in credits, not per-model tokens), so set the
ceiling with margin and treat it as a backstop, not an invoice.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.conditions.base import Condition, EvalContext, Trip, register
from runbreaker.events import EventType


@register
class CostBudget(Condition):
    id: ClassVar[str] = "cost_budget"
    phases: ClassVar[frozenset[EventType]] = frozenset({EventType.PRE_TOOL_USE, EventType.STOP})

    def __init__(self, max_usd: float = 0, price_per_mtok: float = 0) -> None:
        self.max_usd = float(max_usd)
        self.price_per_mtok = float(price_per_mtok)  # legacy blended fallback

    def evaluate(self, ctx: EvalContext) -> Trip | None:
        if self.max_usd <= 0:
            return None
        cost = ctx.cost_usd
        if cost is None and self.price_per_mtok > 0 and ctx.tokens is not None:
            cost = ctx.tokens / 1_000_000 * self.price_per_mtok
        if cost is None or cost <= self.max_usd:
            return None
        return Trip(self.id, f"cost budget exceeded: ~${cost:.2f} > ${self.max_usd:.2f}")
