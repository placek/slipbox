"""Tool handlers — the code that runs when the LLM calls a tool.

hermes-agent contract: every handler takes `(args: dict, **kwargs)`, always
returns a JSON string, and never raises.
"""
from __future__ import annotations

import json

from . import config, embeddings, lookup, operations


def _ok(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _err(message: str, **extra) -> str:
    return json.dumps({"error": message, **extra}, ensure_ascii=False, default=str)


def _guard(name: str, call):
    try:
        return _ok(call())
    except operations.OperationError as exc:
        return _err(str(exc))
    except embeddings.ModelMismatch as exc:
        return _err(str(exc), hint="run slipbox_reindex with full=true")
    except embeddings.EmbeddingError as exc:
        return _err(str(exc), hint=f"is the in-process model {config.embed_model()} loadable?")
    except Exception as exc:  # noqa: BLE001 - a tool must never raise
        return _err(f"{name}: {exc}")


def _as_list(value) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _int_or(value, default=None):
    return int(value) if str(value or "").lstrip("-").isdigit() else default


# --- Read-only ---------------------------------------------------------------

def slipbox_show(args: dict, **_) -> str:
    return _guard("slipbox_show", lambda: operations.show(args.get("ident", "")))


def slipbox_lookup(args: dict, **_) -> str:
    spaces = _as_list(args.get("spaces")) or [config.SPACE_STORE]
    unknown = [s for s in spaces if s not in config.SPACES]
    if unknown:
        return _err(f"unknown vector space(s): {', '.join(unknown)}")
    return _guard("slipbox_lookup", lambda: lookup.lookup(
        args.get("query", ""), spaces=spaces, limit=_int_or(args.get("limit")),
    ))


def slipbox_inbox(args: dict, **_) -> str:
    return _guard("slipbox_inbox", operations.inbox)


def slipbox_stage(args: dict, **_) -> str:
    status = (args.get("status") or "").strip() or None
    if status and status not in config.REVIEW_STATUSES:
        return _err(f"unknown review status: {status}")
    return _guard("slipbox_stage", lambda: operations.stage(status))


def slipbox_sources(args: dict, **_) -> str:
    return _guard("slipbox_sources", operations.sources)


def slipbox_store(args: dict, **_) -> str:
    return _guard("slipbox_store", lambda: operations.store(args.get("prefix")))


def slipbox_tree(args: dict, **_) -> str:
    return _guard("slipbox_tree", lambda: operations.tree(args.get("ident", "")))


def slipbox_backlinks(args: dict, **_) -> str:
    return _guard("slipbox_backlinks", lambda: operations.backlinks(args.get("ident", "")))


def slipbox_index(args: dict, **_) -> str:
    return _guard("slipbox_index", operations.index)


def slipbox_original(args: dict, **_) -> str:
    return _guard("slipbox_original", lambda: operations.original(args.get("ident", "")))


def slipbox_status(args: dict, **_) -> str:
    return _guard("slipbox_status", operations.status)


def slipbox_log(args: dict, **_) -> str:
    return _guard("slipbox_log", lambda: operations.log(
        args.get("ident", ""), limit=_int_or(args.get("limit"), 20)
    ))


def slipbox_schedule(args: dict, **_) -> str:
    return _guard("slipbox_schedule", operations.schedule)


# --- Gated (interactive read path) -------------------------------------------

def slipbox_search(args: dict, **_) -> str:
    """Run the shared lookup across every space and frame it for a cited answer.

    The tool nominates and reads; the cited *summary* is composed by the caller
    (the host agent — or, in a headless deployment, the in-process judge),
    because the whitepaper's quality budget belongs to the judge, never to the
    retriever.
    """
    def run():
        result = lookup.lookup(
            args.get("query", ""),
            spaces=[config.SPACE_STORE, config.SPACE_SOURCE, config.SPACE_STAGE],
            limit=_int_or(args.get("limit")),
        )
        result["instruction"] = (
            "Read the candidates (slipbox_show / slipbox_quote), then answer so "
            "that EVERY claim cites the note IDs it derives from. Mark staged "
            "candidates as not yet situated. On an explicit contradiction, let the "
            "newer position win and cite the older as the earlier one."
        )
        return result
    return _guard("slipbox_search", run)


def slipbox_quote(args: dict, **_) -> str:
    return _guard("slipbox_quote", lambda: operations.quote(args.get("ident", "")))


# --- Writing -----------------------------------------------------------------

def slipbox_setup(args: dict, **_) -> str:
    return _guard("slipbox_setup", operations.setup)


def slipbox_capture(args: dict, **_) -> str:
    return _guard("slipbox_capture", lambda: operations.capture(
        title=args.get("title", ""),
        content=args.get("content", ""),
        captured_by=args.get("captured_by"),
        reference=args.get("reference"),
        extraction=args.get("extraction") or config.EXTRACTION_OK,
        attachments=_as_list(args.get("attachments")),
    ))


def slipbox_source(args: dict, **_) -> str:
    return _guard("slipbox_source", lambda: operations.create_source(
        title=args.get("title", ""),
        author=args.get("author", "") or "",
        source_type=args.get("type") or "other",
        reference=args.get("reference", "") or "",
        description=args.get("description", "") or "",
        date=args.get("date"),
        topic=args.get("topic"),
        tags=_as_list(args.get("tags")),
        attachments=_as_list(args.get("attachments")),
        accessed=args.get("accessed"),
    ))


def slipbox_atom(args: dict, **_) -> str:
    placement = args.get("placement")
    duplicate = args.get("duplicate")
    return _guard("slipbox_atom", lambda: operations.create_atom(
        title=args.get("title", ""),
        body=args.get("body", ""),
        source=_as_list(args.get("source")),
        captured_by=args.get("captured_by"),
        created=args.get("created"),
        link_after=(args.get("link_after") or "").strip() or None,
        candidates=_as_list(args.get("candidates")),
        new_thread_topic=(args.get("new_thread_topic") or "").strip() or None,
        variants=_as_list(args.get("variants")),
        rationale=args.get("rationale", "") or "",
        scope=(args.get("scope") or "").strip() or None,
        scope_rationale=args.get("scope_rationale", "") or "",
        placement=placement if isinstance(placement, dict) else None,
        duplicate=duplicate if isinstance(duplicate, dict) else None,
        attachments=_as_list(args.get("attachments")),
    ))


def slipbox_scope(args: dict, **_) -> str:
    return _guard("slipbox_scope", lambda: operations.set_scope(
        ident=args.get("ident", ""),
        scope=args.get("scope", ""),
        rationale=args.get("rationale", "") or "",
    ))


def slipbox_move_attachments(args: dict, **_) -> str:
    return _guard("slipbox_move_attachments",
                  lambda: operations.move_attachments(_as_list(args.get("names"))))


def slipbox_archive_original(args: dict, **_) -> str:
    return _guard("slipbox_archive_original", lambda: operations.archive_original(
        inbox_ident=args.get("ident", ""), source=args.get("source", ""),
    ))


def slipbox_drop_inbox(args: dict, **_) -> str:
    return _guard("slipbox_drop_inbox", lambda: operations.drop_inbox(
        _as_list(args.get("idents")), reason=args.get("reason") or "processed"
    ))


def slipbox_review(args: dict, **_) -> str:
    return _guard("slipbox_review", lambda: operations.review(
        ident=args.get("ident", ""),
        status=args.get("status", ""),
        link_after=(args.get("link_after") or "").strip() or None,
        variant_of=(args.get("variant_of") or "").strip() or None,
        decided_by=args.get("decided_by"),
        new_thread_topic=args.get("new_thread_topic"),
    ))


def slipbox_persist(args: dict, **_) -> str:
    return _guard("slipbox_persist", lambda: operations.persist(
        ident=args.get("ident", ""),
        after=(args.get("after") or "").strip() or None,
        topic=_as_list(args.get("topic")),
        rationale=args.get("rationale", "") or "",
        new_thread=bool(args.get("new_thread", False)),
    ))


def slipbox_index_add(args: dict, **_) -> str:
    return _guard("slipbox_index_add", lambda: operations.index_add(
        topic=_as_list(args.get("topic")), note_id=args.get("note_id", "")
    ))


def slipbox_index_write(args: dict, **_) -> str:
    return _guard("slipbox_index_write",
                  lambda: operations.index_write(args.get("content", "")))


def slipbox_purge_rejected(args: dict, **_) -> str:
    return _guard("slipbox_purge_rejected", operations.purge_rejected)


def slipbox_reindex(args: dict, **_) -> str:
    return _guard("slipbox_reindex",
                  lambda: operations.reindex(full=bool(args.get("full", False))))


HANDLERS = {
    "slipbox_show": slipbox_show,
    "slipbox_lookup": slipbox_lookup,
    "slipbox_inbox": slipbox_inbox,
    "slipbox_stage": slipbox_stage,
    "slipbox_sources": slipbox_sources,
    "slipbox_store": slipbox_store,
    "slipbox_tree": slipbox_tree,
    "slipbox_backlinks": slipbox_backlinks,
    "slipbox_index": slipbox_index,
    "slipbox_original": slipbox_original,
    "slipbox_status": slipbox_status,
    "slipbox_log": slipbox_log,
    "slipbox_schedule": slipbox_schedule,
    "slipbox_search": slipbox_search,
    "slipbox_quote": slipbox_quote,
    "slipbox_setup": slipbox_setup,
    "slipbox_capture": slipbox_capture,
    "slipbox_source": slipbox_source,
    "slipbox_atom": slipbox_atom,
    "slipbox_scope": slipbox_scope,
    "slipbox_move_attachments": slipbox_move_attachments,
    "slipbox_archive_original": slipbox_archive_original,
    "slipbox_drop_inbox": slipbox_drop_inbox,
    "slipbox_review": slipbox_review,
    "slipbox_persist": slipbox_persist,
    "slipbox_index_add": slipbox_index_add,
    "slipbox_index_write": slipbox_index_write,
    "slipbox_purge_rejected": slipbox_purge_rejected,
    "slipbox_reindex": slipbox_reindex,
}
