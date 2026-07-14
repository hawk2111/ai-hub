"""Per-session run budget accounting (steps + tokens).

Makes Constitution Principle VII's "bounded by explicit step/cost limits" real:
budget-guard ticks this on every tool call and trips the Phase-5 circuit breaker
when a limit is exceeded, so the run degrades to read-only instead of looping or
burning budget. State lives in .claude/state/budget.json (git-ignored), keyed by
session id, so each `/ship` (a fresh `claude -p` session) starts at zero.

Best-effort: swallows its own errors so accounting never wedges a tool call.
"""
import json
import os
import time

STATE_DIR = os.path.join(
    os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()), ".claude", "state"
)
BUDGET = os.path.join(STATE_DIR, "budget.json")


def _load() -> dict:
    try:
        with open(BUDGET, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(BUDGET, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


def tick(session: str, tokens: int | None = None) -> tuple[int, int]:
    """Count one step for the session; optionally update the token total.
    Returns (steps, tokens)."""
    data = _load()
    s = data.get(session) or {"steps": 0, "tokens": 0,
                              "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    s["steps"] = int(s.get("steps", 0)) + 1
    if tokens is not None:
        s["tokens"] = int(tokens)
    data[session] = s
    _save(data)
    return s["steps"], int(s.get("tokens", 0))


def status(session: str) -> dict:
    return _load().get(session, {"steps": 0, "tokens": 0})


def reset(session: str | None = None) -> None:
    """Clear one session's counters, or all of them when session is None."""
    if session is None:
        _save({})
        return
    data = _load()
    if data.pop(session, None) is not None:
        _save(data)
