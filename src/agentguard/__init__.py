"""agentguard — circuit breaker and budget guard for unattended AI coding agents.

Kept import-light on purpose: this module is imported by every hook process, which
the host CLI spawns once per tool call.
"""

__version__ = "0.1.0"
