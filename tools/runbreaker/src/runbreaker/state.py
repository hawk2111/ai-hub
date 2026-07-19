"""Crash-safe, concurrency-safe JSON state.

Hook processes run concurrently — parallel tool calls and subagents all spawn
their own interpreter — so every read-modify-write is serialized with an advisory
lock, and every write lands via `os.replace` on a fsynced temp file. Without both,
a counter loses increments and a crash mid-write leaves truncated JSON behind.

Locking is per-platform: `fcntl.flock` on POSIX, `msvcrt.locking` on Windows.
Windows has no shared-read lock in `msvcrt`, so readers take the exclusive lock
too. That is slightly coarser and also happens to be necessary: on Windows
`os.replace` fails if another process holds the destination open, so serializing
readers against writers is what keeps the atomic swap from raising.

A truncated file is not the same thing as a missing one, and callers need to
tell them apart: a missing breaker file means "fresh session, closed", while an
unreadable one means "we lost the safety state" and must fail closed. `Draft`
therefore reports `missing` and `corrupt` separately rather than collapsing both
into a default.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WINDOWS_LOCK_ATTEMPTS = 500
WINDOWS_LOCK_DELAY = 0.01

if sys.platform == "win32":  # pragma: no cover - platform dependent
    import msvcrt

    _HAVE_LOCK = True

    def _lock_file(fd: int, *, exclusive: bool) -> bool:
        # msvcrt has no shared mode, so readers serialize with writers too. We poll
        # with LK_NBLCK rather than blocking in LK_LOCK: the blocking call holds the
        # GIL, which would deadlock two threads of one process against each other.
        for _ in range(WINDOWS_LOCK_ATTEMPTS):
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                time.sleep(WINDOWS_LOCK_DELAY)  # sleeping releases the GIL
            else:
                return True
        return False

    def _unlock_file(fd: int) -> None:
        with suppress(OSError):
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    try:
        import fcntl

        _HAVE_LOCK = True
    except ImportError:  # pragma: no cover - exotic POSIX
        _HAVE_LOCK = False

    def _lock_file(fd: int, *, exclusive: bool) -> bool:
        if not _HAVE_LOCK:  # pragma: no cover - exotic POSIX
            return False
        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            return True
        return False

    def _unlock_file(fd: int) -> None:
        if not _HAVE_LOCK:  # pragma: no cover - exotic POSIX
            return
        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)

SCHEMA_VERSION = 1
VERSION_KEY = "_v"

Factory = Callable[[], dict[str, Any]]


@dataclass
class Draft:
    """A working copy of one state file, plus how we found it on disk."""

    data: dict[str, Any]
    missing: bool = False
    corrupt: bool = False
    #: Set False inside an `update()` block to skip persisting.
    write: bool = True


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    # -- public API ---------------------------------------------------------

    def read(self, name: str, default: Factory) -> Draft:
        """Snapshot one file under a shared lock. Never writes."""
        with self._lock(name, exclusive=False):
            return self._read_unlocked(name, default)

    @contextmanager
    def update(self, name: str, default: Factory) -> Iterator[Draft]:
        """Read-modify-write one file under an exclusive lock."""
        with self._lock(name, exclusive=True):
            draft = self._read_unlocked(name, default)
            yield draft
            if draft.write:
                self._write_unlocked(name, draft.data)

    def path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    # -- internals ----------------------------------------------------------

    def _read_unlocked(self, name: str, default: Factory) -> Draft:
        path = self.path(name)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Draft(data=default(), missing=True)
        except OSError:
            return Draft(data=default(), corrupt=True)

        try:
            parsed = json.loads(raw)
        except ValueError:
            return Draft(data=default(), corrupt=True)

        if not isinstance(parsed, dict) or parsed.get(VERSION_KEY) != SCHEMA_VERSION:
            # A file written by a different runbreaker version is not something we
            # can reason about. Treat it exactly like a truncated one and let the
            # caller decide whether that is safe.
            return Draft(data=default(), corrupt=True)

        parsed.pop(VERSION_KEY, None)
        return Draft(data=parsed)

    def _write_unlocked(self, name: str, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {VERSION_KEY: SCHEMA_VERSION, **data}
        tmp_fd, tmp_name = tempfile.mkstemp(dir=self.root, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
                fh.flush()
                os.fsync(fh.fileno())
            Path(tmp_name).replace(self.path(name))
        except BaseException:
            with suppress(OSError):
                Path(tmp_name).unlink()
            raise
        self._fsync_dir()

    def _fsync_dir(self) -> None:
        # Makes the rename itself durable. Not available everywhere; best effort.
        with suppress(OSError, AttributeError):
            fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    @contextmanager
    def _lock(self, name: str, *, exclusive: bool) -> Iterator[None]:
        if not _HAVE_LOCK:  # pragma: no cover - exotic POSIX
            yield
            return
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / f"{name}.lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            # Never raises: a hook that cannot lock should still meter the run,
            # badly, rather than abort and hand the host an exit code it may read
            # as a denial.
            acquired = _lock_file(fd, exclusive=exclusive)
            try:
                yield
            finally:
                if acquired:
                    _unlock_file(fd)
        finally:
            os.close(fd)


