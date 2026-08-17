"""hermes-agent hooks.

* `on_session_start` — the re-embedding trigger: compare the notes against
  `embeddings.db` and say what drifted. It only *reports*; embedding a whole
  repository must never block the start of a conversation. The scheduled jobs and
  `slipbox_reindex` do the actual work.
* `on_session_end` — safety net for "every content-modifying action ends with a
  commit": picks up changes made outside the write tools (manual edits, generic
  file tools). A clean tree means no commit.

Hooks never raise — a git or sqlite problem must not take the agent down.
`SLIPBOX_AUTOCOMMIT=0` disables the commit.
"""
from __future__ import annotations

import logging

from . import config, gitops, lookup

logger = logging.getLogger(__name__)


def on_session_start(**_) -> None:
    try:
        report = lookup.freshness(config.root())
    except Exception as exc:  # noqa: BLE001 - a hook must never raise
        logger.warning("slipbox: freshness check failed: %s", exc)
        return
    if report.get("stale"):
        logger.info(
            "slipbox: embeddings.db is stale (missing %s, changed %s, orphaned %s) "
            "— run `slipbox reindex`",
            report["missing"], report["changed"], report["orphaned"],
        )


def on_session_end(**_) -> None:
    try:
        result = gitops.commit("slipbox: commit pending changes")
        if result.get("committed"):
            logger.info("slipbox: %s", result["message"])
    except Exception as exc:  # noqa: BLE001 - a hook must never raise
        logger.warning("slipbox: session-end commit failed: %s", exc)
