"""Trip when a run exceeds its token budget — but only when we actually know.

Two deliberate choices:

* Unknown usage abstains. A renamed field in a provider's session file must not
  read as "zero tokens consumed"; that was the prototype's silent fail-open.
* We trip at a fraction of the limit. Every provider's token ledger is
  approximate (Claude can drop the final `message_stop`; Codex has had
  attribution bugs), so an exact cutoff is theatre. Trip early and leave margin.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.conditions.base import Condition, EvalContext, Trip, register
from runbreaker.events import EventType


@register
class TokenBudget(Condition):
    id: ClassVar[str] = "token_budget"
    phases: ClassVar[frozenset[EventType]] = frozenset({EventType.PRE_TOOL_USE, EventType.STOP})

    def __init__(self, max_tokens: int = 0, trip_at_fraction: float = 0.9) -> None:
        self.max_tokens = int(max_tokens)
        self.trip_at_fraction = float(trip_at_fraction)

    @property
    def threshold(self) -> int:
        return int(self.max_tokens * self.trip_at_fraction)

    def evaluate(self, ctx: EvalContext) -> Trip | None:
        if self.max_tokens <= 0 or ctx.tokens is None:
            return None
        if ctx.tokens <= self.threshold:
            return None
        return Trip(
            self.id,
            f"token budget exceeded: {ctx.tokens} tokens > {self.threshold} "
            f"({self.trip_at_fraction:.0%} of {self.max_tokens})",
        )
