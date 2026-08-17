"""The semantic layer: in-process embeddings in a sqlite-vec index.

The whitepaper's §"Semantic layer":

* embeddings come from a **local, in-process** model (bge-m3 via FlagEmbedding) —
  no Ollama, no llama.cpp, no external inference server (see `models.py`),
* vectors live in `embeddings.db`, a single sqlite file kept **outside git**,
* three separate `vec0` tables — `vec_store`, `vec_source`, `vec_stage` — so
  "similar thought", "matching source" and "fresh material" stay distinct query
  spaces, never post-filtered from one polluted ranking,
* every row carries the note's path and a content hash, so divergence between
  the files and the index is detected on startup and before every scheduled run,
* `embedding_meta(model, dim)` guards the space: a model mismatch is a hard
  error demanding a full reindex, never silently bad results.

Vectors are stored L2-normalised, so the L2 distance sqlite-vec reports converts
to cosine distance as `d² / 2` — the same metric the pure-python fallback
computes when the sqlite-vec extension is not installed.
"""
from __future__ import annotations

import array
import logging
import math
import os
import sqlite3
from pathlib import Path

from . import config, models, notes

logger = logging.getLogger(__name__)

TABLES = {
    config.SPACE_STORE: "vec_store",
    config.SPACE_SOURCE: "vec_source",
    config.SPACE_STAGE: "vec_stage",
}


class EmbeddingError(RuntimeError):
    """Embeddings are unavailable (model not loadable, no device, bad output)."""


class ModelMismatch(RuntimeError):
    """`embeddings.db` was built with a different model — reindex required."""


# --- In-process embedder (bge-m3) --------------------------------------------

def embed(text: str, prefix: str = "") -> list[float]:
    """Embed one text with the in-process model, L2-normalised.

    Kept as a plain module-level function on purpose: it is the single seam the
    tests stub out, so the whole pipeline runs without loading torch.
    """
    try:
        vector = models.embed_texts([prefix + (text or "")])[0]
    except models.ModelUnavailable as exc:
        raise EmbeddingError(str(exc)) from exc
    if not vector:
        raise EmbeddingError(f"empty embedding from {config.embed_model()}")
    return normalise(vector)


def embed_query(text: str) -> list[float]:
    return embed(text, config.query_prefix())


def embed_doc(text: str) -> list[float]:
    return embed(text, config.doc_prefix())


def available() -> bool:
    try:
        embed("ping")
        return True
    except EmbeddingError:
        return False


# --- Vector maths ------------------------------------------------------------

def normalise(vector) -> list[float]:
    norm = math.sqrt(sum(float(x) * float(x) for x in vector))
    return [float(x) / norm for x in vector] if norm else [float(x) for x in vector]


def pack(vector) -> bytes:
    return array.array("f", vector).tobytes()


def unpack(blob: bytes) -> list[float]:
    values = array.array("f")
    values.frombytes(blob)
    return list(values)


def cosine_distance(a, b) -> float:
    """0 for identical direction, 1 for orthogonal, 2 for opposite."""
    if len(a) != len(b):
        return 2.0
    return 1.0 - sum(x * y for x, y in zip(a, b))


# --- Store -------------------------------------------------------------------

def _load_extension(conn: sqlite3.Connection) -> bool:
    """Load sqlite-vec, from the python package or `$SLIPBOX_SQLITE_VEC`."""
    if not hasattr(conn, "enable_load_extension"):
        return False
    try:
        conn.enable_load_extension(True)
    except (AttributeError, sqlite3.OperationalError):
        return False
    try:
        try:
            import sqlite_vec  # type: ignore[import-not-found]

            sqlite_vec.load(conn)
            return True
        except ImportError:
            path = os.environ.get("SLIPBOX_SQLITE_VEC")
            if not path:
                return False
            conn.load_extension(path)
            return True
    except (sqlite3.OperationalError, OSError) as exc:
        logger.warning("slipbox: sqlite-vec not loaded (%s) — using the python fallback", exc)
        return False
    finally:
        try:
            conn.enable_load_extension(False)
        except sqlite3.OperationalError:
            pass


class Store:
    """`embeddings.db` — bookkeeping plus one vector table per space."""

    def __init__(self, root: Path | None = None, readonly: bool = False):
        self.root = root or config.root()
        self.path = self.root / config.DB_FILE
        self.readonly = readonly
        if readonly:
            if not self.path.exists():
                raise FileNotFoundError(self.path)
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=10000")
        self.vec = _load_extension(self.conn)
        if not readonly:
            self._create_bookkeeping()

    # -- lifecycle
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        self.conn.close()

    @property
    def backend(self) -> str:
        return "sqlite-vec" if self.vec else "python-fallback"

    # -- schema
    def _create_bookkeeping(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS embedding_meta (
                model TEXT NOT NULL,
                dim   INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS items (
                space TEXT NOT NULL,
                key   TEXT NOT NULL,
                path  TEXT NOT NULL,
                hash  TEXT NOT NULL,
                rid   INTEGER NOT NULL,
                PRIMARY KEY (space, key)
            );
            CREATE TABLE IF NOT EXISTS rid_seq (
                space TEXT PRIMARY KEY,
                next  INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fallback_vectors (
                space TEXT NOT NULL,
                rid   INTEGER NOT NULL,
                vec   BLOB NOT NULL,
                PRIMARY KEY (space, rid)
            );
            CREATE TABLE IF NOT EXISTS job_runs (
                job      TEXT PRIMARY KEY,
                started  TEXT,
                finished TEXT,
                outcome  TEXT,
                detail   TEXT
            );
            """
        )
        self.conn.commit()

    def meta(self) -> tuple[str, int] | None:
        try:
            row = self.conn.execute("SELECT model, dim FROM embedding_meta LIMIT 1").fetchone()
        except sqlite3.OperationalError:
            return None
        return (row[0], int(row[1])) if row else None

    def ensure_meta(self, dim: int) -> None:
        """Record (model, dim) or refuse to mix spaces."""
        current = self.meta()
        model = config.embed_model()
        if current is None:
            self.conn.execute("INSERT INTO embedding_meta (model, dim) VALUES (?,?)", (model, dim))
            self.conn.commit()
            return
        if current != (model, dim):
            raise ModelMismatch(
                f"embeddings.db was built with {current[0]} (dim {current[1]}), "
                f"configured model is {model} (dim {dim}) — run slipbox_reindex full=true"
            )

    def reset(self) -> None:
        """Drop every vector and its bookkeeping — used by a full reindex."""
        for table in TABLES.values():
            self.conn.execute(f"DROP TABLE IF EXISTS {table}")
        self.conn.executescript(
            "DELETE FROM items; DELETE FROM rid_seq;"
            " DELETE FROM fallback_vectors; DELETE FROM embedding_meta;"
        )
        self.conn.commit()

    def _ensure_space(self, space: str, dim: int) -> None:
        if not self.vec:
            return
        self.conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {TABLES[space]} USING vec0("
            f"embedding float[{dim}])"
        )

    def _next_rid(self, space: str) -> int:
        row = self.conn.execute("SELECT next FROM rid_seq WHERE space = ?", (space,)).fetchone()
        rid = int(row[0]) if row else 1
        self.conn.execute(
            "INSERT INTO rid_seq (space, next) VALUES (?,?) "
            "ON CONFLICT(space) DO UPDATE SET next = excluded.next",
            (space, rid + 1),
        )
        return rid

    # -- rows
    def items(self, space: str) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT key, path, hash, rid FROM items WHERE space = ?", (space,)
        ).fetchall()
        return {r[0]: {"path": r[1], "hash": r[2], "rid": r[3]} for r in rows}

    def count(self, space: str) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM items WHERE space = ?", (space,)).fetchone()
        return int(row[0]) if row else 0

    def upsert(self, space: str, key: str, path: str, digest: str, vector) -> None:
        dim = len(vector)
        self.ensure_meta(dim)
        self._ensure_space(space, dim)
        existing = self.conn.execute(
            "SELECT rid FROM items WHERE space = ? AND key = ?", (space, key)
        ).fetchone()
        rid = int(existing[0]) if existing else self._next_rid(space)
        self._delete_vector(space, rid)
        self._insert_vector(space, rid, vector)
        self.conn.execute(
            "INSERT INTO items (space, key, path, hash, rid) VALUES (?,?,?,?,?) "
            "ON CONFLICT(space, key) DO UPDATE SET path = excluded.path, "
            "hash = excluded.hash, rid = excluded.rid",
            (space, key, path, digest, rid),
        )
        self.conn.commit()

    def delete(self, space: str, key: str) -> bool:
        row = self.conn.execute(
            "SELECT rid FROM items WHERE space = ? AND key = ?", (space, key)
        ).fetchone()
        if not row:
            return False
        self._delete_vector(space, int(row[0]))
        self.conn.execute("DELETE FROM items WHERE space = ? AND key = ?", (space, key))
        self.conn.commit()
        return True

    def vector(self, space: str, key: str) -> list[float] | None:
        row = self.conn.execute(
            "SELECT rid FROM items WHERE space = ? AND key = ?", (space, key)
        ).fetchone()
        if not row:
            return None
        rid = int(row[0])
        if self.vec:
            found = self.conn.execute(
                f"SELECT embedding FROM {TABLES[space]} WHERE rowid = ?", (rid,)
            ).fetchone()
        else:
            found = self.conn.execute(
                "SELECT vec FROM fallback_vectors WHERE space = ? AND rid = ?", (space, rid)
            ).fetchone()
        return unpack(found[0]) if found else None

    def move(self, from_space: str, to_space: str, key: str,
             new_key: str | None = None, new_path: str | None = None) -> bool:
        """Move a vector between spaces without recomputing it.

        This is the whitepaper's persist migration: the atom's content is
        unchanged, so its embedding travels `vec_stage` → `vec_store` verbatim.
        """
        row = self.conn.execute(
            "SELECT path, hash FROM items WHERE space = ? AND key = ?", (from_space, key)
        ).fetchone()
        vector = self.vector(from_space, key)
        if not row or vector is None:
            return False
        self.delete(from_space, key)
        self.upsert(to_space, new_key or key, new_path or row[0], row[1], vector)
        return True

    # -- vector table access
    def _insert_vector(self, space: str, rid: int, vector) -> None:
        if self.vec:
            self.conn.execute(
                f"INSERT INTO {TABLES[space]} (rowid, embedding) VALUES (?, ?)",
                (rid, pack(vector)),
            )
        else:
            self.conn.execute(
                "INSERT OR REPLACE INTO fallback_vectors (space, rid, vec) VALUES (?,?,?)",
                (space, rid, pack(vector)),
            )

    def _delete_vector(self, space: str, rid: int) -> None:
        if self.vec:
            try:
                self.conn.execute(f"DELETE FROM {TABLES[space]} WHERE rowid = ?", (rid,))
            except sqlite3.OperationalError:
                pass  # table not created yet
        else:
            self.conn.execute(
                "DELETE FROM fallback_vectors WHERE space = ? AND rid = ?", (space, rid)
            )

    def search(self, space: str, vector, k: int | None = None,
               max_distance: float | None = None) -> list[dict]:
        """k-NN inside one space. Distances are cosine (0 = identical)."""
        k = config.top_k() if k is None else k
        max_distance = config.distance_max() if max_distance is None else max_distance
        by_rid = {
            row["rid"]: {"key": key, "path": row["path"]}
            for key, row in self.items(space).items()
        }
        if not by_rid:
            return []

        hits: list[tuple[int, float]] = []
        if self.vec:
            try:
                rows = self.conn.execute(
                    f"SELECT rowid, distance FROM {TABLES[space]} "
                    "WHERE embedding MATCH ? AND k = ?",
                    (pack(vector), max(k, 1)),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            # sqlite-vec reports L2; on normalised vectors cosine = L2² / 2.
            hits = [(int(r[0]), min(float(r[1]) ** 2 / 2.0, 2.0)) for r in rows]
        else:
            rows = self.conn.execute(
                "SELECT rid, vec FROM fallback_vectors WHERE space = ?", (space,)
            ).fetchall()
            hits = sorted(
                ((int(r[0]), cosine_distance(vector, unpack(r[1]))) for r in rows),
                key=lambda item: item[1],
            )[:k]

        results = []
        for rid, distance in hits:
            row = by_rid.get(rid)
            if row and distance <= max_distance:
                results.append({**row, "distance": round(distance, 5), "space": space})
        return results

    # -- job bookkeeping (`slipbox_schedule`)
    def record_run(self, job: str, started: str, finished: str, outcome: str,
                   detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO job_runs (job, started, finished, outcome, detail) VALUES (?,?,?,?,?) "
            "ON CONFLICT(job) DO UPDATE SET started = excluded.started, "
            "finished = excluded.finished, outcome = excluded.outcome, detail = excluded.detail",
            (job, started, finished, outcome, detail),
        )
        self.conn.commit()

    def runs(self) -> dict[str, dict]:
        try:
            rows = self.conn.execute(
                "SELECT job, started, finished, outcome, detail FROM job_runs"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {
            r[0]: {"started": r[1], "finished": r[2], "outcome": r[3], "detail": r[4]}
            for r in rows
        }


# --- Re-embedding trigger ----------------------------------------------------

def sync(root: Path | None = None, spaces=None, full: bool = False) -> dict:
    """Bring `embeddings.db` in line with the notes on disk.

    Compares each note's path and content hash against the index: gaps are
    embedded point-wise, divergence re-embedded, vanished notes dropped. A fresh
    clone (empty database) therefore rebuilds itself; `full=True` forces a
    rebuild from scratch, which is what a model change requires.
    """
    root = root or config.root()
    spaces = tuple(spaces or config.SPACES)
    report: dict = {"backend": None, "spaces": {}, "embedded": 0, "removed": 0,
                    "unchanged": 0, "failed": 0}

    with Store(root) as store:
        report["backend"] = store.backend
        if full:
            store.reset()
        for space in spaces:
            present = {}
            for note in notes.notes_in_space(root, space):
                present[note.key] = note
            indexed = store.items(space)
            stats = {"embedded": 0, "removed": 0, "unchanged": 0, "failed": 0}

            for key in set(indexed) - set(present):
                store.delete(space, key)
                stats["removed"] += 1

            for key, note in present.items():
                digest = note.hash()
                row = indexed.get(key)
                if row and row["hash"] == digest and row["path"] == note.rel:
                    stats["unchanged"] += 1
                    continue
                try:
                    vector = embed_doc(note.embed_text())
                except EmbeddingError as exc:
                    logger.warning("slipbox: embedding failed for %s: %s", note.rel, exc)
                    stats["failed"] += 1
                    continue
                store.upsert(space, key, note.rel, digest, vector)
                stats["embedded"] += 1

            report["spaces"][space] = stats
            for field in ("embedded", "removed", "unchanged", "failed"):
                report[field] += stats[field]

    report["degraded"] = report["failed"] > 0
    return report
