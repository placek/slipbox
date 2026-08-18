"""hermes-agent hooks.

* `on_session_start` — first-run setup + the re-embedding trigger. On a fresh
  repository it creates the layout, `index.md` and `embeddings.db` (cheap: no
  embedding); then it compares the notes against `embeddings.db` and says what
  drifted. It only *reports* drift; embedding a whole repository must never block
  the start of a conversation. The scheduled jobs and `slipbox_reindex` do that.
* `on_session_end` — safety net for "every content-modifying action ends with a
  commit": picks up changes made outside the write tools (manual edits, generic
  file tools). A clean tree means no commit.

Hooks never raise — a git or sqlite problem must not take the agent down.
`SLIPBOX_AUTOCOMMIT=0` disables the commit.
"""
from __future__ import annotations

import logging

from . import config, gitops, lookup, operations

logger = logging.getLogger(__name__)


def on_session_start(**_) -> None:
    # Set up and freshness-check EVERY configured repo (config.repo_items() yields
    # the single default repo when SLIPBOX_REPOS is unset). A read-only deployment
    # never runs setup (it may not create or commit) — it only reports drift.
    read_only = config.readonly()
    for name, root in config.repo_items():
        label = name or "repo"
        if not read_only:
            try:
                if not operations.is_initialized(root):
                    result = operations.setup(root)
                    logger.info("slipbox: initialized %s at %s (created: %s)",
                                label, result["root"], ", ".join(result["created"]) or "nothing")
            except Exception as exc:  # noqa: BLE001 - a hook must never raise
                logger.warning("slipbox: setup failed for %s: %s", label, exc)

        try:
            report = lookup.freshness(root)
        except Exception as exc:  # noqa: BLE001 - a hook must never raise
            logger.warning("slipbox: freshness check failed for %s: %s", label, exc)
            continue
        if report.get("stale"):
            logger.info(
                "slipbox: %s embeddings.db is stale (missing %s, changed %s, orphaned %s) "
                "— run `slipbox reindex`",
                label, report["missing"], report["changed"], report["orphaned"],
            )


def on_session_end(**_) -> None:
    if config.readonly():
        return  # a read-only agent makes no changes, so there is nothing to commit
    for name, root in config.repo_items():
        try:
            result = gitops.commit("slipbox: commit pending changes", root)
            if result.get("committed"):
                logger.info("slipbox: %s: %s", name or "repo", result["message"])
        except Exception as exc:  # noqa: BLE001 - a hook must never raise
            logger.warning("slipbox: session-end commit failed for %s: %s", name or "repo", exc)
