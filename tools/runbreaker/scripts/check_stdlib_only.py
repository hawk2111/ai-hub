#!/usr/bin/env python3
"""Fail if anything under src/runbreaker imports outside the standard library.

Hook processes are spawned by the host CLI as a bare `python3`, with no venv
active. A third-party import there would raise ImportError, and on Copilot CLI a
non-zero exit means *deny* — one stray dependency would lock users out of every
tool. This check is cheap; the failure mode is not.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src" / "runbreaker"
ALLOWED = set(sys.stdlib_module_names) | {"runbreaker"}


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def main() -> int:
    offenders: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for root in sorted(imported_roots(tree) - ALLOWED):
            offenders.append(f"{path.relative_to(ROOT.parent.parent)}: {root}")

    if offenders:
        print("non-stdlib imports reachable from a hook process:")
        print("\n".join(f"  {line}" for line in offenders))
        return 1
    print(f"ok — {len(list(ROOT.rglob('*.py')))} modules, stdlib only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
