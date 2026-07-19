"""Claude Code.

Exit 2 blocks a PreToolUse call and blocks a Stop, feeding stderr back to the
model in both cases. Any other non-zero exit is a non-blocking error — Claude
fails open.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.providers.base import ExitCodeAdapter


class ClaudeAdapter(ExitCodeAdapter):
    name: ClassVar[str] = "claude"
    write_tools: ClassVar[frozenset[str]] = frozenset(
        {"Write", "Edit", "MultiEdit", "NotebookEdit"}
    )
