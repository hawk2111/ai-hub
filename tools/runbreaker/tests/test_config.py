from __future__ import annotations

import textwrap

from runbreaker import config


def write_config(project, body: str) -> None:
    home = project / ".runbreaker"
    home.mkdir(parents=True, exist_ok=True)
    (home / "runbreaker.toml").write_text(textwrap.dedent(body), encoding="utf-8")


def test_defaults_when_no_file_exists(project):
    cfg = config.load()
    ids = [c["id"] for c in cfg.conditions]
    assert ids == ["step_budget", "gate_failures"]
    assert cfg.gate.enabled is False, "no checks configured => the Stop hook is a no-op"


def test_file_replaces_the_default_conditions(project):
    write_config(
        project,
        """
        [[conditions]]
        id = "token_budget"
        max_tokens = 500
        """,
    )
    assert [c["id"] for c in config.load().conditions] == ["token_budget"]


def test_env_overrides_the_file(project, monkeypatch):
    write_config(
        project,
        """
        [[conditions]]
        id = "step_budget"
        max_steps = 10
        """,
    )
    monkeypatch.setenv("RUNBREAKER_MAX_STEPS", "999")
    (step,) = config.load().conditions
    assert step["max_steps"] == 999


def test_env_can_add_a_condition_absent_from_the_file(project, monkeypatch):
    monkeypatch.setenv("RUNBREAKER_MAX_TOKENS", "1234")
    conditions = {c["id"]: c for c in config.load().conditions}
    assert conditions["token_budget"]["max_tokens"] == 1234


def test_a_malformed_toml_falls_back_to_defaults(project):
    write_config(project, "this is not = valid = toml")
    assert [c["id"] for c in config.load().conditions] == ["step_budget", "gate_failures"]


def test_gate_checks_are_parsed(project):
    write_config(
        project,
        """
        [gate]
        watch = ["src/**/*.py"]
        tail_lines = 5

        [[gate.checks]]
        name = "pytest"
        command = ["python3", "-m", "pytest", "-q"]
        """,
    )
    gate = config.load().gate
    assert gate.enabled and gate.tail_lines == 5
    assert gate.checks[0].command == ("python3", "-m", "pytest", "-q")
    assert gate.checks[0].fail_records_breaker is True


def test_a_check_without_a_command_is_dropped(project):
    write_config(
        project,
        """
        [[gate.checks]]
        name = "bogus"
        """,
    )
    assert config.load().gate.enabled is False


def test_skip_flags_come_from_env(project, monkeypatch):
    monkeypatch.setenv("RUNBREAKER_SKIP_BUDGET", "1")
    monkeypatch.setenv("RUNBREAKER_SKIP_GATE", "1")
    cfg = config.load()
    assert cfg.skip_budget and cfg.skip_gate


def test_warn_only_defaults_off(project):
    assert config.load().warn_only is False


def test_warn_only_from_file(project):
    write_config(
        project,
        """
        [breaker]
        mode = "warn"
        """,
    )
    assert config.load().warn_only is True


def test_warn_only_env_overrides_the_file(project, monkeypatch):
    write_config(project, '[breaker]\nmode = "warn"\n')
    monkeypatch.setenv("RUNBREAKER_ENFORCE", "block")
    assert config.load().warn_only is False


def test_time_and_cost_ceilings_come_from_env(project, monkeypatch):
    monkeypatch.setenv("RUNBREAKER_MAX_MINUTES", "45")
    monkeypatch.setenv("RUNBREAKER_MAX_USD", "12.5")
    conditions = {c["id"]: c for c in config.load().conditions}
    assert conditions["time_budget"]["max_minutes"] == 45
    assert conditions["cost_budget"]["max_usd"] == 12.5
