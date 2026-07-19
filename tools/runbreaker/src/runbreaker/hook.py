"""The hook entrypoint. Every provider's hook command lands here.

One invariant governs this file: **an exception must exit 0.**

Claude treats a non-zero, non-2 exit as a harmless error and proceeds. Copilot
treats it as a *deny* — every non-zero exit other than 2 is fail-closed. So a
crash in runbreaker would not merely fail to guard a Copilot run, it would lock
the user out of every tool. Exit 2 is reserved for a decision we deliberately
made; anything unexpected exits 0 and lets the agent through.

Note the argv parsing is hand-rolled: `argparse` exits 2 on a bad flag, which is
precisely the code that means "deny".
"""

from __future__ import annotations

import json
import sys
from typing import Any

from runbreaker import config
from runbreaker.handlers import build_runtime, route
from runbreaker.providers import detect

EXIT_OK = 0
EXIT_BLOCK = 2


def _flag(argv: list[str], name: str) -> str | None:
    prefix = f"--{name}"
    for i, arg in enumerate(argv):
        if arg == prefix and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(f"{prefix}="):
            return arg.split("=", 1)[1]
    return None


def _read_payload() -> dict[str, Any]:
    raw = json.load(sys.stdin)
    return raw if isinstance(raw, dict) else {}


def main(argv: list[str] | None = None) -> None:
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        raw = _read_payload()
        adapter = detect(raw, _flag(args, "provider"))
        event = adapter.parse(raw, _flag(args, "event"))

        cfg = config.load()
        decision = route(event, build_runtime(cfg))
        stdout, stderr, code = adapter.emit(decision, event)

        if stdout:
            sys.stdout.write(stdout)
        if stderr:
            sys.stderr.write(stderr + "\n")
        sys.exit(code)
    except SystemExit:
        raise
    except BaseException:
        sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
