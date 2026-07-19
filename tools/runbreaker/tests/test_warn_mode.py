"""Warn mode observes what it would have done, but never denies or blocks.

The adoption on-ramp: a team points runbreaker at real runs to tune thresholds
before it can interfere. The exit-code contract still holds — nothing is denied,
so every tool call exits 0 — while the audit trail records the trips that *would*
have fired.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import pre_tool_payload, run_hook

from runbreaker import audit
from runbreaker.breaker import Breaker
from runbreaker.state import Store


def _entries(project: Path) -> list[dict]:
    return audit.read(project / ".runbreaker" / "state" / "audit.jsonl")


def test_warn_mode_allows_a_write_that_would_have_tripped(project, monkeypatch, capsys):
    monkeypatch.setenv("RUNBREAKER_ENFORCE", "warn")
    monkeypatch.setenv("RUNBREAKER_MAX_STEPS", "1")  # trips from the 2nd call on

    codes = [
        run_hook(pre_tool_payload("claude"), "claude", "pre_tool_use", monkeypatch, capsys)[0]
        for _ in range(3)
    ]
    assert codes == [0, 0, 0], "warn mode must never deny a write"


def test_warn_mode_records_the_trip_but_leaves_the_breaker_closed(
    project, state_dir, monkeypatch, capsys
):
    monkeypatch.setenv("RUNBREAKER_ENFORCE", "warn")
    monkeypatch.setenv("RUNBREAKER_MAX_STEPS", "1")

    for _ in range(3):
        run_hook(pre_tool_payload("claude"), "claude", "pre_tool_use", monkeypatch, capsys)

    assert not Breaker(Store(state_dir)).status().is_open, "warn mode must not open the breaker"
    warns = [e for e in _entries(project) if e.get("decision") == "warn"]
    assert warns, "the trip that would have fired should still be audited"
    assert any("step budget" in e.get("reason", "") for e in warns)


def test_block_mode_still_denies(project, state_dir, monkeypatch, capsys):
    """The default (no RUNBREAKER_ENFORCE) is real enforcement."""
    monkeypatch.setenv("RUNBREAKER_MAX_STEPS", "1")
    codes = [
        run_hook(pre_tool_payload("claude"), "claude", "pre_tool_use", monkeypatch, capsys)[0]
        for _ in range(3)
    ]
    assert 2 in codes, "block mode must deny once the step budget is exceeded"
    assert Breaker(Store(state_dir)).status().is_open
