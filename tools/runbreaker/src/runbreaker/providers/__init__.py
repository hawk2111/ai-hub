"""Provider registry and detection.

The installer bakes `--provider` into every hook command, so detection is normally
a lookup. Sniffing exists only so a hand-written config degrades gracefully instead
of misrouting: under PascalCase payloads all three CLIs look alike, and Claude and
Codex are genuinely indistinguishable from the payload alone.

One case overrides the flag. VS Code's agent mode reads `.claude/settings.json` and
`.github/hooks/*.json` — hook files we installed for *other* hosts — so a shim baked
with `--provider claude` can find itself handling a VS Code tool call. VS Code's tool
names all start with `copilot_`, which no other host emits, so the payload wins there.
"""

from __future__ import annotations

import os
from typing import Any

from runbreaker.providers.base import EmitResult, ProviderAdapter
from runbreaker.providers.claude import ClaudeAdapter
from runbreaker.providers.codex import CodexAdapter
from runbreaker.providers.copilot import CopilotAdapter
from runbreaker.providers.vscode import TOOL_PREFIX, VSCodeAdapter

__all__ = [
    "ADAPTERS",
    "ClaudeAdapter",
    "CodexAdapter",
    "CopilotAdapter",
    "EmitResult",
    "ProviderAdapter",
    "VSCodeAdapter",
    "detect",
]

ADAPTERS: dict[str, type[ProviderAdapter]] = {
    cls.name: cls for cls in (ClaudeAdapter, CodexAdapter, CopilotAdapter, VSCodeAdapter)
}


def detect(raw: dict[str, Any], provider: str | None = None) -> ProviderAdapter:
    tool = raw.get("tool_name") or raw.get("toolName")
    if isinstance(tool, str) and tool.startswith(TOOL_PREFIX):
        return VSCodeAdapter()
    if provider and provider in ADAPTERS:
        return ADAPTERS[provider]()
    if "toolName" in raw or "toolArgs" in raw:
        return CopilotAdapter()
    if "turn_id" in raw or "tool_use_id" in raw or os.environ.get("CODEX_HOME"):
        return CodexAdapter()
    return ClaudeAdapter()
