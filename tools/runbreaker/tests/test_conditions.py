from __future__ import annotations

import textwrap

from tests.conftest import make_event

from runbreaker.conditions import (
    CostBudget,
    GateFailures,
    LoopGuard,
    RateLimitPressure,
    StepBudget,
    TimeBudget,
    TokenBudget,
    build,
    build_all,
    evaluate,
    load_custom,
)
from runbreaker.events import EventType


def ctx(**kwargs):
    from runbreaker.conditions import EvalContext

    defaults = {
        "event": make_event(),
        "steps": 0,
        "tokens": None,
        "rate_limit_percent": None,
        "consecutive_fails": 0,
        "elapsed_seconds": 0.0,
    }
    return EvalContext(**{**defaults, **kwargs})


# -- step_budget ------------------------------------------------------------


def test_step_budget_boundary_is_strictly_greater():
    cond = StepBudget(max_steps=10)
    assert cond.evaluate(ctx(steps=10)) is None
    assert cond.evaluate(ctx(steps=11)) is not None


def test_step_budget_zero_disables():
    assert StepBudget(max_steps=0).evaluate(ctx(steps=10_000)) is None


# -- token_budget -----------------------------------------------------------


def test_token_budget_abstains_on_unknown_tokens():
    """The whole point: a schema drift must not read as zero consumption."""
    assert TokenBudget(max_tokens=100).evaluate(ctx(tokens=None)) is None


def test_token_budget_trips_at_the_fraction_not_the_limit():
    cond = TokenBudget(max_tokens=1000, trip_at_fraction=0.9)
    assert cond.evaluate(ctx(tokens=900)) is None
    trip = cond.evaluate(ctx(tokens=901))
    assert trip is not None and "900" in trip.reason


def test_token_budget_disabled_without_a_limit():
    assert TokenBudget().evaluate(ctx(tokens=10**9)) is None


# -- time_budget ------------------------------------------------------------


def test_time_budget_boundary_is_strictly_greater():
    cond = TimeBudget(max_minutes=10)
    assert cond.evaluate(ctx(elapsed_seconds=600)) is None
    assert cond.evaluate(ctx(elapsed_seconds=601)) is not None


def test_time_budget_zero_disables():
    assert TimeBudget(max_minutes=0).evaluate(ctx(elapsed_seconds=10**6)) is None


def test_time_budget_runs_at_pre_and_stop():
    assert TimeBudget.phases == frozenset({EventType.PRE_TOOL_USE, EventType.STOP})


# -- cost_budget ------------------------------------------------------------


def test_cost_budget_abstains_on_unknown_tokens():
    assert CostBudget(max_usd=10, price_per_mtok=15).evaluate(ctx(tokens=None)) is None


def test_cost_budget_disabled_without_a_price_or_ceiling():
    assert CostBudget(max_usd=10).evaluate(ctx(tokens=10**9)) is None
    assert CostBudget(price_per_mtok=15).evaluate(ctx(tokens=10**9)) is None


def test_cost_budget_trips_when_estimated_spend_exceeds_the_ceiling():
    # Legacy blended fallback: 1M tokens @ $15/1M = $15 against a $10 ceiling.
    cond = CostBudget(max_usd=10, price_per_mtok=15)
    assert cond.evaluate(ctx(tokens=600_000)) is None  # $9 < $10
    trip = cond.evaluate(ctx(tokens=1_000_000))
    assert trip is not None and "15.00" in trip.reason


def test_cost_budget_uses_the_central_cost_when_present():
    """The computed per-model cost (ctx.cost_usd) wins over the legacy blended path."""
    cond = CostBudget(max_usd=10)
    assert cond.evaluate(ctx(cost_usd=9.0)) is None
    trip = cond.evaluate(ctx(cost_usd=11.5))
    assert trip is not None and "11.50" in trip.reason


# -- repeat_loop ------------------------------------------------------------


def test_loop_guard_trips_on_identical_calls_in_a_row():
    cond = LoopGuard(threshold=3)
    assert cond.evaluate(ctx(recent_tools=("A", "A"))) is None
    assert cond.evaluate(ctx(recent_tools=("A", "A", "A"))) is not None


def test_loop_guard_ignores_progress():
    """Distinct calls are progress, not a loop — even many of them."""
    cond = LoopGuard(threshold=3)
    assert cond.evaluate(ctx(recent_tools=("A", "B", "C", "D", "E"))) is None


def test_loop_guard_trips_on_an_a_b_a_b_cycle():
    cond = LoopGuard(threshold=3)
    # Two full A-B cycles is not enough; three (six calls) is.
    assert cond.evaluate(ctx(recent_tools=("A", "B", "A", "B"))) is None
    trip = cond.evaluate(ctx(recent_tools=("A", "B", "A", "B", "A", "B")))
    assert trip is not None and "A-B-A-B" in trip.reason


def test_loop_guard_only_looks_at_the_tail():
    """Earlier progress does not excuse a loop that starts later."""
    cond = LoopGuard(threshold=2)
    assert cond.evaluate(ctx(recent_tools=("X", "Y", "Z", "A", "A"))) is not None


def test_loop_guard_zero_disables():
    assert LoopGuard(threshold=0).evaluate(ctx(recent_tools=("A",) * 100)) is None


def test_loop_guard_only_runs_pre_tool_use():
    assert LoopGuard.phases == frozenset({EventType.PRE_TOOL_USE})


# -- gate_failures ----------------------------------------------------------


def test_gate_failures_trips_exactly_at_the_threshold():
    cond = GateFailures(threshold=3)
    assert cond.evaluate(ctx(consecutive_fails=2)) is None
    assert cond.evaluate(ctx(consecutive_fails=3)) is not None


def test_gate_failures_only_runs_at_stop():
    assert GateFailures.phases == frozenset({EventType.STOP})


# -- rate_limit_pressure ----------------------------------------------------


def test_rate_limit_abstains_when_the_provider_does_not_report_it():
    assert RateLimitPressure(max_percent=80).evaluate(ctx(rate_limit_percent=None)) is None


def test_rate_limit_trips_above_the_ceiling():
    cond = RateLimitPressure(max_percent=80)
    assert cond.evaluate(ctx(rate_limit_percent=80.0)) is None
    assert cond.evaluate(ctx(rate_limit_percent=80.1)) is not None


# -- registry / evaluation --------------------------------------------------


def test_evaluate_skips_conditions_outside_the_current_phase():
    conditions = [GateFailures(threshold=1)]
    pre = ctx(event=make_event(event=EventType.PRE_TOOL_USE), consecutive_fails=5)
    stop = ctx(event=make_event(event=EventType.STOP), consecutive_fails=5)
    assert evaluate(conditions, pre) is None
    assert evaluate(conditions, stop) is not None


def test_build_rejects_unknown_ids_and_bad_params():
    assert build({"id": "nope"}) is None
    assert build({"id": "step_budget", "bogus_param": 1}) is None


def test_build_all_reports_what_it_could_not_build():
    built, rejected = build_all([{"id": "step_budget"}, {"id": "nope"}])
    assert len(built) == 1
    assert rejected == ["nope"]


def test_custom_condition_loads_from_a_directory(tmp_path):
    (tmp_path / "wall_clock.py").write_text(
        textwrap.dedent(
            '''
            from runbreaker.conditions import Condition, Trip, register
            from runbreaker.events import EventType

            @register
            class WallClock(Condition):
                id = "wall_clock"
                phases = frozenset({EventType.PRE_TOOL_USE})

                def __init__(self, max_steps=1):
                    self.max_steps = max_steps

                def evaluate(self, ctx):
                    return Trip(self.id, "too long") if ctx.steps > self.max_steps else None
            '''
        ),
        encoding="utf-8",
    )
    assert "wall_clock" in load_custom(tmp_path)

    cond = build({"id": "wall_clock", "max_steps": 2})
    assert cond is not None
    assert cond.evaluate(ctx(steps=3)) is not None


def test_a_broken_custom_condition_does_not_take_the_hook_down(tmp_path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')", encoding="utf-8")
    assert load_custom(tmp_path) == []
