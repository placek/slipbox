"""In-process semantic models — no server, no Ollama, no llama.cpp.

The whitepaper's §"Semantic layer": exactly three model classes, each a lazily
loaded singleton so importing the plugin (or running the tests) never pulls
torch or downloads weights.

* **embedder** — ``BAAI/bge-m3``: multilingual dense 1024-dim vectors. Insures
  the whole index; degrades gracefully (three other channels back it up).
* **reranker** — ``BAAI/bge-reranker-v2-m3``: a light cross-encoder scoring the
  bisection probes, topic matches and the candidate shortlist.
* **judge** — a ~24B generalist: the generative model for the irreplaceable
  operations (atomisation, the final candidate verdict, cited summaries). In the
  normal hermes deployment the host agent *is* the judge — it runs the skills
  directly, so this loader stays cold. It exists only as an in-process fallback
  for headless operation (a scheduled job, or the CLI with no conversational
  host), so a cited answer can still be produced without a live agent.

A missing dependency or unavailable device raises :class:`ModelUnavailable`,
which callers translate into a soft, reported degradation — never silently wrong
results. The GPU belongs to the conversational model: the embedder and reranker
default to CPU, the judge is lazy-loaded and rarely resident.
"""
from __future__ import annotations

import logging
import threading

from . import config

logger = logging.getLogger(__name__)


class ModelUnavailable(RuntimeError):
    """An in-process model could not be loaded (missing library or device)."""


_VENV_MOUNTED = False
_IMPORT_HINT = ""


def mount_semantic_venv() -> str | None:
    """Put the configured semantic venv on `sys.path`, ahead of the host's own.

    The plugin runs inside the host agent's interpreter, which has no torch. The
    launcher wrapper solves that with `PYTHONPATH`, but only for sessions started
    through it — start `hermes` from anywhere else and the semantic layer
    silently degrades. Doing it here instead makes the venv follow the plugin
    rather than the launch command.

    It must be *prepended*: the host ships its own, newer `huggingface_hub`, and
    on a plain append that one shadows the venv's, leaving `transformers`
    refusing to import against a version it does not support. `PYTHONPATH` has
    the same precedence, which is why the wrapper never hit this.

    Returns the path mounted, or None. Idempotent, and cheap enough to call from
    every loader.
    """
    global _VENV_MOUNTED
    if _VENV_MOUNTED:
        return None
    _VENV_MOUNTED = True
    venv = config.semantic_venv()
    site = None
    if venv is not None:
        import sys

        candidates = sorted(venv.glob("lib/python3.*/site-packages"))
        site = next((str(p) for p in candidates if p.is_dir()), None)
        if site is None:
            logger.warning("slipbox: no site-packages under %s", venv)
        elif site not in sys.path:
            sys.path.insert(0, site)
            logger.info("slipbox: mounted semantic venv %s", site)
    _ensure_cxx_runtime()
    return site


def _ensure_cxx_runtime() -> str | None:
    """Load the C++ runtime into the process when the linker did not.

    A foreign-built torch needs `libstdc++.so.6`, which on NixOS lives outside
    the default search path. The usual fix is `LD_LIBRARY_PATH`, but that has to
    be set *before* exec — so a session started without the launcher wrapper is
    unfixable from inside... unless the library is loaded explicitly. `dlopen`
    with `RTLD_GLOBAL` does exactly that: it publishes the symbols into the
    global scope, and torch's extension then resolves against them when it is
    imported afterwards.

    The need is probed by asking the linker for the library *directly*, never by
    trying to import torch: a torch import that fails this way leaves partially
    initialised `torch._C` state behind in `sys.modules`, and every later import
    then fails in confusing, unrelated-looking ways (a missing `PreTrainedModel`,
    say) that no longer mention the real cause.
    """
    import ctypes

    try:
        ctypes.CDLL("libstdc++.so.6", mode=ctypes.RTLD_GLOBAL)
        return None  # the linker resolves it already; do not interfere
    except OSError:
        pass

    for directory in config.native_lib_dirs():
        candidate = directory / "libstdc++.so.6"
        if not candidate.is_file():
            continue
        try:
            ctypes.CDLL(str(candidate), mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:  # noqa: PERF203 - try the next candidate
            logger.debug("slipbox: could not preload %s (%s)", candidate, exc)
            continue
        logger.info("slipbox: preloaded C++ runtime %s", candidate)
        return str(candidate)
    logger.warning(
        "slipbox: libstdc++ is not loadable and no candidate was found "
        "(set SLIPBOX_NATIVE_LIBS, or launch via ./hermes-slipbox)"
    )
    return None


def _import_failure(exc: Exception, what: str) -> ModelUnavailable:
    """Translate an import failure into an error that names the actual remedy."""
    text = str(exc)
    if "libstdc++" in text or "cannot open shared object" in text:
        return ModelUnavailable(
            f"{what}: the model libraries are present but the C++ runtime is not "
            "loadable (libstdc++). LD_LIBRARY_PATH must be set *before* the "
            "process starts — the dynamic linker reads it at exec, so no "
            "in-process fix is possible. Launch via ./hermes-slipbox, or from a "
            f"directory where direnv applies .envrc ({text})"
        )
    return ModelUnavailable(
        f"{what}: {text}. Point SLIPBOX_SEMANTIC_VENV at a venv built from this "
        "interpreter, or install the deps into it (slipbox/requirements.txt)"
    )


_LOCK = threading.Lock()
_EMBEDDER = None
_RERANKER = None
_RERANKER_OK: bool | None = None  # cached score-probe result (None = not yet probed)
_JUDGE = None


def _resolve_device() -> str:
    configured = config.model_device()
    if configured:
        return configured
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


# --- Embedder: BAAI/bge-m3 ---------------------------------------------------

def embedder():
    """Return the singleton bge-m3 model, loading it on first use."""
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    with _LOCK:
        if _EMBEDDER is None:
            mount_semantic_venv()
            try:
                from FlagEmbedding import BGEM3FlagModel  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - exercised only with the dep
                raise _import_failure(exc, f"cannot load {config.embed_model()}") from exc
            fp16 = config.use_fp16() and _resolve_device() != "cpu"
            logger.info("slipbox: loading embedder %s (fp16=%s)", config.embed_model(), fp16)
            _EMBEDDER = BGEM3FlagModel(config.embed_model(), use_fp16=fp16)
    return _EMBEDDER


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Dense embeddings for a batch of texts (raises :class:`ModelUnavailable`)."""
    model = embedder()
    output = model.encode(list(texts), return_dense=True, return_sparse=False,
                          return_colbert_vecs=False)
    dense = output["dense_vecs"] if isinstance(output, dict) else output
    return [list(map(float, vector)) for vector in dense]


# --- Reranker: BAAI/bge-reranker-v2-m3 ---------------------------------------

def reranker():
    global _RERANKER
    if _RERANKER is not None:
        return _RERANKER
    with _LOCK:
        if _RERANKER is None:
            mount_semantic_venv()
            try:
                from FlagEmbedding import FlagReranker  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover
                raise _import_failure(exc, f"cannot load {config.reranker_model()}") from exc
            fp16 = config.use_fp16() and _resolve_device() != "cpu"
            logger.info("slipbox: loading reranker %s (fp16=%s)", config.reranker_model(), fp16)
            _RERANKER = FlagReranker(config.reranker_model(), use_fp16=fp16)
    return _RERANKER


def rerank_scores(query: str, documents: list[str]) -> list[float]:
    """Normalised (0..1) cross-encoder scores of ``query`` against each document."""
    if not documents:
        return []
    model = reranker()
    scores = model.compute_score([[query, doc] for doc in documents], normalize=True)
    if not isinstance(scores, (list, tuple)):
        scores = [scores]
    return [float(s) for s in scores]


def reranker_available() -> bool:
    """Whether the reranker can actually SCORE — not merely load.

    A cross-encoder that constructs but throws at score time (e.g. a
    FlagEmbedding × transformers version clash) must report unavailable, so the
    positional layer falls back to token overlap instead of crashing the lookup.
    The probe runs once per process and the boolean is cached, so this stays
    cheap on the hot lookup path.
    """
    global _RERANKER_OK
    if _RERANKER_OK is not None:
        return _RERANKER_OK
    try:
        rerank_scores("ping", ["pong"])
        _RERANKER_OK = True
    except ModelUnavailable:
        _RERANKER_OK = False
    except Exception as exc:  # noqa: BLE001 - a broken reranker is an unavailable one
        logger.warning(
            "slipbox: reranker %s loaded but cannot score (%s) — using token overlap",
            config.reranker_model(), exc,
        )
        _RERANKER_OK = False
    return _RERANKER_OK


# --- Generative judge --------------------------------------------------------

def judge():
    """Load the generative judge (the headless in-process fallback)."""
    global _JUDGE
    if _JUDGE is not None:
        return _JUDGE
    with _LOCK:
        if _JUDGE is None:
            mount_semantic_venv()
            try:
                import torch  # type: ignore[import-not-found]  # noqa: F401
                from transformers import (  # type: ignore[import-not-found]
                    AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                )
            except ImportError as exc:  # pragma: no cover
                raise _import_failure(exc, f"cannot load the judge {config.judge_model()}") from exc
            name = config.judge_model()
            logger.info("slipbox: loading judge %s (NF4)", name)
            quant = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype="float16",
            )
            tokenizer = AutoTokenizer.from_pretrained(name)
            model = AutoModelForCausalLM.from_pretrained(
                name, quantization_config=quant, device_map="auto",
            )
            _JUDGE = (model, tokenizer)
    return _JUDGE


def judge_generate(messages: list[dict], max_new_tokens: int = 768,
                   max_seconds: float | None = None) -> str:
    """Run the judge on a chat-style prompt and return its text.

    `messages` is the OpenAI-style `[{"role": ..., "content": ...}]` list.

    `max_seconds` is a real wall-clock bound, not advice. The judge normally runs
    on CPU — the GPU belongs to the conversational model — at single-digit tokens
    per second, so `max_new_tokens` alone bounds nothing anyone can plan around:
    4096 tokens at 1.5 tok/s is three quarters of an hour. `MaxTimeCriteria`
    stops generation mid-stream instead, which is why a caller can be promised
    that a distillation either finishes or fails within a configured time.
    """
    model, tokenizer = judge()
    import torch  # type: ignore[import-not-found]

    inputs = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt"
    ).to(model.device)

    criteria = None
    if max_seconds and max_seconds > 0:
        try:
            from transformers import (  # type: ignore[import-not-found]
                MaxTimeCriteria, StoppingCriteriaList,
            )

            criteria = StoppingCriteriaList([MaxTimeCriteria(max_time=float(max_seconds))])
        except ImportError:  # pragma: no cover - older transformers
            logger.debug("slipbox: MaxTimeCriteria unavailable; generation is unbounded")

    with torch.no_grad():
        generated = model.generate(
            inputs, max_new_tokens=max_new_tokens, do_sample=False,
            pad_token_id=tokenizer.eos_token_id, stopping_criteria=criteria,
        )
    text = tokenizer.decode(generated[0][inputs.shape[-1]:], skip_special_tokens=True)
    return text.strip()


def judge_available() -> bool:
    try:
        judge()
        return True
    except ModelUnavailable:
        return False


def import_hint() -> str:
    """Why the last cheap probe failed, phrased as a remedy (empty if it passed)."""
    return _IMPORT_HINT


def judge_resident() -> bool:
    """Whether the judge is already loaded. Free — touches nothing."""
    return _JUDGE is not None


def judge_importable() -> bool:
    """Can the judge's libraries be imported at all?

    A *cheap* liveness probe: it answers "would loading stand a chance" without
    pulling ~24B of weights. Status surfaces (`doctor`, the atomiser's backend
    report) must never trigger a real load — a health check that costs minutes of
    GPU time and gigabytes of RAM is one nobody can afford to run.
    """
    mount_semantic_venv()
    try:
        import torch  # type: ignore[import-not-found]  # noqa: F401
        import transformers  # type: ignore[import-not-found]  # noqa: F401

        return True
    except ImportError as exc:
        # Distinguish "no libraries" from "libraries present, C++ runtime not
        # loadable" — they have completely different remedies, and reporting the
        # first when it is really the second sends you installing what you have.
        global _IMPORT_HINT
        _IMPORT_HINT = str(_import_failure(exc, "judge"))
        return False


def unload_judge() -> None:
    """Release the judge's VRAM (it is lazy-loaded and rarely resident)."""
    global _JUDGE
    with _LOCK:
        _JUDGE = None
    try:
        import torch  # type: ignore[import-not-found]

        torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 - best effort
        pass
