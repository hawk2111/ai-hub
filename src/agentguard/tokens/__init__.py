"""Token-source registry.

Adding a source — an OpenTelemetry reader, or Codex's `state_5.sqlite` once its
`agent_job_items.tokens_used` column stabilizes — is one class plus one entry here.
"""

from __future__ import annotations

from agentguard.tokens.base import UNKNOWN, Cache, ProviderUsage, TokenSource
from agentguard.tokens.claude import ClaudeTokenSource
from agentguard.tokens.codex import CodexTokenSource
from agentguard.tokens.copilot import CopilotTokenSource
from agentguard.tokens.null import NullTokenSource

__all__ = ["UNKNOWN", "Cache", "ProviderUsage", "TokenSource", "get_source"]

_SOURCES: dict[str, type[TokenSource]] = {
    cls.id: cls
    for cls in (ClaudeTokenSource, CodexTokenSource, CopilotTokenSource, NullTokenSource)
}


def get_source(name: str, provider: str) -> TokenSource:
    """Resolve a configured source name; "auto" follows the provider."""
    key = provider if name == "auto" else name
    return _SOURCES.get(key, NullTokenSource)()
