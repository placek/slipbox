"""The shared lookup mechanism — one implementation for every skill.

The whitepaper's §"Retrieval Architecture": all lookup — in search, in source
deduplication, in placement — runs the same four-step mechanism:

1. **Structural** — the nested topic map (`index.md`) yields entry-note IDs at
   near-zero cost; the interval a topic covers is derived as `[entry, next)`.
2. **Positional** — from each entry note the search bisects the linearly ordered
   store, following the branch the query matches best, and returns a window of
   ±N notes. Probes are scored by the **reranker** (`bge-reranker-v2-m3`) when it
   is available, by token overlap otherwise. Stop criteria: window size, a hard
   probe budget, or a signal plateau.
3. **Semantic** — global k-NN over the embeddings, always per space, never
   trimmed to the structural interval and never intersected with the positional
   hits.
4. **Judged** — the union of steps 2 and 3, deduplicated, capped, each carrying
   provenance (a candidate found by both layers is a soft ranking prior). The
   final ranking is the reading judge's job: vectors nominate, a reader decides.

The channels have decorrelated failure modes — a query must defeat structure,
position and semantics simultaneously to miss. This module never writes: it is
safe to call from read-only tools.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import config, embeddings, folgezettel, indexmd, models, notes
from .text import tokens as _tokens


def _excerpt(note: notes.Note, width: int = 240) -> str:
    body = re.sub(r"\s+", " ", note.body).strip()
    return body[:width] + ("…" if len(body) > width else "")


# --- Step 2: positional bisection --------------------------------------------

def _probe_scorer(query: str, by_id: dict[str, notes.Note]):
    """A cached `note_id -> score` function and whether it uses the reranker.

    The reranker (a cross-encoder) is the probe judge; when it cannot be loaded
    the mechanism degrades to token overlap, so the whole pipeline — and the
    tests — run without a GPU.
    """
    cache: dict[str, float] = {}
    if models.reranker_available():
        def score(note_id: str) -> float:
            if note_id not in cache:
                note = by_id.get(note_id)
                text = note.embed_text() if note else ""
                cache[note_id] = models.rerank_scores(query, [text])[0] if text else 0.0
            return cache[note_id]
        return score, True

    wanted = _tokens(query)

    def score(note_id: str) -> float:  # token-overlap fallback
        if note_id not in cache:
            note = by_id.get(note_id)
            haystack = _tokens(note.title) | _tokens(note.body) if note else set()
            cache[note_id] = len(wanted & haystack) / len(wanted) if wanted else 0.0
        return cache[note_id]

    return score, False


def descend(query: str, start: str, by_id: dict[str, notes.Note],
            budget: int | None = None) -> str:
    """Halve the branch below `start`, following the best-matching child.

    Returns the ID where the descent stopped — the centre of the region the
    thread most likely occupies. `budget` caps how many notes are read; the
    descent also stops when no child beats the current note (a signal plateau).
    """
    if not _tokens(query):
        return start
    budget = config.probe_budget() if budget is None else budget
    score, _ = _probe_scorer(query, by_id)

    current = start
    known = set(by_id)
    seen = {current}
    reads = 0
    while True:
        children = folgezettel.children(current, known)
        if not children:
            return current
        best, best_score = None, 0.0
        for child in children:
            branch = (child, *folgezettel.descendants(child, known))
            child_score = 0.0
            for member in branch:
                child_score = max(child_score, score(member))
                reads += 1
                if reads >= budget:
                    break
            if child_score > best_score:
                best, best_score = child, child_score
            if reads >= budget:
                break
        own_score = score(current)
        if best is None or best_score <= own_score or best in seen or reads >= budget:
            return current
        seen.add(best)
        current = best


def bisect(root: Path, query: str, starts, window: int | None = None) -> list[dict]:
    """Windows of ±N store notes around the regions the descent settled on."""
    window = config.window() if window is None else window
    by_id = {n.stem: n for n in notes.store_notes(root) if folgezettel.is_valid(n.stem)}
    if not by_id:
        return []
    found: dict[str, dict] = {}
    for start in starts:
        if start not in by_id:
            continue
        centre = descend(query, start, by_id)
        for note_id in folgezettel.neighbourhood(centre, by_id, window):
            note = by_id.get(note_id)
            if note is None or note_id in found:
                continue
            found[note_id] = {
                "key": note_id,
                "id": note_id,
                "path": note.rel,
                "title": note.title,
                "space": config.SPACE_STORE,
                "from_topic": start,
                "centre": centre,
            }
    return list(found.values())


# --- Step 3: vector search ---------------------------------------------------

def vector_search(root: Path, vector, spaces, k=None, max_distance=None) -> tuple[list[dict], str]:
    """k-NN per space. Returns (hits, notice) — never raises on a missing index."""
    try:
        store = embeddings.Store(root, readonly=True)
    except FileNotFoundError:
        return [], f"{config.DB_FILE} does not exist yet — run slipbox_reindex"
    except (OSError, sqlite3.Error) as exc:
        return [], f"cannot open {config.DB_FILE}: {exc}"
    try:
        meta = store.meta()
        if meta and meta[0] != config.embed_model():
            return [], (
                f"embeddings.db was built with {meta[0]}, configured model is "
                f"{config.embed_model()} — run slipbox_reindex full=true"
            )
        hits: list[dict] = []
        for space in spaces:
            hits.extend(store.search(space, vector, k=k, max_distance=max_distance))
        return hits, ""
    finally:
        store.close()


# --- Freshness ---------------------------------------------------------------

def freshness(root: Path | None = None) -> dict:
    """How far `embeddings.db` has drifted from the notes on disk."""
    root = root or config.repo_root(None)
    report = {"missing": 0, "changed": 0, "orphaned": 0, "indexed": 0, "notes": 0}
    try:
        store = embeddings.Store(root, readonly=True)
    except FileNotFoundError:
        report["missing"] = sum(len(notes.notes_in_space(root, s)) for s in config.SPACES)
        report["notes"] = report["missing"]
        report["stale"] = report["missing"] > 0
        return report
    try:
        for space in config.SPACES:
            indexed = store.items(space)
            report["indexed"] += len(indexed)
            present = {n.key: n for n in notes.notes_in_space(root, space)}
            report["notes"] += len(present)
            report["orphaned"] += len(set(indexed) - set(present))
            for key, note in present.items():
                row = indexed.get(key)
                if row is None:
                    report["missing"] += 1
                elif row["hash"] != note.hash() or row["path"] != note.rel:
                    report["changed"] += 1
    finally:
        store.close()
    report["stale"] = bool(report["missing"] or report["changed"] or report["orphaned"])
    return report


# --- Step 0: the synthesis lead ------------------------------------------------

def synthesis_lead(query: str, root: Path | None = None, *, vector=None,
                   max_distance: float | None = None) -> dict | None:
    """A question already answered — consulted *before* the four layers, not beside them.

    The other channels answer "which notes are about this". This one answers
    something different and cheaper: "has this road been walked before". A hit
    means the hops were made, the threads gathered and the citations already
    placed, so the judge does not compose from nothing — it checks the *delta*.

    The delta is what makes this safe rather than a cache of stale answers: how
    many atoms have been placed in the cited threads since the synthesis was
    written. Empty delta and the earlier answer still stands. Non-empty and the
    judge reads only those atoms and writes a new synthesis superseding this one,
    which is a fraction of the work of answering from scratch.

    Never authoritative. A synthesis is a lead: everything the judge takes from
    it must still resolve to the atoms it cites, which is exactly why it may be
    machine-written and unreviewed without weakening a single guarantee the store
    makes. Returns None when nothing is close enough to be worth reading.
    """
    root = root or config.repo_root(None)
    query = (query or "").strip()
    if vector is None and query:
        try:
            vector = embeddings.embed_query(query)
        except embeddings.EmbeddingError:
            return None
    if vector is None:
        return None

    hits, notice = vector_search(root, vector, (config.SPACE_SYNTHESIS,), k=1,
                                 max_distance=(config.synthesis_lead_distance()
                                               if max_distance is None else max_distance))
    if notice or not hits:
        return None
    best = min(hits, key=lambda h: h["distance"])
    note = notes.load(root, best["path"])
    if note is None:
        return None

    drift = next((d for d in operations_drift(root) if d["path"] == note.rel), {})
    return {
        "path": note.rel,
        "title": note.title,
        "question": note.get("question") or "",
        "created": note.get("created"),
        "cites": note.cites,
        "distance": best["distance"],
        "drift": drift.get("drift", 0),
        "new_atoms": drift.get("new_atoms", []),
        "superseded_by": drift.get("superseded_by"),
        "verdict": (
            "superseded — read the newer synthesis instead"
            if drift.get("superseded_by") else
            "current — no atom has been placed in the cited threads since"
            if not drift.get("drift") else
            f"stale by {drift.get('drift')} atom(s) — read those, then re-synthesise"
        ),
        "instruction": (
            "This is a LEAD, never evidence. Its claims are proved by the atoms it "
            "cites, so verify through them before repeating anything. If `new_atoms` "
            "is non-empty, read only those and write a new synthesis that supersedes "
            "this one."
        ),
    }


def operations_drift(root: Path):
    """`operations.synthesis_drift` without the import cycle it would otherwise cost."""
    from . import operations

    return operations.synthesis_drift(root)["syntheses"]


# --- Step 4: ranking the union -------------------------------------------------

def _rank(candidate: dict) -> float:
    """Effective distance a candidate is ordered by — lower is better.

    The vector distance leads, because it is the only *calibrated* signal in the
    union. A candidate the positional layer swept in but the embedder never
    nominated carries no distance; it takes the neutral one
    (`config.positional_distance`). Agreement between the layers subtracts a
    small bonus rather than sorting ahead of everything, which is what "a soft
    prior, never a filter" has to mean once the list is truncated to a limit.
    """
    distance = candidate.get("distance")
    if distance is None:
        distance = config.positional_distance()
    if candidate.get("both_layers"):
        distance -= config.both_layers_bonus()
    return distance


# --- The mechanism ------------------------------------------------------------

def lookup(query: str, spaces=None, root: Path | None = None, *, vector=None,
           k: int | None = None, window: int | None = None,
           limit: int | None = None, use_index: bool = True) -> dict:
    """Run the four-step lookup and return candidates with provenance.

    `vector` short-circuits the query embedding — placement (`slipbox:link`)
    passes the atom's ready-made `vec_stage` vector instead of recomputing it.
    """
    root = root or config.repo_root(None)
    spaces = tuple(spaces or (config.SPACE_STORE,))
    limit = config.candidate_limit() if limit is None else limit
    query = (query or "").strip()
    notices: list[str] = []

    # Step 1 — structural: topics in index.md (only meaningful for store IDs).
    starts: list[str] = []
    if use_index and query and config.SPACE_STORE in spaces:
        starts = indexmd.entry_ids(root, query)

    # Step 2 — positional: bisection from those entry notes. The probe judge is
    # the reranker when it can score, token overlap otherwise (reported below).
    bisected = bisect(root, query, starts, window) if starts else []
    positional_scorer = (
        ("reranker" if models.reranker_available() else "token-overlap")
        if starts else None
    )

    # Step 3 — semantic: global k-NN per space, never trimmed to step 1.
    hits: list[dict] = []
    degraded = False
    if vector is None and query:
        try:
            vector = embeddings.embed_query(query)
        except embeddings.EmbeddingError as exc:
            degraded = True
            notices.append(str(exc))
    if vector is not None:
        hits, notice = vector_search(root, vector, spaces, k=k)
        if notice:
            degraded = True
            notices.append(notice)

    # Step 4 — union, dedup by path, provenance, cap.
    candidates: dict[str, dict] = {}
    for item in bisected:
        candidates[item["path"]] = {**item, "provenance": ["positional"]}
    for hit in hits:
        existing = candidates.get(hit["path"])
        if existing:
            existing["provenance"].append("semantic")
            existing["distance"] = hit["distance"]
            continue
        candidates[hit["path"]] = {**hit, "provenance": ["semantic"]}

    enriched = []
    for item in candidates.values():
        note = notes.load(root, item["path"])
        if note is None:
            continue
        enriched.append({
            **item,
            "title": note.title,
            "excerpt": _excerpt(note),
            # Found by both layers → a soft prior for the judge (never a filter).
            "both_layers": len(item["provenance"]) > 1,
        })

    enriched.sort(key=lambda c: (_rank(c), c["path"]))
    truncated = len(enriched) > limit

    return {
        "query": query,
        "spaces": list(spaces),
        "steps": {
            "structural": starts,
            "positional": [c["key"] for c in bisected],
            "semantic": [h["key"] for h in hits],
        },
        # Which models actually ran: the embedder (semantic k-NN) unless degraded,
        # and the reranker vs token-overlap in the positional probe.
        "positional_scorer": positional_scorer,
        "semantic_scorer": None if (vector is None and degraded) else "bge-m3",
        "candidates": enriched[:limit],
        "count": min(len(enriched), limit),
        "truncated": truncated,
        "degraded": degraded,
        "notices": notices,
        "next_step": (
            "Read the candidates (slipbox_show) and rank them by content — the "
            "vector order is a prefilter, not the answer."
        ),
    }


def nearest_duplicate(root: Path, vector, space: str = config.SPACE_STORE,
                      threshold: float | None = None) -> dict | None:
    """The closest existing note, when it is close enough to be a suspected twin.

    Feeds the whitepaper's atom-level deduplication signal (Stage 2 step 6): a
    very close vector neighbour marks a fresh atom as a *potential* duplicate.
    This is a signal, never a decision — only the human editor drops a note.
    """
    threshold = config.duplicate_distance() if threshold is None else threshold
    hits, _ = vector_search(root, vector, (space,), k=1, max_distance=threshold)
    if not hits:
        return None
    best = min(hits, key=lambda h: h["distance"])
    note = notes.load(root, best["path"])
    return {
        "twin": best.get("key"),
        "path": best["path"],
        "title": note.title if note else "",
        "distance": best["distance"],
    }
