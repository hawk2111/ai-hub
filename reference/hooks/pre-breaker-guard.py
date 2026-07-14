#!/usr/bin/env python3
"""PreToolUse guard — enforces the circuit breaker on file mutations.

When the breaker is OPEN, Write/Edit/MultiEdit are blocked so an unattended run
drops to read-only and surfaces to a human rather than looping. Reading, running
tests, and `tools/agent/breaker.sh reset` stay available (Bash is not blocked
here), so there is no deadlock.

Contract: exit 2 = block (stderr shown to Claude). exit 0 = allow.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import breaker  # noqa: E402
import _audit  # noqa: E402


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if not breaker.is_open():
        sys.exit(0)

    st = breaker.status()
    tool = data.get("tool_name", "Write") or "Write"
    path = (data.get("tool_input") or {}).get("file_path", "")
    reason = st.get("reason", "open")
    _audit.record(
        event="blocked", tool=tool, detail={"file_path": path},
        decision="deny", session_id=data.get("session_id", ""),
        reason=f"circuit breaker open: {reason}",
    )
    sys.stderr.write(
        f"[guard] BLOCKED: circuit breaker is OPEN ({reason}). "
        "The run is read-only until a human investigates. "
        "Reset with: tools/agent/breaker.sh reset\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
