"""A source that always answers "unknown" — for providers we cannot meter."""

from __future__ import annotations

from typing import ClassVar

from agentguard.events import HookEvent
from agentguard.tokens.base import UNKNOWN, Cache, ProviderUsage, TokenSource


class NullTokenSource(TokenSource):
    id: ClassVar[str] = "null"

    def read(self, event: HookEvent, cache: Cache) -> ProviderUsage:
        return UNKNOWN
