from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from agentguard import hook
from agentguard.events import EventType, HookEvent

AGENTGUARD_VARS = (
    "AGENTGUARD_HOME",
    "AGENTGUARD_PROJECT_DIR",
    "AGENTGUARD_MAX_STEPS",
    "AGENTGUARD_MAX_TOKENS",
    "AGENTGUARD_BREAKER_THRESHOLD",
    "AGENTGUARD_MAX_RATE_PERCENT",
    "AGENTGUARD_SKIP_BUDGET",
    "AGENTGUARD_SKIP_GATE",
    "AGENTGUARD_GC_DAYS",
    "AGENTGUARD_MAX_SESSIONS",
    "AGENTGUARD_TOKEN_SOURCE",
    "AGENTGUARD_RECOMPUTE_EVERY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (*AGENTGUARD_VARS, "CODEX_HOME", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AGENTGUARD_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTGUARD_HOME", str(tmp_path / ".agentguard"))
    return tmp_path


@pytest.fixture
def state_dir(project: Path) -> Path:
    path = project / ".agentguard" / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_event(**kwargs: object) -> HookEvent:
    defaults = {
        "provider": "claude",
        "event": EventType.PRE_TOOL_USE,
        "session_id": "s1",
        "cwd": "/tmp",
    }
    return HookEvent(**{**defaults, **kwargs})  # type: ignore[arg-type]


#: Per-provider payload shapes and the tool name that mutates files there.
PROVIDER_CASES = {
    "claude": ("Write", {"file_path": "a.py"}),
    "codex": ("apply_patch", {"file_path": "a.py"}),
    "copilot": ("create", {"file_path": "a.py"}),
    "vscode": ("copilot_createFile", {"filePath": "a.py"}),
}


def pre_tool_payload(provider: str) -> dict[str, object]:
    tool, args = PROVIDER_CASES[provider]
    return {
        "session_id": "s1",
        "cwd": "/tmp",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": args,
    }


def run_hook(
    payload: object,
    provider: str,
    event: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    """Drive `hook.main` in-process and return (exit code, stdout, stderr)."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    with pytest.raises(SystemExit) as exc:
        hook.main(["--provider", provider, "--event", event])
    captured = capsys.readouterr()
    return int(exc.value.code or 0), captured.out, captured.err
