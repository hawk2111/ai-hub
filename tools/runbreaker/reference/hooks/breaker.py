"""Circuit-breaker state for unattended agent runs.

When the breaker is OPEN, the pre-breaker-guard blocks file mutations
(Write/Edit/MultiEdit) so the agent drops to read-only and must surface to a
human instead of looping on a broken task or burning budget. Read-only tools and
`tools/agent/breaker.sh` stay available, so there is no deadlock — a human (or
the launcher) resets the breaker to close it.

State file: .claude/state/breaker.json (git-ignored). Closing an open breaker is
a deliberate manual/launcher action; a green gate does NOT auto-close it.
"""
import json
import os
import time

STATE_DIR = os.path.join(
    os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()), ".claude", "state"
)
STATE = os.path.join(STATE_DIR, "breaker.json")
THRESHOLD = int(os.environ.get("KAI_BREAKER_THRESHOLD", "3"))

_CLOSED = {"state": "closed", "fails": 0, "reason": "", "tripped_at": ""}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _load() -> dict:
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return dict(_CLOSED)


def _save(data: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


def status() -> dict:
    return _load()


def is_open() -> bool:
    return _load().get("state") == "open"


def trip(reason: str) -> dict:
    d = _load()
    d.update(state="open", reason=reason or "manual trip", tripped_at=_now())
    _save(d)
    return d


def reset() -> dict:
    _save(dict(_CLOSED))
    return dict(_CLOSED)


def record_fail() -> dict:
    """Count a failed quality gate; trip when the threshold is reached."""
    d = _load()
    if d.get("state") == "open":
        return d
    d["fails"] = int(d.get("fails", 0)) + 1
    if d["fails"] >= THRESHOLD:
        d["state"] = "open"
        d["reason"] = f"auto: {d['fails']} consecutive failed quality gates"
        d["tripped_at"] = _now()
    _save(d)
    return d


def record_success() -> None:
    """Clear the consecutive-fail counter. Does NOT close an open breaker."""
    d = _load()
    if d.get("state") == "open":
        return
    if d.get("fails"):
        d["fails"] = 0
        _save(d)
