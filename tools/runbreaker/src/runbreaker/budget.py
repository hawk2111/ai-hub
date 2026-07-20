"""Per-session accounting: steps, tokens, wall-clock start, and recent tool-call
fingerprints.

Keyed by session id, so each fresh agent run starts at zero. The ledger owns the
token-read throttle, the resolved-path cache, and the recent-call tail (which feeds
loop detection), because all of them need the same exclusive lock as the counter
itself.

A `None` reading never erases a number we already had: "we cannot tell right now"
is weaker evidence than "we measured 2.6M tokens two steps ago".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runbreaker import audit
from runbreaker.config import CostConfig
from runbreaker.events import HookEvent
from runbreaker.state import Store
from runbreaker.tokens import TokenSource
from runbreaker.tokens.base import ProviderUsage, as_float, as_int

NAME = "budget"


def compute_cost(usage: ProviderUsage, cost: CostConfig) -> float | None:
    """Dollar cost of a usage reading, or None when cost is not configured / unknown.

    Per-model input and output rates are exact where the source split usage by model
    (Claude, Codex); a model without a configured rate — and any source that reports
    only a grand total — falls back to the blended `default_per_mtok`.
    """
    if not cost.enabled:
        return None
    if usage.by_model:
        total = 0.0
        for m in usage.by_model:
            rate = cost.models.get(m.model)
            r_in = rate.input if rate else cost.default_per_mtok
            r_out = rate.output if rate else cost.default_per_mtok
            total += m.input_tokens / 1_000_000 * r_in + m.output_tokens / 1_000_000 * r_out
        return total
    if usage.total_tokens is not None and cost.default_per_mtok > 0:
        return usage.total_tokens / 1_000_000 * cost.default_per_mtok
    return None

DEFAULT_GC_DAYS = 7
DEFAULT_MAX_SESSIONS = 50

#: How many recent tool-call fingerprints to keep per session for loop detection.
#: Bounded so the ledger stays small; large enough for a generous repeat threshold.
RECENT_MAX = 64


def _empty() -> dict[str, Any]:
    return {"sessions": {}}


def _new_session() -> dict[str, Any]:
    return {
        "steps": 0,
        "tokens": None,
        "rate_limit_percent": None,
        "started_at": time.time(),
        "cache": {},
        "recent": [],
    }


@dataclass(frozen=True)
class SessionBudget:
    steps: int
    tokens: int | None = None
    rate_limit_percent: float | None = None
    elapsed_seconds: float = 0.0
    #: Recent tool-call fingerprints, oldest first. Feeds the loop guard.
    recent_tools: tuple[str, ...] = ()
    #: Estimated USD spend since the last reset. Feeds cost_budget.
    cost_usd: float | None = None


def _snapshot(session: dict[str, Any]) -> SessionBudget:
    # Reuse the same numeric guards the token sources use: both reject bool, so a
    # JSON `true` in a persisted field degrades to "unknown" instead of 1.
    started = as_float(session.get("started_at")) or 0.0
    recent = session.get("recent")
    # Tokens and cost are reported relative to the last reset. The provider's transcript
    # is cumulative and a reset cannot rewind it, so we subtract the reading captured at
    # reset time — otherwise the budget would re-trip on the first call after a reset.
    absolute = as_int(session.get("tokens"))
    baseline = as_int(session.get("tokens_baseline")) or 0
    cost = as_float(session.get("cost"))
    cost_baseline = as_float(session.get("cost_baseline")) or 0.0
    return SessionBudget(
        steps=int(session.get("steps", 0)),
        tokens=max(0, absolute - baseline) if absolute is not None else None,
        rate_limit_percent=as_float(session.get("rate_limit_percent")),
        elapsed_seconds=max(0.0, time.time() - started) if started else 0.0,
        recent_tools=tuple(str(f) for f in recent) if isinstance(recent, list) else (),
        cost_usd=max(0.0, cost - cost_baseline) if cost is not None else None,
    )


class Budget:
    def __init__(self, store: Store) -> None:
        self._store = store

    def tick(
        self,
        event: HookEvent,
        source: TokenSource,
        *,
        recompute_every: int = 5,
        gc_days: int = DEFAULT_GC_DAYS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        audit_path: Path | None = None,
        cost: CostConfig | None = None,
    ) -> SessionBudget:
        """Count one tool call, refreshing token usage every Nth step."""
        with self._store.update(NAME, _empty) as draft:
            if draft.corrupt:
                # The ledger only ever feeds the breaker; losing it costs us an
                # undercount, not safety. Start clean rather than fail closed.
                draft.data = _empty()

            sessions: dict[str, Any] = draft.data.setdefault("sessions", {})
            session = sessions.setdefault(event.session_id, _new_session())
            session["steps"] = int(session.get("steps", 0)) + 1

            if event.tool_name:
                recent = session.setdefault("recent", [])
                recent.append(event.fingerprint())
                del recent[:-RECENT_MAX]  # keep only the tail

            if recompute_every > 0 and (session["steps"] - 1) % recompute_every == 0:
                self._refresh(session, event, source, audit_path, cost)

            session["updated_at"] = time.time()
            _collect(sessions, gc_days=gc_days, max_sessions=max_sessions)
            return _snapshot(session)

    def refresh(
        self,
        event: HookEvent,
        source: TokenSource,
        audit_path: Path | None = None,
        cost: CostConfig | None = None,
    ) -> SessionBudget:
        """Re-read token usage without counting a step.

        Called at Stop, where the token and rate-limit conditions would otherwise see
        whatever the throttled PreToolUse refresh last wrote. It is also the only
        place Copilot ever learns its `transcript_path`, which arrives on the Stop
        payload and nowhere else.
        """
        with self._store.update(NAME, _empty) as draft:
            if draft.corrupt:
                draft.data = _empty()
            sessions: dict[str, Any] = draft.data.setdefault("sessions", {})
            session = sessions.setdefault(event.session_id, _new_session())
            self._refresh(session, event, source, audit_path, cost)
            session["updated_at"] = time.time()
            return _snapshot(session)

    def status(self, session_id: str) -> SessionBudget:
        draft = self._store.read(NAME, _empty)
        sessions = draft.data.get("sessions", {}) if not draft.corrupt else {}
        return _snapshot(sessions.get(session_id, _new_session()))

    def all_sessions(self) -> dict[str, Any]:
        draft = self._store.read(NAME, _empty)
        return {} if draft.corrupt else dict(draft.data.get("sessions", {}))

    def reset(self, session_id: str | None = None) -> None:
        """Restart the run budgets. Steps, the loop tail and the clock genuinely zero;
        cumulative tokens rebase to the current reading (see `_rebase`)."""
        with self._store.update(NAME, _empty) as draft:
            if draft.corrupt:
                draft.data = _empty()
                return
            sessions: dict[str, Any] = draft.data.get("sessions", {})
            targets = (
                list(sessions.values())
                if session_id is None
                else [s for s in [sessions.get(session_id)] if s]
            )
            if not targets:
                draft.write = False
                return
            for session in targets:
                _rebase(session)

    def bump_stop_blocks(self, session_id: str) -> int:
        """Count how many times we have blocked this session from finishing.

        Only Claude reports `stop_hook_active`. Everywhere else this counter is
        what keeps a red gate from bouncing the agent forever.
        """
        with self._store.update(NAME, _empty) as draft:
            if draft.corrupt:
                draft.data = _empty()
            sessions: dict[str, Any] = draft.data.setdefault("sessions", {})
            session = sessions.setdefault(session_id, _new_session())
            session["stop_blocks"] = int(session.get("stop_blocks", 0)) + 1
            return int(session["stop_blocks"])

    def clear_stop_blocks(self, session_id: str) -> None:
        with self._store.update(NAME, _empty) as draft:
            sessions = draft.data.get("sessions", {}) if not draft.corrupt else {}
            session = sessions.get(session_id)
            if not session or not session.get("stop_blocks"):
                draft.write = False
                return
            session["stop_blocks"] = 0

    def collect(
        self, *, gc_days: int = DEFAULT_GC_DAYS, max_sessions: int = DEFAULT_MAX_SESSIONS
    ) -> int:
        with self._store.update(NAME, _empty) as draft:
            if draft.corrupt:
                draft.data = _empty()
                return 0
            sessions = draft.data.setdefault("sessions", {})
            before = len(sessions)
            _collect(sessions, gc_days=gc_days, max_sessions=max_sessions)
            return before - len(sessions)

    @staticmethod
    def _refresh(
        session: dict[str, Any],
        event: HookEvent,
        source: TokenSource,
        audit_path: Path | None = None,
        cost: CostConfig | None = None,
    ) -> None:
        cache = session.setdefault("cache", {})
        try:
            usage = source.read(event, cache)
        except Exception as exc:  # a token source must never break a tool call
            # A safety device that silently stopped measuring is worse than a loud
            # one: without this, a buggy custom TokenSource would disable the token
            # budget with no trace. Surface it once per session, then stay quiet.
            if audit_path is not None and not session.get("token_error_logged"):
                session["token_error_logged"] = True
                audit.record(
                    audit_path,
                    event="token_source",
                    decision="error",
                    provider=event.provider,
                    session_id=event.session_id,
                    reason=f"{source.id}: {type(exc).__name__}: {exc}",
                )
            return
        if usage.total_tokens is not None:
            session["tokens"] = usage.total_tokens
        if usage.rate_limit_percent is not None:
            session["rate_limit_percent"] = usage.rate_limit_percent
        if cost is not None:
            cost_value = compute_cost(usage, cost)
            if cost_value is not None:
                session["cost"] = cost_value


def _rebase(session: dict[str, Any]) -> None:
    """Restart a session's run budgets in place.

    Steps, the loop tail, the stop-block guard and the clock reset to zero. Tokens
    are the exception: the provider's transcript still holds every token this session
    spent, so zeroing here and re-reading would just recompute the same total and
    re-open the breaker on the next call. Instead we record the current reading as a
    baseline that the snapshot subtracts, so the budget now counts usage *since the
    reset*. The token-source cursor in `cache` is deliberately left intact.
    """
    session["steps"] = 0
    session["recent"] = []
    session["stop_blocks"] = 0
    session["started_at"] = time.time()
    tokens = as_int(session.get("tokens"))
    if tokens is not None:
        session["tokens_baseline"] = tokens
    cost = as_float(session.get("cost"))
    if cost is not None:
        session["cost_baseline"] = cost


def _collect(sessions: dict[str, Any], *, gc_days: int, max_sessions: int) -> None:
    """Drop sessions that are too old, then the oldest beyond `max_sessions`."""
    if gc_days > 0:
        cutoff = time.time() - gc_days * 86400
        stale = [
            sid
            for sid, s in sessions.items()
            if float(s.get("updated_at") or s.get("started_at") or 0) < cutoff
        ]
        for sid in stale:
            del sessions[sid]

    if 0 < max_sessions < len(sessions):
        ordered = sorted(
            sessions.items(),
            key=lambda kv: float(kv[1].get("updated_at") or kv[1].get("started_at") or 0),
        )
        for sid, _ in ordered[: len(sessions) - max_sessions]:
            del sessions[sid]
