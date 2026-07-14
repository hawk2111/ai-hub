"""The quality gate: run configured checks before the agent is allowed to finish.

Entirely config-driven. No stack is assumed, no command is hardcoded, and with no
`[[gate.checks]]` configured the Stop hook is a no-op. A red gate hands the failure
back to the agent so it fixes its own mess instead of leaving broken code behind.

`watch` narrows the gate to runs that touched files you care about; an empty
`watch` runs the checks on every stop.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agentguard.config import GateCheck, GateConfig

DEFAULT_TIMEOUT = 600


@dataclass(frozen=True)
class Failure:
    name: str
    output: str
    #: The check could not be launched at all — a bad `command` in the config, not
    #: a defect in the code under test. These never count toward `gate_failures`,
    #: or a typo would open the breaker after three turns. This is easy to hit on
    #: Windows, where `npm` is `npm.cmd` and `subprocess` will not find it.
    config_error: bool = False


@lru_cache(maxsize=128)
def _compile(pattern: str) -> re.Pattern[str]:
    """Translate a glob to a regex. `**` crosses directories, `*` does not."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_compile(p).match(path) for p in patterns)


def changed_files(project: Path) -> list[str]:
    """Tracked modifications plus untracked files. Empty when git is unavailable."""
    files: list[str] = []
    for args in (
        ["diff", "--name-only", "HEAD"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        try:
            out = subprocess.check_output(
                ["git", "-C", str(project), *args],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        files.extend(line for line in out.splitlines() if line)
    return files


def should_run(gate: GateConfig, project: Path) -> bool:
    if not gate.enabled:
        return False
    if not gate.watch:
        return True
    return any(matches(f, gate.watch) for f in changed_files(project))


def tail(text: str, lines: int) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def run_check(check: GateCheck, project: Path, tail_lines: int) -> Failure | None:
    try:
        proc = subprocess.run(
            check.command,
            cwd=project,
            text=True,
            capture_output=True,
            timeout=check.timeout or DEFAULT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return Failure(check.name, f"command not found: {check.command[0]}", config_error=True)
    except subprocess.TimeoutExpired:
        return Failure(check.name, f"timed out after {check.timeout or DEFAULT_TIMEOUT}s")
    except OSError as exc:
        return Failure(check.name, f"could not run: {exc}", config_error=True)

    if proc.returncode == 0:
        return None
    return Failure(check.name, tail((proc.stdout or "") + (proc.stderr or ""), tail_lines))


def run(gate: GateConfig, project: Path) -> list[Failure]:
    failures = []
    for check in gate.checks:
        failure = run_check(check, project, gate.tail_lines)
        if failure is not None:
            failures.append(failure)
    return failures


def report(failures: list[Failure]) -> str:
    body = "\n\n".join(f"### {f.name} FAILED\n{f.output}" for f in failures)
    return f"[agentguard] quality gate is red — fix before finishing:\n\n{body}"
