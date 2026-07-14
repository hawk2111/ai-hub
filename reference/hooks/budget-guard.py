#!/usr/bin/env python3
"""PreToolUse budget meter — enforces per-run step/token limits (Principle VII).

Counts every tool call for the session. When the step or token budget is
exceeded it trips the Phase-5 circuit breaker, so the run degrades to read-only
(writes blocked by pre-breaker-guard) and surfaces to a human — instead of
looping or burning unbounded tokens. This guard never blocks a call itself; the
breaker is the single, consistent enforcement point.

Budgets (env):
  KAI_MAX_STEPS=N    tool-call budget per session (default 250; 0 disables)
  KAI_MAX_TOKENS=N   cumulative cost-bearing token budget (0 disables). Shipped
                     default lives in `.claude/settings.json` "env" (a generous
                     runaway-only ceiling); the unattended launcher sets a
                     tighter per-run value.
  KAI_SKIP_BUDGET=1  disable this meter entirely

The token total counts input + output + cache-creation tokens. Cache *reads*
are deliberately excluded: at ~10% of input price and re-counted every turn they
inflate the total and make a token budget meaningless — the fresh-token total is
the runaway signal. Totals are read from the session transcript (throttled to
every 5th step to bound cost). For precise dollar spend, pair with OpenTelemetry;
this is the in-loop safety net.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _budget  # noqa: E402
import _audit  # noqa: E402
import breaker  # noqa: E402


def sum_tokens(transcript_path: str) -> int:
    """Cumulative cost-bearing tokens: input + output + cache-creation. Cache
    reads are excluded on purpose (cheap + re-counted every turn → inflation)."""
    total = 0
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                usage = (obj.get("message") or {}).get("usage") or {}
                total += (int(usage.get("input_tokens", 0))
                          + int(usage.get("output_tokens", 0))
                          + int(usage.get("cache_creation_input_tokens", 0)))
    except Exception:
        return 0
    return total


def main() -> None:
    if os.environ.get("KAI_SKIP_BUDGET") == "1":
        sys.exit(0)
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    session = data.get("session_id", "") or "unknown"
    max_steps = int(os.environ.get("KAI_MAX_STEPS", "250") or 0)
    max_tokens = int(os.environ.get("KAI_MAX_TOKENS", "0") or 0)

    # Peek current step count to decide whether to recompute tokens this tick.
    steps_so_far = _budget.status(session).get("steps", 0)
    tokens = None
    if max_tokens and (steps_so_far % 5 == 0):
        tp = data.get("transcript_path", "")
        if tp:
            tokens = sum_tokens(tp)

    steps, tok = _budget.tick(session, tokens)

    over_steps = max_steps and steps > max_steps
    over_tokens = max_tokens and tok > max_tokens
    if (over_steps or over_tokens) and not breaker.is_open():
        reason = (f"budget exceeded: steps={steps}/{max_steps or 'off'}, "
                  f"tokens={tok}/{max_tokens or 'off'}")
        breaker.trip(reason)
        _audit.record(event="budget", tool="(meter)", detail={"steps": steps, "tokens": tok},
                      decision="tripped", session_id=session, reason=reason)

    sys.exit(0)


if __name__ == "__main__":
    main()
