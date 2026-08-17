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


_LOCK = threading.Lock()
_EMBEDDER = None
_RERANKER = None
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
            try:
                from FlagEmbedding import BGEM3FlagModel  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - exercised only with the dep
                raise ModelUnavailable(
                    f"FlagEmbedding is not installed — cannot load {config.embed_model()} "
                    "(`pip install FlagEmbedding`)"
                ) from exc
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
            try:
                from FlagEmbedding import FlagReranker  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover
                raise ModelUnavailable(
                    f"FlagEmbedding is not installed — cannot load {config.reranker_model()}"
                ) from exc
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
    try:
        reranker()
        return True
    except ModelUnavailable:
        return False


# --- Generative judge --------------------------------------------------------

def judge():
    """Load the generative judge (the headless in-process fallback)."""
    global _JUDGE
    if _JUDGE is not None:
        return _JUDGE
    with _LOCK:
        if _JUDGE is None:
            try:
                import torch  # type: ignore[import-not-found]  # noqa: F401
                from transformers import (  # type: ignore[import-not-found]
                    AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                )
            except ImportError as exc:  # pragma: no cover
                raise ModelUnavailable(
                    "transformers/bitsandbytes are not installed — cannot load "
                    f"the judge {config.judge_model()}"
                ) from exc
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


def judge_generate(messages: list[dict], max_new_tokens: int = 768) -> str:
    """Run the judge on a chat-style prompt and return its text.

    `messages` is the OpenAI-style `[{"role": ..., "content": ...}]` list. The
    headless fallback for composing a cited summary when no host model is
    available to do it (normally the host agent does this itself).
    """
    model, tokenizer = judge()
    import torch  # type: ignore[import-not-found]

    inputs = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt"
    ).to(model.device)
    with torch.no_grad():
        generated = model.generate(
            inputs, max_new_tokens=max_new_tokens, do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    text = tokenizer.decode(generated[0][inputs.shape[-1]:], skip_special_tokens=True)
    return text.strip()


def judge_available() -> bool:
    try:
        judge()
        return True
    except ModelUnavailable:
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
