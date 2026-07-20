"""Copilot in VS Code: best-effort, like Copilot CLI, and usually unknown.

VS Code's agent hooks hand a `transcript_path` to the **Stop** payload (not to
PreToolUse — see the provider adapter), and its format is documented as "not a
stable hook API". VS Code *does* record token usage (`promptTokens`,
`completionTokens`) — but only in the opt-in agent debug log
(`github.copilot.chat.agentDebugLog.fileLogging.enabled`), whose path is never
handed to a hook. So in practice this abstains far more often than not, and a VS
Code session is guarded by the provider-agnostic conditions (`step_budget`,
`time_budget`, `repeat_loop`, `gate_failures`).

It is wired up anyway, and shares Copilot CLI's reader: the day a VS Code build
puts recognizable usage into the transcript it hands us, `token_budget` and
`cost_budget` start working with no further change. Until then it returns `None`
(unknown) — never a guessed number.
"""

from __future__ import annotations

from typing import ClassVar

from runbreaker.tokens.copilot import CopilotTokenSource


class VSCodeTokenSource(CopilotTokenSource):
    id: ClassVar[str] = "vscode"
