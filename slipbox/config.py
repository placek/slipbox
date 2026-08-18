"""Configuration of the `slipbox` plugin — repository layout and tunables.

Everything is resolved from the environment with sane defaults, so a deployment
tunes the mechanism (k, thresholds, N, the cron expressions) without touching
code. The whitepaper's §"Repository and formats" and §"Semantic layer" are the
reference for the constants here.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- Repository layout (whitepaper §"Storage model") -------------------------
#
# The layout reads as the lifecycle itself — CARP: Capture → Adapt → Review &
# Persist (`inbox → stage → store`) — with `source/` as the single side
# collection. A `source/` note holds the provenance *metadata*; the provenance
# itself, in full, sits beside it as that note's attachment
# (`source/.attachments/<slug>/`) — the cold-storage "originals" layer.

INBOX = "inbox"
INBOX_ATTACHMENTS = "inbox/.attachments"
STAGE = "stage"
STAGE_ATTACHMENTS = "stage/.attachments"
STORE = "store"
STORE_ATTACHMENTS = "store/.attachments"
SOURCE = "source"
SOURCE_ATTACHMENTS = "source/.attachments"  # bibliography notes + cold originals
INDEX_FILE = "index.md"
SOUL_FILE = "SOUL.md"
DB_FILE = "embeddings.db"
LOCK_DIR = ".slipbox-locks"

# Directories created by `ensure_layout` / on first write.
DIRECTORIES = (
    INBOX, INBOX_ATTACHMENTS, STAGE, STAGE_ATTACHMENTS, STORE, STORE_ATTACHMENTS,
    SOURCE, SOURCE_ATTACHMENTS,
)

# Vector spaces (whitepaper §"Semantic layer") — one sqlite-vec table per space,
# so "similar thought", "matching source" and "fresh material" stay distinct
# query spaces, never post-filtered from one polluted ranking.
SPACE_STORE = "store"
SPACE_SOURCE = "source"
SPACE_STAGE = "stage"
SPACES = (SPACE_STORE, SPACE_SOURCE, SPACE_STAGE)

# Review statuses of `stage/` entries (whitepaper §"Human review").
REVIEW_PENDING = "pending"
REVIEW_ACCEPTED = "accepted"
REVIEW_REJECTED = "rejected"
REVIEW_STATUSES = (REVIEW_PENDING, REVIEW_ACCEPTED, REVIEW_REJECTED)

# Extraction statuses of `inbox/` entries (whitepaper §"Capture").
EXTRACTION_OK = "ok"
EXTRACTION_PARTIAL = "partial"
EXTRACTION_FAILED = "failed"
EXTRACTION_STATUSES = (EXTRACTION_OK, EXTRACTION_PARTIAL, EXTRACTION_FAILED)

# Domain-charter scope of a captured source (whitepaper §"Multi-user operation").
SCOPE_IN = "in"
SCOPE_ADJACENT = "adjacent"
SCOPE_OUT = "out"
SCOPES = (SCOPE_IN, SCOPE_ADJACENT, SCOPE_OUT)

SOURCE_TYPES = ("book", "article", "web", "video", "audio", "other")

# The sentinel a stage atom's `id` list carries when the agent's preferred
# placement is a brand-new top-level thread rather than an existing position.
NEW_THREAD = "new-thread"


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def root() -> Path:
    """Root of the slipbox repository.

    `$SLIPBOX_REPO` / `$SLIPBOX_ROOT`, otherwise the parent directory of the
    plugin package (deployment: `<repo>/slipbox`).
    """
    value = _env("SLIPBOX_REPO", "SLIPBOX_ROOT")
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def db_path() -> Path:
    return root() / DB_FILE


# --- Multiple repositories (named knowledge bases) ---------------------------
#
# `SLIPBOX_REPOS="work=/kb/work,personal=/kb/personal"` exposes several stores
# through one plugin instance. A tool's `repo` argument selects one; the FIRST
# configured entry is the default when a call names none. Each repo is fully
# self-isolated (its own `embeddings.db`, `.slipbox-locks/`, `index.md`, `SOUL.md`).
# With the variable unset the plugin stays single-repo (`root()`), unchanged.

def repos() -> dict[str, Path]:
    """Ordered map of configured repositories (insertion order; first = default).

    Empty in single-repo mode. Duplicate names keep their first definition.
    """
    registry: dict[str, Path] = {}
    for pair in _env("SLIPBOX_REPOS").split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, path = pair.split("=", 1)
        name = name.strip()
        if name and name not in registry:
            registry[name] = Path(path.strip()).expanduser().resolve()
    return registry


def default_repo() -> str | None:
    """The default repo name — the first configured, or None in single-repo mode."""
    return next(iter(repos()), None)


def active_repo_name(name: str | None = None) -> str | None:
    """The effective repo name a call targets: the given one, else the default."""
    return None if not repos() else ((name or "").strip() or default_repo())


def repo_root(name: str | None = None) -> Path:
    """Resolve a repo name to its root. None/empty selects the default (first) repo.

    In single-repo mode (no `SLIPBOX_REPOS`) always returns `root()`. Raises
    KeyError on an unknown name.
    """
    registry = repos()
    if not registry:
        return root()
    key = (name or "").strip() or default_repo()
    if key not in registry:
        raise KeyError(f"unknown repo '{key}' — configured: {', '.join(registry) or 'none'}")
    return registry[key]


def repo_items() -> list[tuple[str | None, Path]]:
    """`(name, root)` for every configured repo — for jobs/setup/hooks that loop.

    Single-repo mode yields one `(None, root())`.
    """
    registry = repos()
    return list(registry.items()) if registry else [(None, root())]


# --- Semantic layer (whitepaper §"Semantic layer") ---------------------------
#
# Exactly three model classes, all loaded **in-process** (FlagEmbedding /
# transformers), no external inference server:
#   - embedder: BAAI/bge-m3 — multilingual dense 1024-dim vectors,
#   - reranker: BAAI/bge-reranker-v2-m3 — a cross-encoder scoring the bisection
#     probes, topic matching and the candidate shortlist,
#   - judge: a ~24B generalist — the irreplaceable operations only: atomisation,
#     the final candidate verdict, the cited summary.

def embed_model() -> str:
    return _env("SLIPBOX_EMBED_MODEL", default="BAAI/bge-m3")


def reranker_model() -> str:
    return _env("SLIPBOX_RERANK_MODEL", default="BAAI/bge-reranker-v2-m3")


def judge_model() -> str:
    return _env("SLIPBOX_JUDGE_MODEL", default="mistralai/Mistral-Small-Instruct-2409")


def use_fp16() -> bool:
    """Load the embedder/reranker in FP16 (needs CUDA); CPU falls back to FP32."""
    return _bool("SLIPBOX_FP16", True)


def model_device() -> str:
    """Torch device for the in-process models ('' = auto: cuda if available)."""
    return os.environ.get("SLIPBOX_DEVICE", "")


def query_prefix() -> str:
    """Task prefix for query embeddings (bge-m3 needs none; kept for flexibility)."""
    return os.environ.get("SLIPBOX_QUERY_PREFIX", "")


def doc_prefix() -> str:
    return os.environ.get("SLIPBOX_DOC_PREFIX", "")


def semantic_enabled() -> bool:
    """Whether the model-gated read path (`slipbox_search`, `slipbox_quote`) is on.

    The whitepaper reserves the cited-summary path for when the three models are
    actually reachable; the plugin registers it only then, so a modelless
    deployment advertises the deterministic surface alone.
    """
    return _bool("SLIPBOX_SEMANTIC", True)


# --- Shared lookup mechanism (whitepaper §"Retrieval Architecture") ----------

def top_k() -> int:
    """k for the per-space k-NN search."""
    return _int("SLIPBOX_TOP_K", 10)


def distance_max() -> float:
    """Maximum cosine distance of a vector hit (0 = identical, 2 = opposite)."""
    return _float("SLIPBOX_DISTANCE_MAX", 0.75)


def window() -> int:
    """N — half-width of the ID window bisection returns (return window = 2N+1)."""
    return _int("SLIPBOX_WINDOW", 3)


def leaf_pool() -> int:
    """~50: the leaf-pool size the bookmark index always descends to."""
    return _int("SLIPBOX_LEAF_POOL", 50)


def probe_budget() -> int:
    """Hard cap on notes the bisection reads per run (probe budget)."""
    return _int("SLIPBOX_PROBE_BUDGET", 10)


def reranker_hit() -> float:
    """Normalised reranker score at/above which a probe counts as on-topic."""
    return _float("SLIPBOX_RERANK_HIT", 0.55)


def reranker_tangent() -> float:
    """Below this normalised score a probe is 'off' (between the two: tangential)."""
    return _float("SLIPBOX_RERANK_TANGENT", 0.30)


def candidate_limit() -> int:
    """Upper bound on candidates handed to the judge for content comparison."""
    return _int("SLIPBOX_CANDIDATE_LIMIT", 20)


def duplicate_distance() -> float:
    """Cosine distance below which a fresh atom is flagged a potential duplicate.

    Deliberately sensitive (whitepaper §"Human review"): a false alarm costs one
    expansion at review, a missed duplicate costs permanent noise. Calibrated
    against bge-m3: a paraphrase of the same idea sits at ~0.13 cosine distance, a
    related-but-distinct note at ~0.46 — so 0.20 catches twins with wide margin.
    """
    return _float("SLIPBOX_DUPLICATE_DISTANCE", 0.20)


# --- Backpressure (whitepaper §"Concurrency and automation") -----------------

def pending_warn_threshold() -> int:
    """Pending-review backlog at which capture acknowledges with a warning."""
    return _int("SLIPBOX_PENDING_WARN", 40)


# --- Automation (whitepaper §"Concurrency and automation") -------------------

def lock_timeout() -> float:
    """How long a job waits for a flock before giving up (seconds)."""
    return _float("SLIPBOX_LOCK_TIMEOUT", 30.0)


def cron_schedules() -> dict[str, str]:
    """Cron expressions of the three scheduled jobs, as configured on the host."""
    return {
        "auto-adapt": _env("SLIPBOX_CRON_ADAPT", default="0 22 * * *"),
        "persist": _env("SLIPBOX_CRON_PERSIST", default="0 3 * * *"),
        "digest": _env("SLIPBOX_CRON_DIGEST", default="0 7 * * *"),
    }


def autocommit() -> bool:
    return _bool("SLIPBOX_AUTOCOMMIT", True)
