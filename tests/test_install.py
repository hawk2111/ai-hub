"""Install must produce hook invocations that are Python, portable, and shell-free
wherever the host allows it."""

from __future__ import annotations

import json
import sys

from agentguard import config, install


def test_the_command_spawns_the_installing_interpreter_not_a_bare_python3(project):
    """`python3` is absent from PATH on Windows. A hook that cannot start is worse
    than none: Claude proceeds unguarded, Copilot CLI reads the exit code as deny."""
    command = install._shell_command(config.load(), "codex", "pre_tool_use")
    assert sys.executable in command
    assert not command.startswith("python3 ")


# -- Claude: exec form, no shell at all -------------------------------------


def test_claude_uses_exec_form_so_no_shell_is_involved(project):
    entry = install._exec_entry(config.load(), "claude", "pre_tool_use")
    assert entry["command"] == sys.executable
    assert entry["args"][0] == "-S"
    assert '"' not in entry["args"][1], "exec form takes an argv; nothing needs quoting"


def test_claude_exec_form_keeps_the_shim_path_relocatable(project):
    entry = install._exec_entry(config.load(), "claude", "stop")
    assert entry["args"][1].startswith("${CLAUDE_PROJECT_DIR}/")
    assert entry["args"][1].endswith(".agentguard/shims/claude_stop.py")


def test_claude_exec_form_falls_back_to_an_absolute_path_outside_the_project(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENTGUARD_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("AGENTGUARD_HOME", str(tmp_path / "elsewhere"))
    entry = install._exec_entry(config.load(), "claude", "stop")
    assert entry["args"][1].startswith(str(tmp_path / "elsewhere"))


def test_the_installed_claude_hook_carries_args(project):
    install.install(config.load(), ("claude",))
    data = json.loads((project / ".claude" / "settings.json").read_text())
    hook = data["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "args" in hook


# -- Codex and Copilot: command string, but never a shell script -------------


def _keys(obj) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


def test_no_config_ever_names_a_shell(project):
    install.install(config.load(), ("claude", "codex", "copilot"))
    for rel in (".claude/settings.json", ".codex/hooks.json", ".github/hooks/agentguard.json"):
        keys = _keys(json.loads((project / rel).read_text()))
        assert "bash" not in keys, f"{rel} must not name a shell"
        assert "powershell" not in keys, f"{rel} needs no shell override on a space-free path"


def test_copilot_uses_the_shell_neutral_command_field(project):
    install.install(config.load(), ("copilot",))
    data = json.loads((project / ".github" / "hooks" / "agentguard.json").read_text())
    entry = data["hooks"]["PreToolUse"][0]
    assert "command" in entry
    assert "bash" not in entry


def test_the_command_string_has_no_shell_metacharacters(project):
    command = install._shell_command(config.load(), "codex", "stop")
    assert not any(ch in command for ch in "|&;<>$`()")


# -- the one unavoidable PowerShell override --------------------------------


def test_paths_containing_spaces_are_quoted(tmp_path, monkeypatch):
    home = tmp_path / "Program Files" / "guard"
    monkeypatch.setenv("AGENTGUARD_HOME", str(home))
    monkeypatch.setenv("AGENTGUARD_PROJECT_DIR", str(tmp_path))
    command = install._shell_command(config.load(), "codex", "stop")
    assert f'"{home / "shims" / "codex_stop.py"}"' in command


def test_a_spaced_path_forces_a_powershell_override_with_the_call_operator(
    tmp_path, monkeypatch
):
    """PowerShell reads a quoted exe path as a string literal; bash rejects a leading
    `&`. One string cannot serve both, so the space case needs the override."""
    monkeypatch.setenv("AGENTGUARD_HOME", str(tmp_path / "Program Files" / "guard"))
    monkeypatch.setenv("AGENTGUARD_PROJECT_DIR", str(tmp_path))
    install.install(config.load(), ("copilot",))
    entry = json.loads((tmp_path / ".github" / "hooks" / "agentguard.json").read_text())[
        "hooks"
    ]["PreToolUse"][0]
    assert entry["powershell"].startswith("& ")
    assert entry["powershell"].endswith(entry["command"])


def test_a_space_free_path_needs_no_powershell_override(project):
    install.install(config.load(), ("copilot",))
    entry = json.loads((project / ".github" / "hooks" / "agentguard.json").read_text())["hooks"][
        "PreToolUse"
    ][0]
    assert "powershell" not in entry, "the neutral `command` field covers both shells"


def test_quote_leaves_space_free_paths_alone():
    assert install._quote("/usr/bin/python3") == "/usr/bin/python3"
    assert install._quote(r"C:\Program Files\python.exe") == r'"C:\Program Files\python.exe"'


# -- merging behaviour -------------------------------------------------------


def test_install_is_idempotent(project):
    cfg = config.load()
    install.install(cfg, ("claude",))
    install.install(cfg, ("claude",))
    data = json.loads((project / ".claude" / "settings.json").read_text())
    assert len(data["hooks"]["PreToolUse"]) == 1, "re-running install must not stack entries"


def test_install_preserves_foreign_hook_entries(project):
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "mine"}]}]}})
    )
    install.install(config.load(), ("claude",))
    entries = json.loads(settings.read_text())["hooks"]["PreToolUse"]
    assert len(entries) == 2
    assert any("mine" in json.dumps(e) for e in entries)


def test_gitignore_gets_the_generated_paths(project):
    install.install(config.load(), ("claude",))
    ignored = (project / ".gitignore").read_text()
    assert ".agentguard/state/" in ignored
    assert ".agentguard/shims/" in ignored
