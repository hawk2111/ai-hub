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
