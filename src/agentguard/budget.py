"""Per-session step and token accounting.

Keyed by session id, so each fresh agent run starts at zero. The ledger owns the
token-read throttle and the resolved-path cache, because both need the same
exclusive lock as the counter itself.

A `None` reading never erases a number we already had: "we cannot tell right now"
is weaker evidence than "we measured 2.6M tokens two steps ago".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from agentguard.events import HookEvent
from agentguard.state import Store
from agentguard.tokens import TokenSource

NAME = "budget"

DEFAULT_GC_DAYS = 7
DEFAULT_MAX_SESSIONS = 50


def _empty() -> dict[str, Any]:
    return {"sessions": {}}


def _new_session() -> dict[str, Any]:
    return {
        "steps": 0,
        "tokens": None,
        "rate_limit_percent": None,
        "started_at": time.time(),
        "cache": {},
    }


@dataclass(frozen=True)
class SessionBudget:
    steps: int
    tokens: int | None = None
    rate_limit_percent: float | None = None


def _snapshot(session: dict[str, Any]) -> SessionBudget:
    tokens = session.get("tokens")
    rate = session.get("rate_limit_percent")
    return SessionBudget(
        steps=int(session.get("steps", 0)),
        tokens=int(tokens) if isinstance(tokens, int) else None,
        rate_limit_percent=float(rate) if isinstance(rate, int | float) else None,
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

            if recompute_every > 0 and (session["steps"] - 1) % recompute_every == 0:
                self._refresh(session, event, source)

            session["updated_at"] = time.time()
            _collect(sessions, gc_days=gc_days, max_sessions=max_sessions)
            return _snapshot(session)

    def refresh(self, event: HookEvent, source: TokenSource) -> SessionBudget:
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
            self._refresh(session, event, source)
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
        with self._store.update(NAME, _empty) as draft:
            if session_id is None or draft.corrupt:
                draft.data = _empty()
                return
            draft.data.get("sessions", {}).pop(session_id, None)

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
    def _refresh(session: dict[str, Any], event: HookEvent, source: TokenSource) -> None:
        cache = session.setdefault("cache", {})
        try:
            usage = source.read(event, cache)
        except Exception:  # a token source must never break a tool call
            return
        if usage.total_tokens is not None:
            session["tokens"] = usage.total_tokens
        if usage.rate_limit_percent is not None:
            session["rate_limit_percent"] = usage.rate_limit_percent


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
