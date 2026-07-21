"""The normalized event model shared by every provider adapter.

Adapters translate a host CLI's native hook payload into a `HookEvent` and a
`Decision` back into that CLI's native (stdout, exit code) contract. Everything
between those two boundaries is provider-agnostic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class EventType(StrEnum):
    PRE_TOOL_USE = "pre_tool_use"
    STOP = "stop"
    OTHER = "other"


@dataclass(frozen=True)
class HookEvent:
    """One hook invocation, normalized."""

    provider: str
    event: EventType
    session_id: str
    cwd: str
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)
    #: Absent on Copilot's PreToolUse, and nullable on Codex.
    transcript_path: str | None = None
    #: Claude sets this when we are already inside a gate-triggered continuation.
    stop_hook_active: bool = False
    #: Whether `tool_name` mutates files. The adapter decides; handlers stay generic.
    is_write: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """A stable identity for this tool call: name + a hash of its input.

        Two PreToolUse calls collide iff they invoke the same tool with the same
        arguments — exactly the repetition a loop guard watches for. The input is
        hashed so the ledger stays small even when a call carries a whole file.
        """
        try:
            blob = json.dumps(self.tool_input, sort_keys=True, default=str)
        except (TypeError, ValueError):
            blob = repr(self.tool_input)
        digest = hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()[:12]
        return f"{self.tool_name or ''}:{digest}"


Action = Literal["allow", "deny", "block_stop", "noop"]


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str = ""


ALLOW = Decision("allow")
NOOP = Decision("noop")


def deny(reason: str) -> Decision:
    return Decision("deny", reason)


def block_stop(reason: str) -> Decision:
    return Decision("block_stop", reason)
