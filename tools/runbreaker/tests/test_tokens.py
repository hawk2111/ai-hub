"""Token parsing must answer "unknown", never a confident zero.

Fixtures mirror the real files on disk: Claude reports usage per message (sum it),
Codex reports cumulative totals (take the last one).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import make_event

from runbreaker.events import EventType
from runbreaker.tokens import get_source
from runbreaker.tokens.claude import ClaudeTokenSource
from runbreaker.tokens.codex import CodexTokenSource
from runbreaker.tokens.copilot import CopilotTokenSource


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def claude_message(inp: int, out: int, cache_create: int, cache_read: int = 1000) -> dict:
    return {
        "message": {
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_creation_input_tokens": cache_create,
                "cache_read_input_tokens": cache_read,
            }
        }
    }


def codex_token_count(total: int, used_percent: float | None = None) -> dict:
    payload = {
        "type": "token_count",
        "info": {
            "total_token_usage": {
                "input_tokens": total - 10,
                "cached_input_tokens": 5,
                "output_tokens": 10,
                "reasoning_output_tokens": 0,
                "total_tokens": total,
            }
        },
    }
    if used_percent is not None:
        payload["rate_limits"] = {"primary": {"used_percent": used_percent}}
    return {"type": "event_msg", "payload": payload}


# -- Claude -----------------------------------------------------------------


def test_claude_sums_per_message_and_ignores_cache_reads(tmp_path):
    path = write_jsonl(tmp_path / "t.jsonl", [claude_message(10, 5, 2), claude_message(3, 1, 0)])
    usage = ClaudeTokenSource().read(make_event(transcript_path=str(path)), {})
    assert usage.total_tokens == 21  # (10+5+2) + (3+1+0); the 2000 cache reads are excluded


def test_claude_without_usage_is_unknown_not_zero(tmp_path):
    path = write_jsonl(tmp_path / "t.jsonl", [{"type": "user"}, {"type": "summary"}])
    assert ClaudeTokenSource().read(make_event(transcript_path=str(path)), {}).total_tokens is None


def test_claude_skips_a_half_written_final_line(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(claude_message(10, 5, 2)) + '\n{"message": {"usa', encoding="utf-8")
    assert ClaudeTokenSource().read(make_event(transcript_path=str(path)), {}).total_tokens == 17


def test_claude_missing_transcript_is_unknown():
    assert ClaudeTokenSource().read(make_event(transcript_path=None), {}).total_tokens is None


def test_claude_nonexistent_transcript_is_unknown(tmp_path):
    event = make_event(transcript_path=str(tmp_path / "gone.jsonl"))
    assert ClaudeTokenSource().read(event, {}).total_tokens is None


def test_claude_genuinely_zero_is_zero_not_unknown(tmp_path):
    path = write_jsonl(tmp_path / "t.jsonl", [claude_message(0, 0, 0)])
    assert ClaudeTokenSource().read(make_event(transcript_path=str(path)), {}).total_tokens == 0


# -- Codex ------------------------------------------------------------------


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    (home / "sessions" / "2026" / "05" / "21").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def rollout(codex_home: Path, session_id: str, records: list[dict]) -> Path:
    # Real filenames put the timestamp *before* the session id.
    name = f"rollout-2026-05-21T21-18-45-{session_id}.jsonl"
    return write_jsonl(codex_home / "sessions" / "2026" / "05" / "21" / name, records)


def test_codex_takes_the_last_cumulative_total(codex_home):
    rollout(codex_home, "s1", [codex_token_count(100), codex_token_count(2680003, 9.0)])
    usage = CodexTokenSource().read(make_event(provider="codex", session_id="s1"), {})
    assert usage.total_tokens == 2680003, "cumulative — summing would multiply-count"
    assert usage.rate_limit_percent == 9.0


def test_codex_renamed_field_is_unknown_not_zero(codex_home):
    drifted = {"type": "event_msg", "payload": {"type": "token_count", "info": {"tokens": 999}}}
    rollout(codex_home, "s1", [drifted])
    usage = CodexTokenSource().read(make_event(provider="codex", session_id="s1"), {})
    assert usage.total_tokens is None, "a schema change must not read as zero consumption"


def test_codex_unlocatable_session_is_unknown(codex_home):
    usage = CodexTokenSource().read(make_event(provider="codex", session_id="nope"), {})
    assert usage.total_tokens is None


def test_codex_caches_the_resolved_rollout_path(codex_home):
    path = rollout(codex_home, "s1", [codex_token_count(42)])
    cache: dict[str, str] = {}
    CodexTokenSource().read(make_event(provider="codex", session_id="s1"), cache)
    assert cache["rollout_path"] == str(path)


def test_codex_without_rate_limits_abstains(codex_home):
    rollout(codex_home, "s1", [codex_token_count(42)])
    usage = CodexTokenSource().read(make_event(provider="codex", session_id="s1"), {})
    assert usage.rate_limit_percent is None


# -- Copilot ----------------------------------------------------------------


def test_copilot_without_a_transcript_is_unknown():
    event = make_event(provider="copilot", event=EventType.PRE_TOOL_USE)
    assert CopilotTokenSource().read(event, {}).total_tokens is None


def test_copilot_reuses_the_transcript_path_cached_at_stop(tmp_path):
    path = write_jsonl(tmp_path / "t.jsonl", [{"usage": {"total_tokens": 77}}])
    cache = {"transcript_path": str(path)}
    event = make_event(provider="copilot", transcript_path=None)
    assert CopilotTokenSource().read(event, cache).total_tokens == 77


# -- registry ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("claude", "claude"), ("codex", "codex"), ("copilot", "copilot"), ("mystery", "null")],
)
def test_auto_resolves_by_provider(provider, expected):
    assert get_source("auto", provider).id == expected


def test_explicit_source_overrides_the_provider():
    assert get_source("null", "claude").id == "null"
