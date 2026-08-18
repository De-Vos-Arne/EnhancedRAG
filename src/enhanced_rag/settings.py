"""
Settings for the whole project.

Everything is resolved from the project root, so the code runs the same
from a checkout, a venv, uv, or inside Docker. Every value can be
overridden by an environment variable or a `.env` file — see
`.env.example`.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── .env (optional, no dependency) ─────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]

_env_file = ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


def _path(var: str, default: Path) -> Path:
    return Path(os.environ.get(var, default))


# ── Paths ──────────────────────────────────────────────────────────────
DATA_DIR = _path("RAG_DATA_DIR", ROOT / "data")
EXPORT_DIR = _path("RAG_EXPORT_DIR", ROOT / "exports")
RESULTS_DIR = _path("RAG_RESULTS_DIR", ROOT / "results")
STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"

for _d in (DATA_DIR, EXPORT_DIR, RESULTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

ARCHIVE = _path("RAG_ARCHIVE", DATA_DIR / "PersonalArchive.rnt")
CORPUS_DB = _path("RAG_CORPUS_DB", DATA_DIR / "corpus.db")
RESULTS_DB = _path("RAG_RESULTS_DB", RESULTS_DIR / "eval.db")

# ── Scope ─────────────────────────────────────────────────────────────
# Only Belief-System (page_id 28 in this archive) is in scope for the
# thesis — the rest of the archive is private and must never be embedded,
# indexed, retrieved, or exported. Comma-separated page_ids; empty = all
# pages (do not leave empty against the real archive).
SCOPE_PAGE_IDS = tuple(
    int(p) for p in os.environ.get("RAG_SCOPE_PAGE_IDS", "28").split(",") if p.strip()
)

# ── Models ─────────────────────────────────────────────────────────────
# Named presets so a model can be swapped from the UI or the CLI without
# editing code: `--model deepseek`, or pick it in the chat header.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# context_window: the num_ctx we actually REQUEST from Ollama for local
# models. Ollama silently defaults to ~2048-4096 tokens if this isn't set
# explicitly per-request — meaning fragments beyond that get silently
# dropped with no error, regardless of how much you stuff into the prompt.
# These are conservative for a 12GB-VRAM card (larger num_ctx costs more
# VRAM for KV cache, competing with the model weights themselves); raise
# via RAG_CTX_QWEN / RAG_CTX_LLAMA if you have room. Model cards claim much
# larger windows (32K-128K+) but that's architectural capacity, not what
# fits locally. API backends (deepseek, openai-compatible) manage their own
# context server-side, so these numbers there are just what we won't exceed
# when building the prompt, not something we request.
GENERATORS = {
    # Kept for reference/fallback, but no longer the eval's actual model —
    # RAG_GENERATOR=lmstudio in .env overrides DEFAULT_GENERATOR below, so
    # this only gets used if you pass --generator local-qwen explicitly.
    "local-qwen": dict(
        label="Qwen 2.5 7B (local)", backend="ollama",
        model=os.environ.get("RAG_LOCAL_MODEL", "qwen2.5:7b-instruct"),
        base_url=OLLAMA_URL, api_key="",
        context_window=int(os.environ.get("RAG_CTX_QWEN", "8192"))),
    "local-llama": dict(
        label="Llama 3.1 8B (local)", backend="ollama",
        model="llama3.1:8b-instruct-q4_K_M", base_url=OLLAMA_URL, api_key="",
        context_window=int(os.environ.get("RAG_CTX_LLAMA", "8192"))),
    "deepseek": dict(
        label="DeepSeek Chat (API)", backend="openai", model="deepseek-chat",
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        context_window=int(os.environ.get("RAG_CTX_DEEPSEEK", "64000"))),
    "openai-compatible": dict(
        label="Custom OpenAI-compatible endpoint", backend="openai",
        model=os.environ.get("RAG_GEN_MODEL", "gpt-4o-mini"),
        base_url=os.environ.get("RAG_GEN_BASE_URL", ""),
        api_key=os.environ.get("RAG_GEN_API_KEY", ""),
        context_window=int(os.environ.get("RAG_CTX_OPENAI", "128000"))),
    # LM Studio's local server speaks the OpenAI chat-completions API and
    # doesn't check the key, but the client still requires one to be set —
    # any non-empty string works. Model name is whatever LM Studio shows in
    # its server tab for the currently loaded model; RAG_LMSTUDIO_MODEL
    # defaults to "local-model", which LM Studio accepts when only one
    # model is loaded, but set it explicitly if you keep several loaded.
    # Context window: set to what LM Studio itself reports for the loaded
    # model (its context length setting), not the architecture's max — a
    # bigger number here than what's actually loaded will just error.
    # Pinned to the one model this project actually uses via LM Studio.
    # gen_params are llama.cpp/LM Studio server extensions on top of the
    # OpenAI chat-completions schema (top_p and presence_penalty are also
    # standard OpenAI fields; top_k and repeat_penalty are LM Studio/
    # llama.cpp additions) — the Gemma 4 HauhauCS "balanced" preset values
    # per the finetuner's own recommendations.
    "lmstudio": dict(
        label="Gemma 4 26B HauhauCS (LM Studio)", backend="openai",
        model=os.environ.get(
            "RAG_LMSTUDIO_MODEL",
            "gemma4-26b-a4b-uncensored-hauhaucs-balanced-q4_k_p"),
        base_url=os.environ.get("RAG_LMSTUDIO_URL", "http://localhost:1234/v1"),
        api_key=os.environ.get("RAG_LMSTUDIO_API_KEY", "lm-studio"),
        # 16K is the settled figure for the eval: enough headroom for R6/R8
        # (top_k=16 fragments + 3-neighbour expansion + metadata + system
        # prompt, comfortably under 16K tokens) while still fitting a 26B
        # Q4 model's KV cache on a 12GB card. Load the model in LM Studio
        # with a matching context length (`lms load <model> -c 16384 -y`)
        # or this number and what's actually loaded will disagree.
        context_window=int(os.environ.get("RAG_CTX_LMSTUDIO", "16384")),
        gen_params=dict(top_k=64, top_p=0.95, repeat_penalty=1.0,
                        presence_penalty=1.5)),
    # A second LM Studio slot for trying out alternative local finetunes
    # side by side with the pinned Gemma model above (e.g. the Heretic
    # Qwen 3.8 27B build) — both can be loaded in LM Studio at once and
    # picked between in the UI. Sampling params are a starting guess (same
    # as the Gemma preset); adjust RAG_LMSTUDIO2_* once you know what this
    # finetune's own card recommends.
    "lmstudio-2": dict(
        label="Qwen 3.8 27B Heretic (LM Studio)", backend="openai",
        model=os.environ.get("RAG_LMSTUDIO2_MODEL", "qwen3.8-27b-heretic-ara"),
        base_url=os.environ.get("RAG_LMSTUDIO2_URL", "http://localhost:1234/v1"),
        api_key=os.environ.get("RAG_LMSTUDIO2_API_KEY", "lm-studio"),
        context_window=int(os.environ.get("RAG_CTX_LMSTUDIO2", "16384")),
        gen_params=dict(top_k=64, top_p=0.95, repeat_penalty=1.0,
                        presence_penalty=1.5)),
    # OpenRouter proxies many providers behind one OpenAI-compatible API;
    # RAG_OPENROUTER_MODEL must be an exact OpenRouter model slug (e.g.
    # "deepseek/deepseek-chat") — check openrouter.ai/models for the exact
    # slug and context length of whatever model you actually want, since
    # naming and availability there change over time.
    "openrouter": dict(
        label="OpenRouter", backend="openai",
        model=os.environ.get("RAG_OPENROUTER_MODEL", "deepseek/deepseek-chat"),
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ.get("RAG_OPENROUTER_API_KEY", ""),
        context_window=int(os.environ.get("RAG_CTX_OPENROUTER", "64000"))),
}

DEFAULT_GENERATOR = os.environ.get("RAG_GENERATOR", "local-qwen")

EMBEDDERS = {
    "nomic": dict(label="nomic-embed-text (768d)", backend="ollama",
                  model="nomic-embed-text", dims=768, base_url=OLLAMA_URL),
    "minilm": dict(label="all-minilm (384d)", backend="ollama",
                   model="all-minilm", dims=384, base_url=OLLAMA_URL),
    "mxbai": dict(label="mxbai-embed-large (1024d)", backend="ollama",
                  model="mxbai-embed-large", dims=1024, base_url=OLLAMA_URL),
}

DEFAULT_EMBEDDER = os.environ.get("RAG_EMBEDDER", "nomic")


def generator(name: str | None = None) -> dict:
    key = name or DEFAULT_GENERATOR
    if key not in GENERATORS:
        raise KeyError(f"Unknown generator {key!r}. "
                       f"Available: {', '.join(GENERATORS)}")
    return {"key": key, **GENERATORS[key]}


def embedder(name: str | None = None) -> dict:
    key = name or DEFAULT_EMBEDDER
    if key not in EMBEDDERS:
        raise KeyError(f"Unknown embedder {key!r}. "
                       f"Available: {', '.join(EMBEDDERS)}")
    return {"key": key, **EMBEDDERS[key]}


TEMPERATURE = float(os.environ.get("RAG_TEMPERATURE", "0.2"))
# A flat 900 systematically penalizes richer-context regimes (R6/R7/R8):
# they legitimately have more to synthesize, so they're the ones that hit
# this ceiling and get cut off mid-answer, while sparse regimes (R0-R5)
# rarely do — that's an unfairness in the regime's favor, not the model's.
# 2048 gives real headroom while staying well inside the 16K context window
# even for R6/R8's larger prompts.
MAX_TOKENS = int(os.environ.get("RAG_MAX_TOKENS", "2048"))

# ── Web ────────────────────────────────────────────────────────────────
HOST = os.environ.get("RAG_HOST", "127.0.0.1")
PORT = int(os.environ.get("RAG_PORT", "5000"))
ARCHIVE_READONLY = os.environ.get("RAG_ARCHIVE_READONLY", "0") == "1"

# ── Retrieval regimes ──────────────────────────────────────────────────
# The experiment matrix. Each entry changes essentially one thing relative
# to its parent, so a difference in results is attributable to that thing.
# Adding a regime is one dict entry — the UI, the runner and the stats all
# read this and need no other change.
#
#   retriever      'fts' | 'vector' | 'none'
#   embed_field    'plain' (text only) | 'context' (text + breadcrumb path)
#   min_weight     drop candidates below this effective weight (None = all)
#   weight_alpha   score = cos * (1 + alpha * weight / MAX_WEIGHT)
#   section_bonus  same, from the enclosing section's colour
#   prompt_meta    show colour tags and tree paths to the generator
#   explain_colors include the colour legend in the system prompt
#   expand_note    also pull the N highest-weight siblings from each hit's note
#   top_k          fragments passed to the generator
REGIMES = {
    "R0_baseline_fts": dict(
        label="R0 · Keyword baseline (BM25)",
        blurb="Lexical retrieval over plain text. No embeddings, no metadata.",
        retriever="fts", embed_field=None, min_weight=None, weight_alpha=0.0,
        section_bonus=0.0, prompt_meta=False, explain_colors=False,
        expand_note=0, top_k=12),
    "R1_baseline_vector": dict(
        label="R1 · Dense baseline",
        blurb="Plain-text embeddings, cosine ranking. The unmodified RAG control.",
        retriever="vector", embed_field="plain", min_weight=None, weight_alpha=0.0,
        section_bonus=0.0, prompt_meta=False, explain_colors=False,
        expand_note=0, top_k=12),
    "R2_weight_boost": dict(
        label="R2 · + colour-weight ranking",
        blurb="R1 plus the author's highlight weight folded into the score.",
        retriever="vector", embed_field="plain", min_weight=None, weight_alpha=0.6,
        section_bonus=0.0, prompt_meta=False, explain_colors=False,
        expand_note=0, top_k=12),
    "R3_context_embed": dict(
        label="R3 · + tree path in the embedding",
        blurb="R1 but each fragment is embedded with its breadcrumb path.",
        retriever="vector", embed_field="context", min_weight=None, weight_alpha=0.0,
        section_bonus=0.0, prompt_meta=False, explain_colors=False,
        expand_note=0, top_k=12),
    "R4_weight_filter": dict(
        label="R4 · + highlighted-only pool",
        blurb="R2 restricted to fragments the author actually highlighted.",
        retriever="vector", embed_field="plain", min_weight=1.0, weight_alpha=0.6,
        section_bonus=0.0, prompt_meta=False, explain_colors=False,
        expand_note=0, top_k=12),
    "R5_prompt_meta": dict(
        label="R5 · + metadata in the prompt",
        blurb="R2 with colour tags and tree paths shown to the generator.",
        retriever="vector", embed_field="plain", min_weight=None, weight_alpha=0.6,
        section_bonus=0.0, prompt_meta=True, explain_colors=True,
        expand_note=0, top_k=12),
    "R6_full_metadata": dict(
        label="R6 · Full metadata-enhanced",
        blurb="Everything on: context embeddings, weight ranking, section "
              "bonus, note expansion, metadata visible to the generator.",
        retriever="vector", embed_field="context", min_weight=None, weight_alpha=0.6,
        section_bonus=0.25, prompt_meta=True, explain_colors=True,
        expand_note=3, top_k=16),
    "R7_long_context": dict(
        label="R7 · Long-context control",
        blurb="No query-time retrieval — top-weighted fragments stuffed into "
              "the window. The methodological control from the literature.",
        retriever="none", embed_field=None, min_weight=2.0, weight_alpha=0.0,
        section_bonus=0.0, prompt_meta=True, explain_colors=True,
        expand_note=0, top_k=400),
    "R8_pooled_curated": dict(
        label="R8 · Pooled keyword + vector (auto-curated)",
        blurb="R6 but retrieval pools keyword and vector hits before "
              "expansion, instead of vector alone — the automated version "
              "of the manual 'curate for a question' export tool.",
        retriever="pooled", embed_field="context", min_weight=None, weight_alpha=0.6,
        section_bonus=0.25, prompt_meta=True, explain_colors=True,
        expand_note=3, top_k=16),
}

DEFAULT_REGIMES = ["R0_baseline_fts", "R6_full_metadata", "R8_pooled_curated"]

SYSTEM_PROMPT = (
    "You answer questions about a personal knowledge archive, using the "
    "fragments retrieved for you.\n"
    "1. Ground your answer in the fragments. Cite the numbers you used, like "
    "[3] or [7,9].\n"
    "2. Where the fragments are thin, say what the archive does establish and "
    "where it stops, rather than padding. You may connect and interpret what "
    "is there; just be clear about which parts are the archive's and which "
    "are your inference.\n"
    "3. The archive is a draft in progress. Where fragments disagree or a "
    "topic is only half worked out, say so — that is useful information, not "
    "a failure."
)
