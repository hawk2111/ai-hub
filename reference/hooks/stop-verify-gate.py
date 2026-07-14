#!/usr/bin/env python3
"""Stop / SubagentStop verification gate.

When the agent tries to finish a turn that changed TypeScript source, run the
fast quality gate. If it is red, block the stop and hand the failure back so the
agent fixes it autonomously instead of leaving broken code (the "evidence
threshold" / process fallback). This is what lets human-in-the-loop drop without
losing the Constitution's quality bar.

Contract: exit 2 = block stop, stderr returned to Claude. exit 0 = allow stop.

By default only `typecheck` runs (fastest meaningful signal). Opt in to more:
  KAI_GATE_LINT=1   also run `npm run lint:ci`
  KAI_GATE_TESTS=1  also run `npm run test`
  KAI_SKIP_GATE=1   skip the gate entirely

`stop_hook_active` in the payload means we are already inside a gate-triggered
continuation — exit 0 then to avoid an infinite stop loop.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import breaker  # noqa: E402

PROJ = os.path.abspath(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
WATCHED_PREFIXES = ("src/", "bff/src/", "tools/")
TAIL_LINES = 40


def changed_ts() -> bool:
    files = []
    try:
        tracked = subprocess.check_output(
            ["git", "-C", PROJ, "diff", "--name-only", "HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        )
        files += tracked.splitlines()
    except Exception:
        pass
    try:
        untracked = subprocess.check_output(
            ["git", "-C", PROJ, "ls-files", "--others", "--exclude-standard"],
            text=True, stderr=subprocess.DEVNULL,
        )
        files += untracked.splitlines()
    except Exception:
        pass
    return any(
        f.endswith((".ts", ".tsx")) and f.startswith(WATCHED_PREFIXES)
        for f in files
    )


def run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=PROJ, text=True, capture_output=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-TAIL_LINES:])


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)
    if os.environ.get("KAI_SKIP_GATE") == "1":
        sys.exit(0)
    if not changed_ts():
        sys.exit(0)

    checks = [("typecheck", ["npm", "run", "--silent", "typecheck"])]
    if os.environ.get("KAI_GATE_LINT") == "1":
        checks.append(("lint:ci", ["npm", "run", "--silent", "lint:ci"]))
    if os.environ.get("KAI_GATE_TESTS") == "1":
        checks.append(("test", ["npm", "run", "--silent", "test"]))

    failures = []
    for label, cmd in checks:
        code, out = run(cmd)
        if code != 0:
            failures.append(f"### {label} FAILED\n{tail(out)}")

    # Coverage gate is opt-in; --changed enforces ≥80% on new/changed code
    # (Principle II). exit 2 (tooling unavailable) is a non-blocking note.
    if os.environ.get("KAI_GATE_COVERAGE") == "1":
        code, out = run(["bash", os.path.join(PROJ, "tools", "agent", "coverage-gate.sh"), "--changed"])
        if code == 1:
            failures.append(f"### coverage FAILED\n{tail(out)}")
        elif code == 2:
            sys.stderr.write("[verify-gate] coverage gate UNAVAILABLE (tooling not installed)\n")

    if failures:
        state = breaker.record_fail()
        note = ""
        if state.get("state") == "open":
            note = ("\n[circuit-breaker] OPEN — too many consecutive failed gates. "
                    "Stop and surface to a human; reset with tools/agent/breaker.sh reset.\n")
        sys.stderr.write(
            "[verify-gate] quality gate is red — fix before finishing:\n\n"
            + "\n\n".join(failures)
            + "\n" + note
        )
        sys.exit(2)

    breaker.record_success()
    sys.exit(0)


if __name__ == "__main__":
    main()
