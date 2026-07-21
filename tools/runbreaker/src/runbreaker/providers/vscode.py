"""GitHub Copilot agent mode inside VS Code.

A different product from Copilot CLI, with a different tool vocabulary — and it
reads *other hosts'* hook files. `chat.hookFilesLocations` defaults to
`.github/hooks`, `.claude/settings.json` and `.claude/settings.local.json`, so the
registrations `runbreaker install` writes for Claude and for Copilot CLI both get
picked up here. Without this adapter the breaker would meter a VS Code session
happily and then deny nothing, because a VS Code write tool matches neither
`Write` nor `create`. That is why `detect()` routes VS Code by its tool names,
overriding the baked `--provider` flag.

**Two tool-naming schemes, on purpose.** The tools were originally contributed as
`copilot_createFile`, `copilot_applyPatch`, … but that prefix is being retired —
vscode-copilot-chat's `ToolName` enum now uses prefix-less snake_case
(`create_file`, `apply_patch`, `replace_string_in_file`, …), and its docs say "the
existing `copilot_` tools will be renamed later". Which form reaches a hook has
been a moving target, so we gate on **both**: a stale allowlist here would let a
renamed write tool slip straight past the breaker — exactly the fail-open this
tool exists to prevent.

Exit-code semantics differ again: VS Code treats a non-zero, non-2 exit as a
non-blocking warning (fail-open), and exit 2 as a blocking error shown to the
model. We deny through exit 2 rather than the documented
`hookSpecificOutput.permissionDecision`, because a JSON body an older build does
not understand would fail *open* — and for a safety device, blocking bluntly beats
failing silently.

Escape hatches remain, by design and analogous to Bash elsewhere:
`copilot_runVscodeCommand` / `run_in_terminal` and the notebook-cell runners can
execute code that writes files. `copilot_memory` is deliberately not gated — it
both reads and writes, and the tool name alone cannot tell us which.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.providers.base import ExitCodeAdapter

#: The original contribution prefix. Still matched so an older build keeps working.
TOOL_PREFIX = "copilot_"

#: Legacy `copilot_*` write tools (contributes.languageModelTools names).
_OLD_WRITE_TOOLS = frozenset(
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

#: Current prefix-less write tools (vscode-copilot-chat ToolName enum). These reach
#: a hook through a Claude/Copilot shim, and neither of those hosts emits them, so
#: they also serve as the VS Code fingerprint in `detect()`.
_NEW_WRITE_TOOLS = frozenset(
    {
        "apply_patch",
        "create_directory",
        "create_file",
        "create_new_jupyter_notebook",
        "edit_files",
        "edit_notebook_file",
        "insert_edit_into_file",
        "multi_replace_string_in_file",
        "replace_string_in_file",
    }
)

#: Prefix-less tool names that unambiguously mark a VS Code session when they arrive
#: through a Claude or Copilot shim (those CLIs never emit them). Used by `detect()`.
#: `apply_patch` is Codex's tool too, but Codex is routed by its own baked provider,
#: never reused by VS Code — so `detect()` only consults this under claude/copilot.
NEW_TOOL_NAMES = _NEW_WRITE_TOOLS


class VSCodeAdapter(ExitCodeAdapter):
    name: ClassVar[str] = "vscode"
    write_tools: ClassVar[frozenset[str]] = _OLD_WRITE_TOOLS | _NEW_WRITE_TOOLS
