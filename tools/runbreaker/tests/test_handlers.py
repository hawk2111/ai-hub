"""End-to-end handler behaviour: budgets trip the breaker, red gates block stops."""

from __future__ import annotations

import json
import textwrap

import pytest
from tests.conftest import PYEXE, pre_tool_payload, run_hook

from runbreaker.breaker import Breaker
from runbreaker.providers import (
    ClaudeAdapter,
    CodexAdapter,
    CopilotAdapter,
    VSCodeAdapter,
    detect,
)
from runbreaker.state import Store


def write_config(project, body: str) -> None:
    home = project / ".runbreaker"
    home.mkdir(parents=True, exist_ok=True)
    (home / "runbreaker.toml").write_text(textwrap.dedent(body), encoding="utf-8")


def read_payload(project) -> dict:
    payload = pre_tool_payload("claude")
    return payload


# -- budget trips the breaker -----------------------------------------------


def test_step_budget_trips_the_breaker_and_denies_the_offending_write(
    project, state_dir, monkeypatch, capsys
):
    monkeypatch.setenv("RUNBREAKER_MAX_STEPS", "2")
    payload = read_payload(project)

    assert run_hook(payload, "claude", "pre_tool_use", monkeypatch, capsys)[0] == 0
    assert run_hook(payload, "claude", "pre_tool_use", monkeypatch, capsys)[0] == 0

    code, _, err = run_hook(payload, "claude", "pre_tool_use", monkeypatch, capsys)
    assert code == 2
    assert "step budget exceeded" in err

    assert Breaker(Store(state_dir)).status().is_open


def test_a_tripped_breaker_still_lets_reads_through(project, state_dir, monkeypatch, capsys):
    monkeypatch.setenv("RUNBREAKER_MAX_STEPS", "1")
    write_payload = read_payload(project)
    read_only = {**write_payload, "tool_name": "Read", "tool_input": {}}

    run_hook(write_payload, "claude", "pre_tool_use", monkeypatch, capsys)
    run_hook(read_only, "claude", "pre_tool_use", monkeypatch, capsys)  # trips here
    assert Breaker(Store(state_dir)).status().is_open

    code, _, _ = run_hook(read_only, "claude", "pre_tool_use", monkeypatch, capsys)
    assert code == 0


def test_skip_budget_disables_the_meter(project, state_dir, monkeypatch, capsys):
    monkeypatch.setenv("RUNBREAKER_MAX_STEPS", "1")
    monkeypatch.setenv("RUNBREAKER_SKIP_BUDGET", "1")
    payload = read_payload(project)
    for _ in range(5):
        assert run_hook(payload, "claude", "pre_tool_use", monkeypatch, capsys)[0] == 0
    assert Breaker(Store(state_dir)).status().is_open is False


def test_a_trip_is_written_to_the_audit_log(project, monkeypatch, capsys):
    monkeypatch.setenv("RUNBREAKER_MAX_STEPS", "1")
    payload = read_payload(project)
    run_hook(payload, "claude", "pre_tool_use", monkeypatch, capsys)
    run_hook(payload, "claude", "pre_tool_use", monkeypatch, capsys)

    lines = (project / ".runbreaker" / "state" / "audit.jsonl").read_text().strip().splitlines()
    events = [json.loads(line) for line in lines]
    assert any(e["event"] == "trip" and e["decision"] == "open" for e in events)
    assert any(e["event"] == "blocked" and e["decision"] == "deny" for e in events)


# -- the stop gate ----------------------------------------------------------


STOP_PAYLOAD = {"session_id": "s1", "cwd": "/tmp", "hook_event_name": "Stop"}


def test_no_configured_checks_means_the_stop_gate_is_a_noop(project, monkeypatch, capsys):
    code, _, _ = run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)
    assert code == 0


def test_a_red_gate_blocks_the_stop_and_hands_back_the_failure(project, monkeypatch, capsys):
    write_config(
        project,
        f"""
        [[gate.checks]]
        name = "failing"
        command = ["{PYEXE}", "-c", "import sys; print('boom'); sys.exit(1)"]
        """,
    )
    code, _, err = run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)
    assert code == 2
    assert "quality gate is red" in err
    assert "boom" in err


def test_a_green_gate_lets_the_agent_finish(project, monkeypatch, capsys):
    write_config(
        project,
        f"""
        [[gate.checks]]
        name = "passing"
        command = ["{PYEXE}", "-c", "pass"]
        """,
    )
    code, _, _ = run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)
    assert code == 0


def test_consecutive_red_gates_open_the_breaker(project, state_dir, monkeypatch, capsys):
    write_config(
        project,
        f"""
        [[conditions]]
        id = "gate_failures"
        threshold = 2

        [[gate.checks]]
        name = "failing"
        command = ["{PYEXE}", "-c", "import sys; sys.exit(1)"]
        """,
    )
    assert run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)[0] == 2

    # Second failure hits the threshold: the breaker opens in the same turn, and the
    # agent is allowed to stop so it surfaces to a human instead of looping.
    code, _, _ = run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)
    assert code == 0
    assert Breaker(Store(state_dir)).status().is_open


def test_a_green_gate_clears_a_stale_failure_count(project, state_dir, monkeypatch, capsys):
    breaker = Breaker(Store(state_dir))
    breaker.record_fail()
    breaker.record_fail()

    # No checks configured, so this stop is a no-op — and must still clear the count.
    run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)
    assert breaker.status().fails == 0


def test_an_unlaunchable_check_never_marches_the_breaker_toward_its_threshold(
    project, state_dir, monkeypatch, capsys
):
    """A typo in runbreaker.toml (or `npm` on Windows) is a config error, not red code."""
    write_config(
        project,
        """
        [[conditions]]
        id = "gate_failures"
        threshold = 1

        [[gate.checks]]
        name = "typo"
        command = ["definitely-not-a-real-binary"]
        """,
    )
    run_hook(STOP_PAYLOAD, "claude", "stop", monkeypatch, capsys)
    breaker = Breaker(Store(state_dir))
    assert breaker.status().fails == 0
    assert breaker.status().is_open is False

    entries = (project / ".runbreaker" / "state" / "audit.jsonl").read_text()
    assert "config_error" in entries


def test_the_stop_gate_gives_up_rather_than_looping_forever(project, monkeypatch, capsys):
    """Only Claude reports `stop_hook_active`; elsewhere the block counter is the guard."""
    write_config(
        project,
        f"""
        [gate]
        max_stop_blocks = 2

        [[conditions]]
        id = "gate_failures"
        threshold = 99

        [[gate.checks]]
        name = "failing"
        command = ["{PYEXE}", "-c", "import sys; sys.exit(1)"]
        """,
    )
    codes = [run_hook(STOP_PAYLOAD, "codex", "stop", monkeypatch, capsys)[0] for _ in range(3)]
    assert codes == [2, 2, 0]


# -- provider detection -----------------------------------------------------


def test_detection_prefers_the_explicit_flag():
    assert isinstance(detect({"toolName": "create"}, "claude"), ClaudeAdapter)


def test_a_vscode_tool_name_overrides_the_baked_provider_flag():
    """VS Code reads `.claude/settings.json`, so a claude-baked shim can be handed a
    VS Code tool call. Without the override the breaker would deny nothing there."""
    payload = {"session_id": "s", "cwd": "/tmp", "tool_name": "copilot_createFile"}
    assert isinstance(detect(payload, "claude"), VSCodeAdapter)
    assert isinstance(detect(payload, "copilot"), VSCodeAdapter)


def test_vscode_write_tools_are_recognised_through_a_claude_shim(
    project, state_dir, monkeypatch, capsys
):
    Breaker(Store(state_dir)).trip("test")
    payload = {
        "session_id": "s1",
        "cwd": "/tmp",
        "hook_event_name": "PreToolUse",
        "tool_name": "copilot_replaceString",
        "tool_input": {"filePath": "a.py"},
    }
    code, _, err = run_hook(payload, "claude", "pre_tool_use", monkeypatch, capsys)
    assert code == 2
    assert "circuit breaker is OPEN" in err


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("copilot_createFile", True),
        ("copilot_applyPatch", True),
        ("copilot_replaceString", True),
        ("copilot_multiReplaceString", True),
        ("copilot_editNotebook", True),
        ("copilot_readFile", False),
        ("copilot_findFiles", False),
        # Escape hatches, gated nowhere — same policy as Bash elsewhere.
        ("copilot_runVscodeCommand", False),
        ("copilot_runNotebookCell", False),
    ],
)
def test_vscode_write_tool_vocabulary(tool, expected):
    payload = {"session_id": "s", "cwd": "/tmp", "tool_name": tool, "hook_event_name": "PreToolUse"}
    assert VSCodeAdapter().parse(payload).is_write is expected


def test_camelcase_payload_identifies_copilot():
    assert isinstance(detect({"toolName": "create", "toolArgs": {}}), CopilotAdapter)


def test_codex_specific_fields_identify_codex():
    assert isinstance(detect({"turn_id": "t1", "tool_name": "Bash"}), CodexAdapter)


def test_snake_case_without_hints_falls_back_to_claude():
    assert isinstance(detect({"tool_name": "Write"}), ClaudeAdapter)


@pytest.mark.parametrize(
    ("provider", "tool", "expected"),
    [
        ("claude", "Write", True),
        ("claude", "Read", False),
        ("codex", "apply_patch", True),
        ("codex", "Bash", False),
        ("copilot", "create", True),
        ("copilot", "view", False),
    ],
)
def test_each_adapter_knows_its_own_write_tools(provider, tool, expected):
    from runbreaker.providers import ADAPTERS

    payload = {"session_id": "s", "cwd": "/tmp", "tool_name": tool, "hook_event_name": "PreToolUse"}
    assert ADAPTERS[provider]().parse(payload).is_write is expected


def test_copilot_camelcase_payload_still_parses():
    event = CopilotAdapter().parse({"sessionId": "s1", "toolName": "create", "toolArgs": {"a": 1}})
    assert (event.session_id, event.tool_name, event.is_write) == ("s1", "create", True)
    assert event.tool_input == {"a": 1}
