"""GitHub Copilot CLI.

Register the hooks under PascalCase event names (`PreToolUse`, `Stop`) and Copilot
delivers VS Code-compatible snake_case payloads, identical in shape to Claude's.
The camelCase spellings (`toolName`, `toolArgs`) still parse, as a fallback for
hand-written configs.

Exit-code semantics are inverted relative to Claude: exit 2 denies, and so does
*any* other non-zero exit. Only a timeout fails open. That is why `runbreaker`
never lets an internal exception reach the exit path — a bug here would lock the
user out of every tool.

`agentStop` is documented to take `{"decision": "block", "reason": ...}` where the
reason becomes the next turn's prompt. Exit 2 is only spelled out for `preToolUse`,
so block-stop goes through the JSON body.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.events import Decision, HookEvent
from runbreaker.providers.base import EmitResult, ProviderAdapter, block_stop_json


class CopilotAdapter(ProviderAdapter):
    name: ClassVar[str] = "copilot"
    write_tools: ClassVar[frozenset[str]] = frozenset({"create", "edit"})

    def emit(self, decision: Decision, event: HookEvent) -> EmitResult:
        if decision.action == "deny":
            return None, decision.reason, 2
        if decision.action == "block_stop":
            return block_stop_json(decision.reason), None, 0
        return None, None, 0
