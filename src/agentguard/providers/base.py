"""Provider adapters: the only place that knows a host CLI's dialect.

All three CLIs can be made to speak snake_case — Copilot does so when its hooks
are registered under PascalCase event names — so parsing is shared. What genuinely
differs is the *output* contract, and in one case dangerously so: Copilot treats
any non-zero exit other than 2 as a **deny**, where Claude treats it as a
non-blocking error. A crash in our code must therefore never reach the exit path.

`deny` is emitted as exit 2 + stderr everywhere, because that is the one mechanism
all three document identically. Richer JSON bodies are used only where a provider
needs them.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from agentguard.events import Decision, EventType, HookEvent

#: (stdout, stderr, exit_code)
EmitResult = tuple[str | None, str | None, int]

_STOP_EVENT_NAMES = frozenset({"Stop", "SubagentStop", "agentStop", "subagentStop"})
_PRE_EVENT_NAMES = frozenset({"PreToolUse", "preToolUse"})


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            return value
    return None


class ProviderAdapter(ABC):
    name: ClassVar[str]
    write_tools: ClassVar[frozenset[str]]

    def parse(self, raw: dict[str, Any], event_hint: str | None = None) -> HookEvent:
        tool_name = _first(raw, "tool_name", "toolName")
        tool_input = _first(raw, "tool_input", "toolArgs")
        transcript = _first(raw, "transcript_path", "transcriptPath")
        return HookEvent(
            provider=self.name,
            event=self._event_type(raw, event_hint, tool_name),
            session_id=str(_first(raw, "session_id", "sessionId") or ""),
            cwd=str(raw.get("cwd") or ""),
            tool_name=str(tool_name) if tool_name is not None else None,
            tool_input=tool_input if isinstance(tool_input, dict) else {},
            transcript_path=str(transcript) if transcript else None,
            stop_hook_active=bool(raw.get("stop_hook_active")),
            is_write=bool(tool_name) and str(tool_name) in self.write_tools,
            raw=raw,
        )

    @staticmethod
    def _event_type(raw: dict[str, Any], hint: str | None, tool_name: Any) -> EventType:
        if hint:
            try:
                return EventType(hint)
            except ValueError:
                pass
        name = str(_first(raw, "hook_event_name", "hookEventName") or "")
        if name in _STOP_EVENT_NAMES:
            return EventType.STOP
        if name in _PRE_EVENT_NAMES:
            return EventType.PRE_TOOL_USE
        if tool_name:
            return EventType.PRE_TOOL_USE
        return EventType.OTHER

    @abstractmethod
    def emit(self, decision: Decision, event: HookEvent) -> EmitResult:
        """Render a decision into this CLI's (stdout, stderr, exit code) contract."""


class ExitCodeAdapter(ProviderAdapter):
    """For CLIs where exit 2 + stderr covers both deny and block-stop."""

    def emit(self, decision: Decision, event: HookEvent) -> EmitResult:
        if decision.action in ("deny", "block_stop"):
            return None, decision.reason, 2
        return None, None, 0


def block_stop_json(reason: str) -> str:
    return json.dumps({"decision": "block", "reason": reason})
