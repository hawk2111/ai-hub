"""OpenAI Codex CLI.

`apply_patch` only started firing PreToolUse in openai/codex#18391 (April 2026,
after v0.118); on older builds only `Bash` is gated and file edits slip past the
hook entirely. `runbreaker install` warns when it detects such a build.

`Edit` and `Write` are documented aliases of `apply_patch`.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.providers.base import ExitCodeAdapter


class CodexAdapter(ExitCodeAdapter):
    name: ClassVar[str] = "codex"
    write_tools: ClassVar[frozenset[str]] = frozenset({"apply_patch", "Edit", "Write"})
