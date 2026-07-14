"""The exit-code matrix. This is the test that proves the design is safe.

Copilot denies a tool call on *any* non-zero exit other than 2, so an internal
exception must exit 0 everywhere. Exit 2 is reserved for a deliberate decision.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.conftest import PROVIDER_CASES, pre_tool_payload, run_hook

from agentguard import hook
from agentguard.breaker import Breaker
from agentguard.state import Store

PROVIDERS = tuple(PROVIDER_CASES)


def run_pre(provider, monkeypatch, capsys, payload=None):
    """Drive a PreToolUse hook with this provider's write-tool payload."""
    return run_hook(
        payload if payload is not None else pre_tool_payload(provider),
        provider,
        "pre_tool_use",
        monkeypatch,
        capsys,
    )


@pytest.mark.parametrize("provider", PROVIDERS)
def test_closed_breaker_allows(provider, project, monkeypatch, capsys):
    code, out, _ = run_pre(provider, monkeypatch, capsys)
    assert code == 0
    assert out == ""


@pytest.mark.parametrize("provider", PROVIDERS)
def test_open_breaker_denies_writes(provider, project, state_dir, monkeypatch, capsys):
    Breaker(Store(state_dir)).trip("test")
    code, _, err = run_pre(provider, monkeypatch, capsys)
    assert code == 2
    assert "circuit breaker is OPEN" in err


@pytest.mark.parametrize("provider", PROVIDERS)
def test_open_breaker_allows_reads(provider, project, state_dir, monkeypatch, capsys):
    Breaker(Store(state_dir)).trip("test")
    payload = {**pre_tool_payload(provider), "tool_name": "Read", "tool_input": {}}
    code, _, _ = run_hook(payload, provider, "pre_tool_use", monkeypatch, capsys)
    assert code == 0, "reads and `agentguard reset` must stay reachable"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_corrupt_breaker_state_denies_writes(provider, project, state_dir, monkeypatch, capsys):
    """A truncated safety file is not evidence that the run is safe."""
    (state_dir / "breaker.json").write_text('{"state": "clo', encoding="utf-8")
    code, _, err = run_pre(provider, monkeypatch, capsys)
    assert code == 2
    assert "state unreadable" in err


@pytest.mark.parametrize("provider", PROVIDERS)
def test_internal_exception_never_fails_closed(provider, project, monkeypatch, capsys):
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("bug in agentguard")

    monkeypatch.setattr(hook, "route", boom)
    code, _, _ = run_pre(provider, monkeypatch, capsys)
    assert code == 0, "a bug in agentguard must never lock a Copilot user out"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_malformed_stdin_allows(provider, project, monkeypatch, capsys):
    code, _, _ = run_pre(provider, monkeypatch, capsys, "not json at all")
    assert code == 0


@pytest.mark.parametrize("provider", PROVIDERS)
def test_open_breaker_lets_the_agent_stop(provider, project, state_dir, monkeypatch, capsys):
    """Blocking the stop of a read-only run would trap the agent in a loop."""
    Breaker(Store(state_dir)).trip("test")
    payload = {"session_id": "s1", "cwd": "/tmp", "hook_event_name": "Stop"}
    code, _, _ = run_hook(payload, provider, "stop", monkeypatch, capsys)
    assert code == 0


def test_claude_stop_hook_active_is_a_noop(project, monkeypatch, capsys):
    payload = {
        "session_id": "s1",
        "cwd": "/tmp",
        "hook_event_name": "Stop",
        "stop_hook_active": True,
    }
    code, _, _ = run_hook(payload, "claude", "stop", monkeypatch, capsys)
    assert code == 0


def test_copilot_block_stop_uses_json_body(project, monkeypatch, capsys):
    """Copilot only documents exit 2 for preToolUse; agentStop takes a JSON body."""
    import json

    from tests.conftest import make_event

    from agentguard.events import block_stop
    from agentguard.providers import CopilotAdapter

    stdout, stderr, code = CopilotAdapter().emit(block_stop("fix the tests"), make_event())
    assert code == 0
    assert stderr is None
    assert json.loads(stdout or "") == {"decision": "block", "reason": "fix the tests"}


@pytest.mark.parametrize("provider", ["claude", "codex", "vscode"])
def test_exit_code_adapters_block_stop_via_exit_2(provider, project):
    from tests.conftest import make_event

    from agentguard.events import block_stop
    from agentguard.providers import ADAPTERS

    stdout, stderr, code = ADAPTERS[provider]().emit(block_stop("fix it"), make_event())
    assert (stdout, stderr, code) == (None, "fix it", 2)


@pytest.mark.skipif(os.name != "posix", reason="pins a POSIX-only PATH")
def test_shim_path_runs_as_a_real_process(project: Path) -> None:
    """The generated shim must import agentguard without a venv on sys.path."""
    import json
    import subprocess
    import sys

    from agentguard import config, install

    cfg = config.load()
    install.write_shims(cfg, ("claude",))
    shim = cfg.home / "shims" / "claude_pre_tool_use.py"

    proc = subprocess.run(
        [sys.executable, "-S", str(shim)],
        input=json.dumps(pre_tool_payload("claude")),
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "AGENTGUARD_HOME": str(cfg.home),
            "AGENTGUARD_PROJECT_DIR": str(project),
        },
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
