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


# --- Host plugin configuration ------------------------------------------------
#
# hermes keeps per-plugin settings under `plugins.entries.slipbox` in its
# `config.yaml`. That is the operator's natural home for what the *deployment*
# decides (which model distils, what it is told to do), as opposed to what the
# *repository* decides (layout, thresholds), which stays in the environment.
#
# Reading it must never be load-bearing: the plugin is importable on a bare
# interpreter with no hermes on the path (the test harness and the CLI rely on
# it), so a missing host, an unreadable config, or a malformed entry all degrade
# to "no plugin config" rather than raising. Resolution order for every setting
# below is therefore: plugin config → environment → shipped default.

_PLUGIN_CONFIG: dict | None = None
PLUGIN_ID = "slipbox"


def plugin_config() -> dict:
    """`plugins.entries.slipbox` from the host's config.yaml — `{}` when absent.

    Cached for the process: hermes reads its config once at startup and a plugin
    that re-read it per tool call would be paying for nothing.
    """
    global _PLUGIN_CONFIG
    if _PLUGIN_CONFIG is None:
        _PLUGIN_CONFIG = _load_plugin_config()
    return _PLUGIN_CONFIG


def _load_plugin_config() -> dict:
    try:  # hermes is absent on a bare interpreter — that is a supported mode
        from hermes_cli.config import load_config  # type: ignore[import-not-found]

        cfg = load_config() or {}
        entries = (cfg.get("plugins") or {}).get("entries") or {}
        entry = entries.get(PLUGIN_ID) or {}
        return entry if isinstance(entry, dict) else {}
    except Exception:  # noqa: BLE001 - any failure means "no plugin config"
        return {}


def _setting(path: str, *env_names: str, default: str = "") -> str:
    """One setting resolved plugin config → environment → default.

    `path` is dotted (`atomizer.model`) and looked up inside the plugin's own
    config entry; `env_names` are the environment fallbacks.
    """
    node: object = plugin_config()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            node = None
            break
        node = node[part]
    if node not in (None, ""):
        return str(node)
    return _env(*env_names, default=default) if env_names else default


def _setting_int(path: str, env_name: str, default: int) -> int:
    try:
        return int(_setting(path, env_name, default=str(default)))
    except (TypeError, ValueError):
        return default


def _setting_float(path: str, env_name: str, default: float) -> float:
    try:
        return float(_setting(path, env_name, default=str(default)))
    except (TypeError, ValueError):
        return default


def _setting_bool(path: str, env_name: str, default: bool) -> bool:
    raw = _setting(path, env_name, default="").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


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


def semantic_venv() -> Path | None:
    """A virtualenv holding the heavy model stack, mounted into this process.

    The plugin loads *in-process* inside the host's interpreter, which normally
    has no torch/FlagEmbedding. Pointing at a venv built from that same
    interpreter lets the semantic layer work without a launcher wrapper setting
    `PYTHONPATH` — see `models.mount_semantic_venv`.

    Not a substitute for `LD_LIBRARY_PATH`: on NixOS, foreign torch still needs
    libstdc++ from the dynamic linker, which only the environment can provide
    because the linker reads it at exec time, before any Python runs.
    """
    raw = _setting("semantic.venv", "SLIPBOX_SEMANTIC_VENV").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def native_lib_dirs() -> list[Path]:
    """Where to look for `libstdc++.so.6` when the dynamic linker cannot find it.

    Colon-separated `SLIPBOX_NATIVE_LIBS`, else the nix-ld directory NixOS keeps
    at a stable system path. Used only as a fallback, when torch has already
    failed to import for exactly that reason.
    """
    raw = _setting("semantic.native_libs", "SLIPBOX_NATIVE_LIBS")
    parts = [p.strip() for p in raw.split(":") if p.strip()] if raw else [
        "/run/current-system/sw/share/nix-ld/lib",
    ]
    return [Path(p).expanduser() for p in parts]


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


def readonly() -> bool:
    """Expose only the read surface — the read-only tools, the `slipbox_search` /
    `slipbox_quote` read path, and the `search` skill — with every write tool and
    write skill withheld and the setup/commit hooks disabled.

    Set per profile (`SLIPBOX_READONLY=1`) so one agent can query a store it must
    not modify, while another (or the scheduled jobs) does the writing.
    """
    return _bool("SLIPBOX_READONLY", False)


# --- The atomiser: the dedicated distillation agent ---------------------------
#
# Atomisation is the whitepaper's irreplaceable operation, and it is *not* the
# host agent's job: a conversational model distilling inline blocks the
# conversation, inherits whatever context happens to be in the window, and makes
# a nightly unattended run impossible. So distillation runs as a dedicated agent
# — its own model, its own instructions, its own context, off the conversation —
# behind `atomizer.py`. Every trigger routes through it: the tool, the slash
# command, the CLI, and the auto-adapt cron job alike.
#
# The deployment decides two things, both from plugin config (`atomizer.model`,
# `atomizer.instructions`), which is why the reader above exists.

# --- The skill bundle ---------------------------------------------------------
#
# One slash command that loads the plugin's skills together. Written by the
# plugin at startup rather than installed by hand, so it tracks the skill set
# instead of drifting from it — see `bundle.py`.

def bundle_enabled() -> bool:
    return _setting_bool("bundle.enabled", "SLIPBOX_BUNDLE", default=True)


def bundle_name() -> str:
    """The slash command (`/slipbox`). A bundle outranks a like-named skill."""
    return _setting("bundle.name", "SLIPBOX_BUNDLE_NAME", default=PLUGIN_ID)


def bundle_skills() -> list[str]:
    """Narrow the bundle to these skills; empty means every registered one."""
    raw = _setting("bundle.skills", "SLIPBOX_BUNDLE_SKILLS")
    return [part.strip() for part in raw.split(",") if part.strip()]


def bundle_description() -> str:
    return _setting(
        "bundle.description", "SLIPBOX_BUNDLE_DESCRIPTION",
        default="The slipbox knowledge base — the CARP write path "
                "(capture, adapt, review, persist), retrieval, and the "
                "higher-level maintenance workflows.",
    )


def bundle_instruction() -> str:
    """Guidance injected above the skill bodies when the bundle is invoked."""
    return _setting("bundle.instruction", "SLIPBOX_BUNDLE_INSTRUCTION")


ATOMIZER_LOCAL = "local"      # the in-process judge (models.judge_generate)
ATOMIZER_HOST = "host"        # the host's LLM lane (ctx.llm), hermes-routed
ATOMIZER_BACKENDS = (ATOMIZER_LOCAL, ATOMIZER_HOST)


def atomizer_enabled() -> bool:
    """Whether distillation is delegated to the dedicated agent (default: yes).

    Turning it off restores the old behaviour — the host agent does the reasoning
    by following the `adapt` skill by hand — which is the escape hatch when the
    configured model is unreachable and a human wants the work done anyway.
    """
    return _setting_bool("atomizer.enabled", "SLIPBOX_ATOMIZER", default=True)


def atomizer_backend() -> str:
    """Where the dedicated agent runs: `local` (in-process judge) or `host`.

    `local` keeps the whole operation on this machine — no content leaves the
    box, which is the default a private knowledge base deserves. `host` hands the
    call to hermes' own LLM lane (`ctx.llm`), which owns provider routing and
    auth; steering its model needs the operator's trust flags
    (`plugins.entries.slipbox.llm.allow_model_override`).
    """
    value = _setting("atomizer.backend", "SLIPBOX_ATOMIZER_BACKEND",
                     default=ATOMIZER_LOCAL).strip().lower()
    return value if value in ATOMIZER_BACKENDS else ATOMIZER_LOCAL


ATOMIZER_DEFAULT_MODEL = "Qwen/Qwen3-4B-Instruct-2507"


def atomizer_model() -> str:
    """The model that distils — a role of its own, not the judge reference.

    The whitepaper names a ~24B generalist as the judge, and that is right for
    the cited summaries a human reads interactively. It is the wrong instrument
    for this job: atomisation is an unattended batch on CPU (no CUDA here), where
    decoding is bound by memory traffic, so a 24B spends tens of minutes per
    entry and the token budget is exhausted before a plan is finished.

    The default is therefore a small *instruct* model — chosen for throughput and
    for reliably emitting the one JSON object the contract asks for. Deliberately
    a non-reasoning variant: hybrid models spend the budget on a `<think>` block
    nobody parses. Point `atomizer.model` at the judge to have one model do both.
    """
    return _setting("atomizer.model", "SLIPBOX_ATOMIZER_MODEL",
                    default=ATOMIZER_DEFAULT_MODEL)


def atomizer_provider() -> str:
    """Provider for the `host` backend; empty means the host's own routing."""
    return _setting("atomizer.provider", "SLIPBOX_ATOMIZER_PROVIDER")


def atomizer_instructions() -> str:
    """What the dedicated agent is told to do — the composition contract.

    Resolution: plugin config (`atomizer.instructions`, inline text *or* a path)
    → `$SLIPBOX_ATOMIZER_INSTRUCTIONS` (likewise) → the shipped
    `templates/ATOMIZER.md`. Shipping a default matters: the contract is the
    whitepaper's, and a deployment that overrides it is deliberately changing
    what the store means, never merely tweaking a prompt.
    """
    raw = _setting("atomizer.instructions", "SLIPBOX_ATOMIZER_INSTRUCTIONS").strip()
    if raw:
        # A single line that resolves to a readable file is a path; anything else
        # (notably multi-line YAML) is the instructions themselves.
        if "\n" not in raw:
            candidate = Path(raw).expanduser()
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        return raw
    shipped = Path(__file__).resolve().parent / "templates" / "ATOMIZER.md"
    return shipped.read_text(encoding="utf-8") if shipped.is_file() else ""


def atomizer_max_atoms() -> int:
    """Upper bound on atoms from one entry — the relevance test, made mechanical.

    The contract says *better three sharp notes than ten restatements*; a ceiling
    stops a verbose model turning one article into a wall of near-duplicates.
    """
    return _setting_int("atomizer.max_atoms", "SLIPBOX_ATOMIZER_MAX_ATOMS", default=12)


def atomizer_max_chars() -> int:
    """How much of an entry is handed to the model in one pass."""
    return _setting_int("atomizer.max_chars", "SLIPBOX_ATOMIZER_MAX_CHARS", default=24000)


def atomizer_max_tokens() -> int:
    """Generation ceiling — the plan is JSON, and a truncated plan is unusable.

    Kept *reachable within* `atomizer_timeout`, which is the whole point of the
    number. The local judge runs on CPU at single-digit tokens per second (the
    GPU belongs to the conversational model), so a ceiling the time budget can
    never reach is not a safety limit — it just guarantees every distillation
    ends as a truncated plan. At the measured ~1.5 tok/s these two defaults sit
    either side of the same wall: 2048 tokens ≈ 1365 s, inside the 1800 s budget.
    Raise them together, or not at all.
    """
    return _setting_int("atomizer.max_tokens", "SLIPBOX_ATOMIZER_MAX_TOKENS", default=2048)


def atomizer_temperature() -> float:
    """Near-greedy: distillation wants faithfulness, not invention."""
    return _setting_float("atomizer.temperature", "SLIPBOX_ATOMIZER_TEMPERATURE", default=0.1)


def atomizer_timeout() -> float:
    """Seconds one distillation may take before it is abandoned as failed.

    A real wall-clock bound (`MaxTimeCriteria` on the local path), covering the
    whole proposal including retries. Sized against `atomizer_max_tokens` at CPU
    speed — see there. Generous because this is a background job nobody waits on;
    the cost of being too tight is a plan cut off mid-JSON, which yields nothing.
    """
    return _setting_float("atomizer.timeout", "SLIPBOX_ATOMIZER_TIMEOUT", default=1800.0)


def atomizer_candidates() -> int:
    """How many related store notes the agent is shown as placement candidates."""
    return _setting_int("atomizer.candidates", "SLIPBOX_ATOMIZER_CANDIDATES", default=12)


def atomizer_retries() -> int:
    """Re-asks allowed when the model returns unusable JSON (schema repair)."""
    return _setting_int("atomizer.retries", "SLIPBOX_ATOMIZER_RETRIES", default=2)


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


def positional_distance() -> float:
    """Where a positional-only candidate ranks on the embedder's distance scale.

    The positional layer sweeps whole threads, so it nominates notes the embedder
    never scored — they have no distance of their own. Ranking them best would
    let a topic sweep bury the nearest neighbour; ranking them worst would waste
    the layer that exists precisely to find notes sharing no vocabulary with the
    query. Calibrated against bge-m3, where a genuine answer sits at ~0.2–0.45
    and unrelated prose at ~0.65+: 0.55 places them just inside the plausible
    band — below anything the embedder actually liked, above what it rejected.
    """
    return _float("SLIPBOX_POSITIONAL_DISTANCE", 0.55)


def both_layers_bonus() -> float:
    """How much agreement between the two layers improves a candidate's rank.

    A *prior*, not a verdict (whitepaper: "vectors nominate, a reader decides").
    Subtracted from the effective distance, so corroboration promotes a note past
    near-equals without letting it leapfrog a much closer one. It used to be the
    primary sort key, which — with the result truncated to `candidate_limit`
    afterwards — made it a filter in all but name.
    """
    return _float("SLIPBOX_BOTH_LAYERS_BONUS", 0.05)


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


def git_identity() -> tuple[str, str]:
    """Author of last resort for the repository first-run setup creates.

    Git refuses to commit without an identity, and the scheduled jobs run where
    no global one may be configured — an unattended nightly distillation must not
    lose its audit trail to `Please tell me who you are`. Written into the new
    repository's own config only, and never over an identity that already exists.
    """
    return (_env("SLIPBOX_GIT_NAME", default="slipbox"),
            _env("SLIPBOX_GIT_EMAIL", default="slipbox@localhost"))
