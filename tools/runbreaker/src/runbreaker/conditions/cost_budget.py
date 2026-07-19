"""Trip when a run's estimated spend crosses a dollar ceiling.

A thin layer over the same token count `token_budget` reads, translated to money
by a single blended price. It inherits `token_budget`'s honesty: unknown usage
abstains rather than reading as "$0 spent".

The estimate is deliberately coarse — one blended `$/1M tokens`, no input/output
split, no per-model rate. Token ledgers are already approximate (see
`token_budget`), so a precise cost model would be false precision. Set the ceiling
with margin and treat it as a backstop, not an invoice.
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
        self.price_per_mtok = float(price_per_mtok)

    def evaluate(self, ctx: EvalContext) -> Trip | None:
        if self.max_usd <= 0 or self.price_per_mtok <= 0 or ctx.tokens is None:
            return None
        cost = ctx.tokens / 1_000_000 * self.price_per_mtok
        if cost <= self.max_usd:
            return None
        return Trip(
            self.id,
            f"cost budget exceeded: ~${cost:.2f} > ${self.max_usd:.2f} "
            f"({ctx.tokens} tokens @ ${self.price_per_mtok:g}/1M)",
        )
