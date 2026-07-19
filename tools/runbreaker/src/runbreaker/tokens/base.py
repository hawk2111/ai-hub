"""Reading token usage out of a provider, without lying when we cannot.

The prototype's `sum_tokens()` returned `0` on any parse error. `0` means "this
run consumed nothing", so a renamed field in a provider's session file silently
disabled the token budget. Here, `None` means *unknown* and `0` means *actually
zero* — and the `token_budget` condition abstains on unknown rather than
trusting a number it did not get.

Providers move their token fields around (Codex alone writes them to a rollout
JSONL *and* to `state_5.sqlite`), so each source declares an ordered list of
candidate paths and takes the first hit. A schema change degrades to `None`, not
to a wrong answer.

Session files are append-only, and they grow. `incremental_sum` remembers a byte
offset and a running total in the budget ledger, so a re-read costs only the bytes
written since the last one — otherwise every fifth tool call would re-parse the
entire transcript while holding the state lock.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from runbreaker.events import HookEvent

#: Refuse to read more than this in one go. Reached only on a first read of an
#: enormous file; we answer "unknown" rather than sum a prefix and call it a total.
MAX_DELTA_BYTES = 64 * 1024 * 1024

#: Path cache and resume state, carried across hook invocations in the budget ledger.
Cache = MutableMapping[str, Any]

#: Pull the token count out of one record, or None if it carries none.
Extract = Callable[[dict[str, Any]], int | None]


@dataclass(frozen=True)
class ProviderUsage:
    """What we managed to learn. `None` fields mean "could not tell"."""

    total_tokens: int | None = None
    rate_limit_percent: float | None = None


UNKNOWN = ProviderUsage()


class TokenSource(ABC):
    id: ClassVar[str]

    @abstractmethod
    def read(self, event: HookEvent, cache: Cache) -> ProviderUsage:
        """Best-effort usage for this session. Must never raise."""


def dig(obj: Any, path: str) -> Any:
    """Walk a dotted path, returning None if any hop is missing."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def first_hit(obj: Any, paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = dig(obj, path)
        if value is not None:
            return value
    return None


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def incremental_sum(path: Path, cache: Cache, namespace: str, extract: Extract) -> int | None:
    """Sum `extract` over an append-only JSONL, resuming from the last byte offset.

    Returns None when nothing recognizable was ever found, when the file is
    unreadable, or when the unread tail is too large to trust — never a partial
    total dressed up as a complete one.
    """
    off_key, total_key, seen_key = (f"{namespace}_{s}" for s in ("offset", "total", "seen"))
    try:
        size = path.stat().st_size
    except OSError:
        return None

    offset = as_int(cache.get(off_key)) or 0
    total = as_int(cache.get(total_key)) or 0
    seen = bool(cache.get(seen_key))

    if size < offset:  # rotated or truncated underneath us
        offset, total, seen = 0, 0, False

    if size - offset > MAX_DELTA_BYTES:
        return None

    if size > offset:
        try:
            with path.open("rb") as fh:
                fh.seek(offset)
                blob = fh.read(size - offset)
        except OSError:
            return None

        # Stop at the last newline: a live session file's final line is often
        # half-written, and re-reading it next time is the whole point of the offset.
        consumed = blob.rfind(b"\n") + 1
        for raw in blob[:consumed].splitlines():
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            value = extract(record)
            if value is None:
                continue
            seen = True
            total += value

        offset += consumed
        cache[off_key], cache[total_key], cache[seen_key] = offset, total, seen

    return total if seen else None


def iter_tail_json_lines(path: Path, max_bytes: int = 1024 * 1024) -> Iterator[dict[str, Any]]:
    """Parsed JSONL records from the last `max_bytes` of a file.

    For a cumulative counter the newest record is all that matters. The first
    (likely partial) line of the window is dropped.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # discard the partial line we landed in
            blob = fh.read()
    except OSError:
        return
    for raw in blob.decode("utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj
