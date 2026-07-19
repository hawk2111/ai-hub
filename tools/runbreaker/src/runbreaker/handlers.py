"""Provider-agnostic hook logic.

The prototype registered two separate PreToolUse hooks — one metering the budget,
one enforcing the breaker — which spawned two interpreters per tool call. They are
one handler here, registered once with a `*` matcher: the budget must count reads
too, and only this code needs to know whether the current tool mutates files.

Enforcement lives in exactly one place. Conditions decide *whether* to open the
breaker; the open breaker is what actually denies anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from runbreaker import audit, gate
from runbreaker.breaker import Breaker
from runbreaker.budget import Budget, SessionBudget
from runbreaker.conditions import Condition, EvalContext, Trip, build_all, evaluate, load_custom
from runbreaker.config import Config
from runbreaker.events import ALLOW, NOOP, Decision, EventType, HookEvent, block_stop, deny
from runbreaker.state import Store
from runbreaker.tokens import get_source


@dataclass(frozen=True)
class Runtime:
    config: Config
    breaker: Breaker
    budget: Budget
    conditions: tuple[Condition, ...]


def build_runtime(config: Config) -> Runtime:
    store = Store(config.state_dir)
    load_custom(config.conditions_dir)
    conditions, rejected = build_all(config.conditions)
    if rejected:
        audit.record(
            config.audit_path,
            event="config",
            decision="ignored",
            reason=f"unknown or misconfigured conditions: {', '.join(rejected)}",
        )
    return Runtime(config, Breaker(store), Budget(store), tuple(conditions))


def route(event: HookEvent, rt: Runtime) -> Decision:
    if event.event is EventType.PRE_TOOL_USE:
        return pre_tool_use(event, rt)
    if event.event is EventType.STOP:
        return stop(event, rt)
    return NOOP


def _eval_context(event: HookEvent, snapshot: SessionBudget, fails: int) -> EvalContext:
    """The one place a budget snapshot becomes a condition's view of the run."""
    return EvalContext(
        event=event,
        steps=snapshot.steps,
        tokens=snapshot.tokens,
        rate_limit_percent=snapshot.rate_limit_percent,
        elapsed_seconds=snapshot.elapsed_seconds,
        consecutive_fails=fails,
    )


def pre_tool_use(event: HookEvent, rt: Runtime) -> Decision:
    status = rt.breaker.status()
    if status.is_open:
        return _deny_write(event, rt, status.reason)

    if rt.config.skip_budget:
        return ALLOW

    source = get_source(rt.config.budget.token_source, event.provider)
    snapshot = rt.budget.tick(
        event,
        source,
        recompute_every=rt.config.budget.recompute_every,
        gc_days=rt.config.gc_days,
        max_sessions=rt.config.max_sessions,
        audit_path=rt.config.audit_path,
    )

    trip = evaluate(rt.conditions, _eval_context(event, snapshot, status.fails))
    if trip is None:
        return ALLOW

    detail: dict[str, object] = {"steps": snapshot.steps, "tokens": snapshot.tokens}
    _trip_or_warn(event, rt, trip, detail)
    if rt.config.warn_only:
        return ALLOW
    return _deny_write(event, rt, trip.reason)


def stop(event: HookEvent, rt: Runtime) -> Decision:
    # Claude sets this when we are already inside a gate-triggered continuation.
    if event.stop_hook_active:
        return NOOP

    # An open breaker means the run is read-only and waiting for a human. Blocking
    # the stop now would trap the agent in a loop it has no way to escape.
    status = rt.breaker.status()
    if status.is_open:
        return NOOP

    snapshot = _refresh_budget(event, rt)
    trip = evaluate(rt.conditions, _eval_context(event, snapshot, status.fails))
    if trip is not None:
        _trip_or_warn(event, rt, trip)
        return NOOP

    if rt.config.skip_gate or not rt.config.gate.enabled:
        rt.breaker.record_success()
        return NOOP

    if not gate.should_run(rt.config.gate, rt.config.project):
        # A clean turn clears the counter even when it touched nothing we watch,
        # so stale failures cannot poison an unrelated later turn.
        rt.breaker.record_success()
        return NOOP

    failures = gate.run(rt.config.gate, rt.config.project)
    if not failures:
        rt.breaker.record_success()
        rt.budget.clear_stop_blocks(event.session_id)
        return NOOP

    return _handle_red_gate(event, rt, failures)


def _handle_red_gate(event: HookEvent, rt: Runtime, failures: list[gate.Failure]) -> Decision:
    # A check we could not even launch is a broken config, not broken code. Surface
    # it, but never let it march the breaker toward its threshold — and never bounce
    # the agent back to "fix" code that is not broken.
    for failure in (f for f in failures if f.config_error):
        audit.record(
            rt.config.audit_path,
            event="gate",
            decision="config_error",
            provider=event.provider,
            session_id=event.session_id,
            reason=f"{failure.name}: {failure.output}",
        )

    counted = [f for f in failures if not f.config_error]
    if not counted:
        rt.breaker.record_success()
        return NOOP

    recording = {c.name for c in rt.config.gate.checks if c.fail_records_breaker}
    fails = (
        rt.breaker.record_fail()
        if any(f.name in recording for f in counted)
        else rt.breaker.status().fails
    )

    snapshot = rt.budget.status(event.session_id)
    trip = evaluate(rt.conditions, _eval_context(event, snapshot, fails))

    message = gate.report(counted)
    if trip is not None:
        # The agent is about to stop for good, so this failure text is the last
        # anyone will see of *why*. Keep it with the trip rather than dropping it.
        _trip_or_warn(event, rt, trip, {"fails": fails, "gate": message})
        return NOOP  # read-only now; let it stop and surface to a human

    if rt.config.warn_only:
        # Observe the red gate, but never trap the agent in a fix-me loop.
        audit.record(
            rt.config.audit_path,
            event="gate",
            decision="warn",
            provider=event.provider,
            session_id=event.session_id,
            reason="gate red (warn-only: stop not blocked)",
            detail={"gate": message},
        )
        return NOOP

    blocks = rt.budget.bump_stop_blocks(event.session_id)
    if blocks > rt.config.gate.max_stop_blocks:
        audit.record(
            rt.config.audit_path,
            event="gate",
            decision="give_up",
            provider=event.provider,
            session_id=event.session_id,
            reason=f"gate still red after {blocks - 1} blocked stops",
            detail={"gate": message},
        )
        return NOOP

    return block_stop(message)


def _refresh_budget(event: HookEvent, rt: Runtime) -> SessionBudget:
    if rt.config.skip_budget:
        return rt.budget.status(event.session_id)
    source = get_source(rt.config.budget.token_source, event.provider)
    return rt.budget.refresh(event, source, rt.config.audit_path)


def _trip_or_warn(
    event: HookEvent, rt: Runtime, trip: Trip, detail: dict[str, object] | None = None
) -> None:
    """Open the breaker, or — in warn mode — only record what it would have done."""
    if rt.config.warn_only:
        _warn(event, rt, trip, detail)
    else:
        _open(event, rt, trip, detail)


def _open(
    event: HookEvent, rt: Runtime, trip: Trip, detail: dict[str, object] | None = None
) -> None:
    rt.breaker.trip(trip.reason)
    audit.record(
        rt.config.audit_path,
        event="trip",
        decision="open",
        provider=event.provider,
        session_id=event.session_id,
        tool=event.tool_name or "",
        reason=trip.reason,
        detail={"condition": trip.condition, **(detail or {})},
    )


def _warn(
    event: HookEvent, rt: Runtime, trip: Trip, detail: dict[str, object] | None = None
) -> None:
    audit.record(
        rt.config.audit_path,
        event="trip",
        decision="warn",
        provider=event.provider,
        session_id=event.session_id,
        tool=event.tool_name or "",
        reason=trip.reason,
        detail={"condition": trip.condition, **(detail or {})},
    )


def _deny_write(event: HookEvent, rt: Runtime, reason: str) -> Decision:
    """Deny only mutations. Reads, tests and `runbreaker reset` stay reachable."""
    if not event.is_write:
        return ALLOW
    audit.record(
        rt.config.audit_path,
        event="blocked",
        decision="deny",
        provider=event.provider,
        session_id=event.session_id,
        tool=event.tool_name or "",
        reason=reason,
        detail={"file_path": event.tool_input.get("file_path", "")},
    )
    return deny(
        f"[runbreaker] BLOCKED: circuit breaker is OPEN ({reason}). "
        "The run is read-only until a human investigates. "
        "Reset with: runbreaker reset"
    )
