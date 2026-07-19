"""Budget ledger edge cases introduced alongside the wall-clock and cost budgets."""

from __future__ import annotations

import time

from tests.conftest import make_event

from runbreaker import audit
from runbreaker.budget import Budget, SessionBudget, _snapshot
from runbreaker.state import Store
from runbreaker.tokens.base import ProviderUsage, TokenSource


class BoomSource(TokenSource):
    id = "boom"

    def read(self, event, cache):  # type: ignore[override]
        raise RuntimeError("kaboom")


class GoodSource(TokenSource):
    id = "good"

    def read(self, event, cache):  # type: ignore[override]
        return ProviderUsage(total_tokens=1234, rate_limit_percent=42.0)


def test_snapshot_derives_elapsed_seconds_from_started_at():
    snap = _snapshot({"steps": 3, "started_at": time.time() - 120})
    assert 119 <= snap.elapsed_seconds <= 122


def test_snapshot_rejects_bool_number_fields():
    """A JSON `true` persisted into a numeric field must read as unknown, not 1."""
    snap = _snapshot({"steps": 1, "tokens": True, "rate_limit_percent": True})
    assert snap.tokens is None
    assert snap.rate_limit_percent is None


def test_a_broken_token_source_is_audited_once_per_session(tmp_path):
    store = Store(tmp_path / "state")
    budget = Budget(store)
    audit_path = tmp_path / "audit.jsonl"
    event = make_event()

    for _ in range(3):
        budget.tick(event, BoomSource(), recompute_every=1, audit_path=audit_path)

    errors = [e for e in audit.read(audit_path) if e.get("event") == "token_source"]
    assert len(errors) == 1, "a broken source must be surfaced, but only once per session"
    assert "kaboom" in errors[0]["reason"]


def test_a_broken_token_source_never_breaks_the_tick(tmp_path):
    budget = Budget(Store(tmp_path / "state"))
    snap = budget.tick(make_event(), BoomSource(), recompute_every=1)
    assert snap.steps == 1, "the step still counts even though the token read blew up"
    assert snap.tokens is None


def test_a_working_token_source_populates_the_snapshot(tmp_path):
    budget = Budget(Store(tmp_path / "state"))
    snap = budget.tick(make_event(), GoodSource(), recompute_every=1)
    assert snap.tokens == 1234
    assert snap.rate_limit_percent == 42.0


def test_tick_records_tool_fingerprints_for_loop_detection(tmp_path):
    budget = Budget(Store(tmp_path / "state"))
    event = make_event(tool_name="Write", tool_input={"file_path": "a.py"})
    snap = budget.tick(event, GoodSource(), recompute_every=0)
    for _ in range(2):
        snap = budget.tick(event, GoodSource(), recompute_every=0)
    assert len(snap.recent_tools) == 3, "every tool call is fingerprinted"
    assert len(set(snap.recent_tools)) == 1, "identical calls share a fingerprint"


def test_recent_tools_are_bounded(tmp_path):
    from runbreaker.budget import RECENT_MAX

    budget = Budget(Store(tmp_path / "state"))
    event = make_event(tool_name="Read", tool_input={"file_path": "a.py"})
    snap = SessionBudget(steps=0)
    for _ in range(RECENT_MAX + 20):
        snap = budget.tick(event, GoodSource(), recompute_every=0)
    assert len(snap.recent_tools) == RECENT_MAX, "the ledger keeps only the tail"


def test_fingerprint_distinguishes_tool_and_input():
    write_a = make_event(tool_name="Write", tool_input={"file_path": "a.py"})
    write_b = make_event(tool_name="Write", tool_input={"file_path": "b.py"})
    edit_a = make_event(tool_name="Edit", tool_input={"file_path": "a.py"})
    same = make_event(tool_name="Write", tool_input={"file_path": "a.py"})
    assert write_a.fingerprint() == same.fingerprint(), "same tool + input => same print"
    assert write_a.fingerprint() != write_b.fingerprint(), "input matters"
    assert write_a.fingerprint() != edit_a.fingerprint(), "tool name matters"
