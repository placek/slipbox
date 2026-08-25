"""Git plumbing: every content-modifying action ends with a commit.

The whitepaper's invariant: markdown is the only source of truth and every
change is git-audited. `embeddings.db` and the lock directory stay outside git
(they are derived); the plugin makes sure `.gitignore` says so before the first
commit.
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

IGNORED = (config.DB_FILE, config.DB_FILE + "-*", config.LOCK_DIR + "/")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def is_repo(root: Path) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree").returncode == 0


def _has_identity(root: Path) -> bool:
    """Whether git can resolve an author for a commit here."""
    return bool(_git(root, "config", "user.email").stdout.strip()
                and _git(root, "config", "user.name").stdout.strip())


def init(root: Path) -> dict:
    """Create the repository when the store is not under git yet.

    Setup used to write `.gitignore` and then call `commit`, which answered
    `not a git repository` and returned quietly — so on a fresh store every
    capture, atom and placement was written to disk with **no audit trail**, and
    nothing said so: `slipbox_log` simply reported an empty history. A store that
    is not git-audited is not a slipbox, so first-run setup creates the repo
    rather than assuming somebody else did.

    A store deliberately kept *inside* a larger repository is left alone —
    `is_repo` is already true there and its commits belong to the outer tree.

    An automated commit also needs an author. Where the host has no global
    identity configured, git refuses the commit with an error nobody reads, so a
    repository-local fallback is set here — never overriding a real one.
    """
    if is_repo(root):
        return {"initialized": False, "reason": "already a git repository"}
    root.mkdir(parents=True, exist_ok=True)
    result = _git(root, "init", "-q")
    if result.returncode != 0:
        logger.warning("slipbox: git init failed: %s", result.stderr.strip())
        return {"initialized": False, "error": result.stderr.strip() or "git init failed"}
    identity = False
    if not _has_identity(root):
        name, email = config.git_identity()
        _git(root, "config", "user.name", name)
        _git(root, "config", "user.email", email)
        identity = True
    return {"initialized": True, "local_identity": identity}


def state(root: Path) -> dict:
    """Whether this store can actually be audited — what `doctor` reports."""
    repo = is_repo(root)
    return {
        "is_repo": repo,
        "autocommit": config.autocommit(),
        "has_identity": _has_identity(root) if repo else False,
        "commits": (
            int(_git(root, "rev-list", "--count", "HEAD").stdout.strip() or 0)
            if repo else 0
        ),
    }


def ensure_gitignore(root: Path) -> bool:
    """Guarantee the derived artefacts are ignored. True when the file changed."""
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    missing = [rule for rule in IGNORED if rule not in existing.split()]
    if not missing:
        return False
    prefix = "" if existing.endswith("\n") or not existing else "\n"
    path.write_text(existing + prefix + "\n".join(missing) + "\n", encoding="utf-8")
    return True


def commit(message: str, root: Path | None = None) -> dict:
    """Commit everything in the working tree. Idempotent: no changes, no commit."""
    root = root or config.repo_root(None)
    if not config.autocommit():
        return {"committed": False, "reason": "autocommit disabled (SLIPBOX_AUTOCOMMIT=0)"}
    if not is_repo(root):
        return {"committed": False, "error": f"not a git repository: {root}"}

    ensure_gitignore(root)
    _git(root, "add", "-A")
    if _git(root, "diff", "--cached", "--quiet").returncode == 0:
        return {"committed": False, "reason": "nothing to commit"}

    text = (message or "").strip() or f"slipbox: update {datetime.now():%Y-%m-%dT%H:%M}"
    # --no-gpg-sign: automated commits must not depend on a GPG agent being
    # reachable from the host runtime (it usually is not).
    result = _git(root, "commit", "-q", "--no-gpg-sign", "-m", text)
    if result.returncode != 0:
        logger.warning("slipbox: git commit failed: %s", result.stderr.strip())
        return {"committed": False, "error": result.stderr.strip()}
    return {"committed": True, "message": text}


def history(relative_path: str, root: Path | None = None, limit: int = 20) -> list[dict]:
    """Commits touching a path, newest first — follows renames (`stage/` → `store/`)."""
    root = root or config.repo_root(None)
    if not is_repo(root):
        return []
    result = _git(
        root, "log", f"-{limit}", "--follow", "--name-status",
        "--date=short", "--format=%x00%H%x1f%ad%x1f%an%x1f%s", "--", relative_path,
    )
    if result.returncode != 0:
        return []
    entries = []
    for chunk in result.stdout.split("\x00"):
        if not chunk.strip():
            continue
        head, _, tail = chunk.partition("\n")
        sha, _, rest = head.partition("\x1f")
        when, _, rest = rest.partition("\x1f")
        author, _, subject = rest.partition("\x1f")
        changes = [line.split("\t") for line in tail.strip().splitlines() if line.strip()]
        entries.append({
            "commit": sha[:10],
            "date": when,
            "author": author,
            "subject": subject,
            "changes": [{"status": c[0], "path": c[-1]} for c in changes if len(c) >= 2],
        })
    return entries
