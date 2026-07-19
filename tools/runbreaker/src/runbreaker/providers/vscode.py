"""GitHub Copilot agent mode inside VS Code.

A different product from Copilot CLI, with a different tool vocabulary — and it
reads *other hosts'* hook files. `chat.hookFilesLocations` defaults to
`.github/hooks`, `.claude/settings.json` and `.claude/settings.local.json`, so the
registrations `runbreaker install` writes for Claude and for Copilot CLI both get
picked up here. Without this adapter the breaker would meter a VS Code session
happily and then deny nothing, because `copilot_createFile` matches neither
`Write` nor `create`. That is why `detect()` lets the tool name override the
baked `--provider` flag.

Tool names taken from `contributes.languageModelTools` in github.copilot-chat.

Exit-code semantics differ again: VS Code treats a non-zero, non-2 exit as a
non-blocking warning (fail-open), and exit 2 as a blocking error shown to the
model. We deny through exit 2 rather than the documented
`hookSpecificOutput.permissionDecision`, because a JSON body an older build does
not understand would fail *open* — and for a safety device, blocking bluntly beats
failing silently.

Two escape hatches remain, both by design and both analogous to Bash elsewhere:
`copilot_runVscodeCommand` and `copilot_runNotebookCell` can execute code that
writes files. `copilot_memory` is deliberately not gated — it both reads and
writes, and the tool name alone cannot tell us which.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.providers.base import ExitCodeAdapter

#: Every VS Code Copilot tool name carries this prefix, which is what makes the
#: override in `detect()` unambiguous.
TOOL_PREFIX = "copilot_"


class VSCodeAdapter(ExitCodeAdapter):
    name: ClassVar[str] = "vscode"
    write_tools: ClassVar[frozenset[str]] = frozenset(
        {
            "copilot_applyPatch",
            "copilot_createDirectory",
            "copilot_createFile",
            "copilot_createNewJupyterNotebook",
            "copilot_editFiles",
            "copilot_editNotebook",
            "copilot_insertEdit",
            "copilot_multiReplaceString",
            "copilot_replaceString",
        }
    )
