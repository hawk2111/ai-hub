from __future__ import annotations

import subprocess
import sys

from runbreaker import gate
from runbreaker.config import GateCheck, GateConfig


def check(*args: str, name: str = "c", **kwargs) -> GateCheck:
    return GateCheck(name=name, command=(sys.executable, "-c", *args), **kwargs)


def test_glob_star_does_not_cross_directories():
    assert gate.matches("src/a.py", ("src/*.py",))
    assert not gate.matches("src/nested/a.py", ("src/*.py",))


def test_globstar_crosses_directories():
    assert gate.matches("src/nested/deep/a.py", ("src/**/*.py",))
    assert gate.matches("src/a.py", ("src/**/*.py",))


def test_a_green_check_produces_no_failure(tmp_path):
    assert gate.run_check(check("pass"), tmp_path, 40) is None


def test_a_red_check_captures_the_output_tail(tmp_path):
    failure = gate.run_check(check("import sys; print('boom'); sys.exit(1)"), tmp_path, 40)
    assert failure is not None
    assert "boom" in failure.output


def test_a_missing_command_is_flagged_as_a_config_error(tmp_path):
    """Easy to hit on Windows, where `npm` is `npm.cmd`. It must not march the
    breaker toward its threshold — that would turn a typo into a lockout."""
    missing = GateCheck(name="ghost", command=("definitely-not-a-real-binary",))
    failure = gate.run_check(missing, tmp_path, 40)
    assert failure is not None
    assert "command not found" in failure.output
    assert failure.config_error is True


def test_a_timeout_is_a_real_failure_not_a_config_error(tmp_path):
    sleep = (sys.executable, "-c", "import time; time.sleep(5)")
    slow = GateCheck(name="slow", command=sleep, timeout=1)
    failure = gate.run_check(slow, tmp_path, 40)
    assert failure is not None and "timed out" in failure.output
    assert failure.config_error is False


def test_a_failing_check_is_a_real_failure_not_a_config_error(tmp_path):
    failure = gate.run_check(check("import sys; sys.exit(1)"), tmp_path, 40)
    assert failure is not None and failure.config_error is False


def test_tail_keeps_only_the_last_lines():
    assert gate.tail("\n".join(str(i) for i in range(100)), 3) == "97\n98\n99"


def test_no_checks_means_the_gate_never_runs(tmp_path):
    assert gate.should_run(GateConfig(), tmp_path) is False


def test_empty_watch_runs_on_every_stop(tmp_path):
    cfg = GateConfig(checks=(check("pass"),), watch=())
    assert gate.should_run(cfg, tmp_path) is True


def test_watch_narrows_the_gate_to_changed_files(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    cfg = GateConfig(checks=(check("pass"),), watch=("src/**/*.py",))
    assert gate.should_run(cfg, tmp_path) is False

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1")
    assert gate.should_run(cfg, tmp_path) is True


def test_changed_files_is_empty_outside_a_git_repo(tmp_path):
    assert gate.changed_files(tmp_path) == []


def test_report_names_every_failing_check():
    failures = [gate.Failure("typecheck", "err"), gate.Failure("pytest", "boom")]
    text = gate.report(failures)
    assert "typecheck FAILED" in text and "pytest FAILED" in text
