"""Advisory file locks shared by scheduled jobs and manual skills.

The whitepaper's §"Concurrency and automation": three scheduled jobs and the
manual skills all take `flock`-based locks on the resources they touch
(`inbox/`, staging, the vector database, the repository for commits), waiting or
skipping when contended. Manual skills take the very same locks, so they wait
instead of racing a cron run.

Lock files live in `<root>/.slipbox-locks/` — outside git, like `embeddings.db`.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

from . import config

try:  # POSIX only; on other platforms locking degrades to a no-op.
    import fcntl
except ImportError:  # pragma: no cover - not exercised on Linux
    fcntl = None  # type: ignore[assignment]

INBOX = "inbox"
STAGE = "stage"
DB = "embeddings-db"
REPO = "repo"
# Held for a whole distillation by the dedicated atomiser. Deliberately a name no
# ordinary operation takes: the steps *inside* a distillation still acquire
# `inbox`/`stage`/`repo`/`db` themselves, and flock conflicts between two open
# descriptions even within one process — so reusing those names here would
# deadlock the pipeline against itself. This one only serialises distillations.
ATOMIZER = "atomizer"
ALL = (INBOX, STAGE, DB, REPO, ATOMIZER)


class LockBusy(RuntimeError):
    """Another job holds the lock; the caller should wait or skip the run."""


def _lock_path(name: str, root: Path | None = None) -> Path:
    base = (root or config.repo_root(None)) / config.LOCK_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{name}.lock"


@contextmanager
def hold(*names: str, timeout: float | None = None, root: Path | None = None):
    """Hold the named locks for the duration of the block.

    Names are acquired in a fixed order, so two jobs asking for overlapping sets
    can never deadlock each other.
    """
    timeout = config.lock_timeout() if timeout is None else timeout
    wanted = sorted(set(names))
    handles: list = []
    try:
        for name in wanted:
            handles.append(_acquire(name, timeout, root))
        yield
    finally:
        for handle in reversed(handles):
            _release(handle)


def _acquire(name: str, timeout: float, root: Path | None):
    path = _lock_path(name, root)
    handle = open(path, "a+")  # noqa: SIM115 - released in _release
    if fcntl is None:
        return handle
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()} {time.time():.0f}\n")
            handle.flush()
            return handle
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise LockBusy(f"lock '{name}' is held by another process") from None
            time.sleep(0.2)


def _release(handle) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def held(name: str, root: Path | None = None) -> bool:
    """Whether the lock is currently taken (probe, never blocks)."""
    if fcntl is None:
        return False
    path = _lock_path(name, root)
    with open(path, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False


def state(root: Path | None = None) -> dict:
    """Lock state for `slipbox_schedule`."""
    return {name: ("held" if held(name, root) else "free") for name in ALL}
