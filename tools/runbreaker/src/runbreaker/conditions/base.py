"""Trip conditions: the extensible half of the breaker.

A condition never blocks anything. It inspects the run and returns a `Trip` or
`None`; opening the breaker is the single enforcement point. That separation is
what lets a new condition be a class rather than a new code path through the
hooks.

`phases` decides when a condition runs. `gate_failures` only makes sense at Stop
— evaluating it before a tool call would open the breaker one step *after* the
red gate that justified it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, TypeVar

from runbreaker.events import EventType, HookEvent


@dataclass(frozen=True)
class Trip:
    condition: str
    reason: str


@dataclass(frozen=True)
class EvalContext:
    event: HookEvent
    steps: int
    tokens: int | None
    rate_limit_percent: float | None
    consecutive_fails: int
    elapsed_seconds: float = 0.0


class Condition(ABC):
    id: ClassVar[str]
    phases: ClassVar[frozenset[EventType]]

    @abstractmethod
    def evaluate(self, ctx: EvalContext) -> Trip | None:
        """Return a Trip to open the breaker, or None to abstain."""


REGISTRY: dict[str, type[Condition]] = {}

C = TypeVar("C", bound=type[Condition])


def register(cls: C) -> C:
    REGISTRY[cls.id] = cls
    return cls


def build(spec: Mapping[str, Any]) -> Condition | None:
    """Instantiate one condition from a config table. Returns None if unusable."""
    params = dict(spec)
    cond_id = params.pop("id", None)
    cls = REGISTRY.get(str(cond_id))
    if cls is None:
        return None
    try:
        return cls(**params)
    except TypeError:
        return None


def build_all(specs: Iterable[Mapping[str, Any]]) -> tuple[list[Condition], list[str]]:
    """Build every configured condition, reporting the ids we could not build."""
    built: list[Condition] = []
    rejected: list[str] = []
    for spec in specs:
        condition = build(spec)
        if condition is None:
            rejected.append(str(spec.get("id", "<missing id>")))
        else:
            built.append(condition)
    return built, rejected


def evaluate(conditions: Sequence[Condition], ctx: EvalContext) -> Trip | None:
    """First condition whose phase matches and which trips, wins."""
    for condition in conditions:
        if ctx.event.event not in condition.phases:
            continue
        trip = condition.evaluate(ctx)
        if trip is not None:
            return trip
    return None
