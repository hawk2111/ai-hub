"""The human-facing subcommands: doctor (is it wired up?) and report (what did
it do?)."""

from __future__ import annotations

import json

from runbreaker import audit, config, install
from runbreaker.cli import main


def test_doctor_fails_before_install_and_passes_after(project, capsys):
    assert main(["doctor", "--provider", "claude"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "shim" in out

    install.install(config.load(), ("claude",))
    assert main(["doctor", "--provider", "claude"]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out


def test_doctor_json_is_machine_readable(project, capsys):
    install.install(config.load(), ("claude",))
    main(["doctor", "--provider", "claude", "--json"])
    checks = json.loads(capsys.readouterr().out)
    assert all(c["ok"] for c in checks)
    assert any("shim" in c["label"] for c in checks)


def test_report_summarizes_the_audit_trail(project, capsys):
    cfg = config.load()
    audit.record(cfg.audit_path, event="trip", decision="open", provider="claude", reason="boom")
    audit.record(cfg.audit_path, event="blocked", decision="deny", provider="claude")

    assert main(["report"]) == 0
    out = capsys.readouterr().out
    assert "2 audit event(s)" in out
    assert "trip/open: 1" in out
    assert "blocked/deny: 1" in out
    assert "boom" in out, "recent trips should surface their reason"


def test_report_json_gives_aggregates(project, capsys):
    cfg = config.load()
    audit.record(cfg.audit_path, event="trip", decision="warn", provider="codex")
    main(["report", "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["total"] == 1
    assert summary["by_outcome"]["trip/warn"] == 1
    assert summary["by_provider"]["codex"] == 1


def test_report_on_an_empty_trail_does_not_crash(project, capsys):
    assert main(["report"]) == 0
    assert "0 audit event(s)" in capsys.readouterr().out


def test_status_is_human_readable_by_default(project, capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "breaker: CLOSED" in out
    assert "step_budget(max_steps=250)" in out, "active conditions should be listed"
    assert "sessions: none tracked yet" in out


def test_status_reflects_an_open_breaker(project, capsys):
    main(["trip", "went sideways"])
    capsys.readouterr()
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "breaker: OPEN" in out
    assert "went sideways" in out
    assert "runbreaker reset" in out, "an open breaker should tell the human how to close it"


def test_status_json_stays_machine_readable(project, capsys):
    main(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["breaker"]["state"] == "closed"
    assert "conditions" in payload


def test_manual_trip_is_recorded_in_the_audit_trail(project, capsys):
    cfg = config.load()
    assert main(["trip", "manual stop"]) == 0
    capsys.readouterr()

    entries = audit.read(cfg.audit_path)
    assert [e["event"] for e in entries] == ["trip"]
    assert entries[0]["decision"] == "open"
    assert entries[0]["provider"] == "cli"
    assert entries[0]["reason"] == "manual stop"

    # ...and it surfaces in `report`, so a human sees the same trip they caused.
    assert main(["report"]) == 0
    out = capsys.readouterr().out
    assert "trip/open: 1" in out
    assert "manual stop" in out


def test_manual_reset_is_recorded_in_the_audit_trail(project, capsys):
    cfg = config.load()
    main(["reset"])
    capsys.readouterr()
    entries = audit.read(cfg.audit_path)
    assert [(e["event"], e["decision"]) for e in entries] == [("reset", "closed")]
