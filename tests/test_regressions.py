"""Regressions for defects found in review. Each test names the failure it prevents."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from tests.conftest import make_event, run_hook

from agentguard import config, gate, install
from agentguard.breaker import Breaker
from agentguard.budget import Budget
from agentguard.state import Store
from agentguard.tokens import base
from agentguard.tokens.claude import ClaudeTokenSource, usage_tokens
from agentguard.tokens.copilot import CopilotTokenSource
from agentguard.tokens.null import NullTokenSource


def write_config(project: Path, body: str) -> None:
    home = project / ".agentguard"
    home.mkdir(parents=True, exist_ok=True)
    (home / "agentguard.toml").write_text(textwrap.dedent(body), encoding="utf-8")


STOP_PAYLOAD = {"session_id": "s1", "cwd": "/tmp", "hook_event_name": "Stop"}


# -- 1. a broken install must not lock a Copilot user out --------------------


def test_a_shim_whose_package_vanished_exits_zero(project):
    """Deleted venv, moved checkout: the ImportError happens before `hook.main`'s
    catch-all can see it, and any exit code but 0 or 2 is a deny on Copilot CLI."""
    cfg = config.load()
    install.write_shims(cfg, ("copilot",))
    shim = cfg.home / "shims" / "copilot_pre_tool_use.py"
    shim.write_text(
        shim.read_text().replace(str(install.package_root()), "/no/such/path"), encoding="utf-8"
    )

    proc = subprocess.run(
        [sys.executable, "-S", str(shim)],
        input=json.dumps({"session_id": "s", "cwd": "/tmp", "tool_name": "create"}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


# -- 2. a truncated read is unknown, not a confident number ------------------


def test_an_oversized_unread_tail_is_unknown_not_a_partial_sum(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "MAX_DELTA_BYTES", 10)
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"message": {"usage": {"input_tokens": 5}}}) + "\n")
    usage = ClaudeTokenSource().read(make_event(transcript_path=str(path)), {})
    assert usage.total_tokens is None, "summing a prefix and calling it a total is a lie"


# -- 3. a config error must not bounce the agent -----------------------------


def test_an_unlaunchable_check_does_not_block_the_stop(project, monkeypatch, capsys):
    write_config(
        project,
        """
        [[gate.checks]]
        name = "typo"
        command = ["definitely-not-a-real-binary"]
        """,
    )
    code, _, _ = run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)
    assert code == 0, "a typo in agentguard.toml is not broken code to fix"


def test_a_real_failure_alongside_a_config_error_still_blocks(project, monkeypatch, capsys):
    write_config(
        project,
        f"""
        [[gate.checks]]
        name = "typo"
        command = ["definitely-not-a-real-binary"]

        [[gate.checks]]
        name = "failing"
        command = ["{sys.executable}", "-c", "import sys; sys.exit(1)"]
        """,
    )
    code, _, err = run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)
    assert code == 2
    assert "failing FAILED" in err
    assert "typo" not in err, "the model is told about code, not about our config"


# -- 4. Copilot learns transcript_path at Stop -------------------------------


def test_stop_caches_the_transcript_path_for_later_pre_tool_use(project, state_dir, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"usage": {"total_tokens": 4242}}) + "\n")

    budget = Budget(Store(state_dir))
    stop_event = make_event(provider="copilot", session_id="c1", transcript_path=str(transcript))
    budget.refresh(stop_event, CopilotTokenSource())

    # A later PreToolUse carries no transcript_path at all.
    later = make_event(provider="copilot", session_id="c1", transcript_path=None)
    assert budget.tick(later, CopilotTokenSource(), recompute_every=1).tokens == 4242


# -- 5. zero is a value, not an absence --------------------------------------


def test_env_zero_disables_gc(project, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_GC_DAYS", "0")
    assert config.load().gc_days == 0


def test_env_zero_disables_the_token_recompute(project, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_RECOMPUTE_EVERY", "0")
    assert config.load().budget.recompute_every == 0


def test_env_zero_disables_the_session_cap(project, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_MAX_SESSIONS", "0")
    assert config.load().max_sessions == 0


# -- 6. incremental reads --------------------------------------------------


def test_the_transcript_is_read_once_and_then_only_its_tail(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"message": {"usage": {"input_tokens": 10}}}) + "\n")

    cache: dict[str, object] = {}
    assert base.incremental_sum(path, cache, "claude", usage_tokens) == 10
    first_offset = cache["claude_offset"]

    with path.open("a") as fh:
        fh.write(json.dumps({"message": {"usage": {"input_tokens": 7}}}) + "\n")

    assert base.incremental_sum(path, cache, "claude", usage_tokens) == 17
    assert cache["claude_offset"] > first_offset


def test_a_rotated_transcript_restarts_the_sum(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"message": {"usage": {"input_tokens": 10}}}) + "\n")
    cache: dict[str, object] = {}
    base.incremental_sum(path, cache, "claude", usage_tokens)

    path.write_text(json.dumps({"message": {"usage": {"input_tokens": 3}}}) + "\n")  # shorter
    assert base.incremental_sum(path, cache, "claude", usage_tokens) == 3


def test_a_half_written_final_line_is_reread_next_time(tmp_path):
    path = tmp_path / "t.jsonl"
    complete = json.dumps({"message": {"usage": {"input_tokens": 10}}}) + "\n"
    path.write_text(complete + '{"message": {"usa')
    cache: dict[str, object] = {}
    assert base.incremental_sum(path, cache, "claude", usage_tokens) == 10

    path.write_text(complete + json.dumps({"message": {"usage": {"input_tokens": 5}}}) + "\n")
    assert base.incremental_sum(path, cache, "claude", usage_tokens) == 15


# -- 7. the host must wait long enough for the gate --------------------------


def test_the_stop_hook_timeout_is_sized_from_the_configured_checks(project):
    write_config(
        project,
        """
        [[gate.checks]]
        name = "slow"
        command = ["true"]
        timeout = 400
        """,
    )
    assert install.stop_timeout(config.load()) == 400 + install.PRE_TOOL_TIMEOUT


def test_a_gateless_project_gets_the_minimum_stop_timeout(project):
    assert install.stop_timeout(config.load()) == install.MIN_STOP_TIMEOUT


def test_copilot_stop_timeout_covers_the_gate(project):
    write_config(
        project,
        f"""
        [[gate.checks]]
        name = "slow"
        command = ["true"]
        timeout = {gate.DEFAULT_TIMEOUT}
        """,
    )
    install.install(config.load(), ("copilot",))
    hooks = json.loads((project / ".github" / "hooks" / "agentguard.json").read_text())["hooks"]
    assert hooks["Stop"][0]["timeoutSec"] > hooks["PreToolUse"][0]["timeoutSec"]


# -- 8. the failure that opened the breaker is not thrown away ---------------


def test_the_gate_output_survives_into_the_audit_when_the_breaker_trips(
    project, state_dir, monkeypatch, capsys
):
    write_config(
        project,
        f"""
        [[conditions]]
        id = "gate_failures"
        threshold = 1

        [[gate.checks]]
        name = "failing"
        command = ["{sys.executable}", "-c", "import sys; print('the real reason'); sys.exit(1)"]
        """,
    )
    code, _, _ = run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)
    assert code == 0
    assert Breaker(Store(state_dir)).status().is_open

    trips = [
        json.loads(line)
        for line in (project / ".agentguard" / "state" / "audit.jsonl").read_text().splitlines()
        if json.loads(line)["event"] == "trip"
    ]
    assert "the real reason" in trips[-1]["detail"]["gate"]


# -- housekeeping ------------------------------------------------------------


def test_install_scaffolds_a_starter_config(project):
    install.install(config.load(), ("claude",))
    body = (project / ".agentguard" / "agentguard.toml").read_text()
    assert "[[conditions]]" in body and "step_budget" in body


def test_install_never_overwrites_a_tuned_config(project):
    write_config(project, "# mine\n")
    install.install(config.load(), ("claude",))
    assert (project / ".agentguard" / "agentguard.toml").read_text() == "# mine\n"


def test_install_leaves_a_foreign_hook_that_merely_mentions_agentguard(project):
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"command": "echo agentguard is nice"}]}]}})
    )
    install.install(config.load(), ("claude",))
    entries = json.loads(settings.read_text())["hooks"]["PreToolUse"]
    assert len(entries) == 2, "only an entry pointing at our shims is ours to replace"


# -- stop-time condition evaluation ------------------------------------------


def test_a_token_budget_can_trip_at_stop_even_with_no_gate(project, state_dir, monkeypatch, capsys):
    transcript = project / "t.jsonl"
    transcript.write_text(json.dumps({"message": {"usage": {"input_tokens": 5000}}}) + "\n")
    monkeypatch.setenv("AGENTGUARD_MAX_TOKENS", "1000")

    payload = {**STOP_PAYLOAD, "transcript_path": str(transcript)}
    code, _, _ = run_hook(payload, "claude", "stop", monkeypatch, capsys)
    assert code == 0, "an open breaker lets the agent stop and surface to a human"
    assert Breaker(Store(state_dir)).status().is_open


def test_skip_budget_leaves_the_stop_gate_alone(project, state_dir, monkeypatch, capsys):
    monkeypatch.setenv("AGENTGUARD_SKIP_BUDGET", "1")
    code, _, _ = run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)
    assert code == 0
    assert Budget(Store(state_dir)).status("s1").steps == 0


def test_null_source_keeps_stop_cheap(project, state_dir):
    budget = Budget(Store(state_dir))
    snapshot = budget.refresh(make_event(session_id="x"), NullTokenSource())
    assert snapshot.tokens is None and snapshot.steps == 0, "refresh must not count a step"
