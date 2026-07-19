"""Atomicity, locking, and the corrupt-file policy."""

from __future__ import annotations

import json
import threading

from runbreaker.breaker import Breaker
from runbreaker.budget import Budget
from runbreaker.state import SCHEMA_VERSION, VERSION_KEY, Store


def test_write_is_atomic_and_versioned(tmp_path):
    store = Store(tmp_path)
    with store.update("thing", dict) as draft:
        draft.data["a"] = 1
    on_disk = json.loads((tmp_path / "thing.json").read_text())
    assert on_disk == {VERSION_KEY: SCHEMA_VERSION, "a": 1}
    assert not list(tmp_path.glob("*.tmp")), "no temp file should survive"


def test_missing_and_corrupt_are_distinguishable(tmp_path):
    store = Store(tmp_path)
    assert store.read("thing", dict).missing is True

    (tmp_path / "thing.json").write_text("{not json")
    draft = store.read("thing", dict)
    assert (draft.missing, draft.corrupt) == (False, True)


def test_a_foreign_schema_version_reads_as_corrupt(tmp_path):
    (tmp_path / "thing.json").write_text(json.dumps({VERSION_KEY: 999, "a": 1}))
    assert Store(tmp_path).read("thing", dict).corrupt is True


def test_concurrent_writers_lose_no_increments(tmp_path):
    store = Store(tmp_path)
    workers, per_worker = 8, 25

    def bump() -> None:
        for _ in range(per_worker):
            with store.update("counter", lambda: {"n": 0}) as draft:
                draft.data["n"] = int(draft.data["n"]) + 1

    threads = [threading.Thread(target=bump) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert store.read("counter", lambda: {"n": 0}).data["n"] == workers * per_worker


def test_write_false_skips_persisting(tmp_path):
    store = Store(tmp_path)
    with store.update("thing", dict) as draft:
        draft.data["a"] = 1
        draft.write = False
    assert not (tmp_path / "thing.json").exists()


# -- corrupt-file policy ----------------------------------------------------


def test_corrupt_breaker_reads_as_open(tmp_path):
    (tmp_path / "breaker.json").write_text('{"state": "clo')
    status = Breaker(Store(tmp_path)).status()
    assert status.is_open and status.corrupt
    assert "state unreadable" in status.reason


def test_missing_breaker_reads_as_closed(tmp_path):
    assert Breaker(Store(tmp_path)).status().is_open is False


def test_reset_recovers_a_corrupt_breaker(tmp_path):
    (tmp_path / "breaker.json").write_text('{"state": "clo')
    breaker = Breaker(Store(tmp_path))
    breaker.reset()
    assert breaker.status().is_open is False


def test_corrupt_budget_starts_fresh_rather_than_failing_closed(tmp_path, project):
    from tests.conftest import make_event

    from runbreaker.tokens.null import NullTokenSource

    (tmp_path / "budget.json").write_text("{ truncated")
    budget = Budget(Store(tmp_path))
    assert budget.tick(make_event(), NullTokenSource()).steps == 1


# -- breaker semantics ------------------------------------------------------


def test_record_success_clears_fails_but_never_closes(tmp_path):
    breaker = Breaker(Store(tmp_path))
    assert breaker.record_fail() == 1
    assert breaker.record_fail() == 2
    breaker.record_success()
    assert breaker.status().fails == 0

    breaker.trip("boom")
    breaker.record_success()
    assert breaker.status().is_open, "closing an open breaker is a human decision"


def test_record_fail_is_a_noop_once_open(tmp_path):
    breaker = Breaker(Store(tmp_path))
    breaker.trip("boom")
    assert breaker.record_fail() == 0


# -- budget -----------------------------------------------------------------


def test_unknown_usage_does_not_erase_a_known_total(tmp_path, project):
    from tests.conftest import make_event

    from runbreaker.tokens.base import UNKNOWN, ProviderUsage, TokenSource

    class Flaky(TokenSource):
        id = "flaky"

        def __init__(self) -> None:
            self.calls = 0

        def read(self, event, cache):
            self.calls += 1
            return ProviderUsage(total_tokens=500) if self.calls == 1 else UNKNOWN

    budget = Budget(Store(tmp_path))
    source = Flaky()
    assert budget.tick(make_event(), source, recompute_every=1).tokens == 500
    assert budget.tick(make_event(), source, recompute_every=1).tokens == 500


def test_gc_drops_sessions_beyond_the_cap(tmp_path, project):
    from tests.conftest import make_event

    from runbreaker.tokens.null import NullTokenSource

    budget = Budget(Store(tmp_path))
    for i in range(5):
        budget.tick(make_event(session_id=f"s{i}"), NullTokenSource(), max_sessions=3)
    assert len(budget.all_sessions()) == 3
