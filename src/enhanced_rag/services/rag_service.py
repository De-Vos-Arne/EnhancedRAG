"""
The RAG service — the one thing the web layer and the CLI both call.

Holds a corpus and a set of model clients, and answers a query under a
named regime. Model swapping happens here: `answer(..., generator="deepseek")`
builds a client on demand and caches it.
"""

from __future__ import annotations

import time
from dataclasses import asdict

import numpy as np

from .. import settings
from ..core import colours
from ..repositories.corpus_repository import CorpusRepository, Fragment
from .models import Embedder, Generator
from .retrieval import RetrievalService


NARRATIVE_NOTE = (
    "Write the answer as natural, flowing prose in your own words. Do not "
    "refer to numbered sources, fragments, or excerpts, and do not say "
    "things like \"according to fragment 3\" — synthesize the material as "
    "if explaining it directly, while staying grounded in what was "
    "actually provided."
)


class RagService:
    def __init__(self, corpus_db=None, embedder_name=None):
        self.embedder = Embedder(embedder_name)
        self.repo = CorpusRepository(corpus_db or settings.CORPUS_DB,
                                     dims=self.embedder.dims)
        self.retrieval = RetrievalService(self.repo, self.embedder)
        self._generators: dict[str, Generator] = {}

    def generator(self, name: str | None = None) -> Generator:
        key = name or settings.DEFAULT_GENERATOR
        if key not in self._generators:
            self._generators[key] = Generator(key)
        return self._generators[key]

    def stats(self) -> dict:
        return {**self.repo.stats(), "embedder": self.embedder.key,
                "embed_model": self.embedder.model}

    # ── prompt ───────────────────────────────────────────────────────
    @staticmethod
    def format_context(frags: list[Fragment], regime: dict) -> str:
        narrative = regime.get("narrative_style")
        out = []
        for n, f in enumerate(frags, 1):
            numbering = "" if narrative else f"[{n}] "
            if regime.get("prompt_meta"):
                head = f"{numbering}{colours.tag_for(f.color, bool(f.is_bold))}".rstrip()
                if f.tree_path:
                    head += f" ({f.tree_path})"
                out.append(f"{head}\n{f.text}" if head else f.text)
            else:
                out.append(f"{numbering}{f.text}" if numbering else f.text)
        return "\n\n".join(out)

    def build_messages(self, query: str, frags: list[Fragment],
                       regime: dict) -> list[dict]:
        system = settings.SYSTEM_PROMPT
        if regime.get("explain_colors"):
            system += "\n\n" + colours.legend()
        if regime.get("narrative_style"):
            system += "\n\n" + NARRATIVE_NOTE
        user = (f"Fragments retrieved from the archive:\n\n"
                f"{self.format_context(frags, regime)}\n\n"
                f"Question: {query}")
        return [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    # Request-time knobs a user can override per-call without touching the
    # named regime presets — keeps the presets as the reproducible/citable
    # configs while still letting the bench UI expose live sliders.
    OVERRIDABLE_KEYS = {"top_k", "min_weight", "narrative_style"}

    def _fit_to_context(self, query: str, frags: list[Fragment], regime: dict,
                        gen: Generator) -> tuple[list[Fragment], list[dict], int]:
        """Trim frags until the built prompt fits gen.context_window.

        The bench UI's "fill context %" slider estimates size from the
        base top_k alone — it doesn't know a regime might add more
        fragments afterward (expand_note pulls extra same-note fragments
        on top of top_k). For Ollama that's forgiving since num_ctx is set
        per-request; for LM Studio the context is fixed at model-load
        time, so an underestimate is a hard 400, not a slow response.
        Trimming from the end drops expansion-sourced fragments first
        (they're appended after the primary retrieval hits), and only
        eats into real hits if it still doesn't fit.
        """
        reserve = settings.MAX_TOKENS + 300  # generation budget + message overhead
        budget = max(256, gen.context_window - reserve)
        frags = list(frags)
        while frags:
            messages = self.build_messages(query, frags, regime)
            est_tokens = sum(len(m["content"]) for m in messages) / 4
            if est_tokens <= budget:
                return frags, messages, int(est_tokens)
            frags.pop()
        return frags, self.build_messages(query, frags, regime), 0

    def answer_stream(self, query: str, regime_key: str, *,
                      generator: str | None = None, overrides: dict | None = None):
        """Same retrieval + context-fitting as answer(), but yields the
        generated answer as it streams instead of blocking for it.

        First yields a ("meta", dict) event with everything answer()
        returns except "answer" itself (fragments, metrics, labels) — the
        caller has enough to render the column immediately. Then yields
        ("delta", str) events as text arrives, then ("done", None).
        """
        if regime_key not in settings.REGIMES:
            raise KeyError(f"Unknown regime {regime_key!r}. "
                           f"Available: {', '.join(settings.REGIMES)}")
        regime = settings.REGIMES[regime_key]
        if overrides:
            regime = {**regime, **{k: v for k, v in overrides.items()
                                   if k in self.OVERRIDABLE_KEYS and v is not None}}

        t0 = time.perf_counter()
        frags = self.retrieval.retrieve(query, regime)
        retrieve_s = time.perf_counter() - t0

        if not frags:
            yield ("meta", {
                "regime": regime_key, "regime_label": regime["label"],
                "query": query, "generator": None, "generator_label": None,
                "fragments": [], "metrics": {
                    "n_fragments": 0, "n_expanded": 0, "n_trimmed_to_fit": 0,
                    "mean_weight": 0.0, "max_weight": 0.0, "pct_highlighted": 0.0,
                    "distinct_notes": 0, "context_chars": 0,
                    "retrieve_s": round(retrieve_s, 3), "generate_s": 0.0,
                }})
            yield ("delta", "Nothing in the corpus matched this query.")
            yield ("done", None)
            return

        gen = self.generator(generator)
        fitted, messages, _ = self._fit_to_context(query, frags, regime, gen)
        n_trimmed = len(frags) - len(fitted)
        frags = fitted
        weights = [f.weight for f in frags] or [0.0]

        yield ("meta", {
            "regime": regime_key, "regime_label": regime["label"], "query": query,
            "generator": gen.key, "generator_label": gen.label,
            "fragments": [asdict(f) for f in frags],
            "metrics": {
                "n_fragments": len(frags),
                "n_expanded": sum(1 for f in frags if f.source == "expanded"),
                "n_trimmed_to_fit": n_trimmed,
                "mean_weight": round(float(np.mean(weights)), 3),
                "max_weight": round(float(np.max(weights)), 3),
                "pct_highlighted": round(
                    100 * sum(1 for f in frags if f.weight >= 1) / max(len(frags), 1), 1),
                "distinct_notes": len({f.note_uid for f in frags}),
                "context_chars": sum(len(f.text) for f in frags),
                "retrieve_s": round(retrieve_s, 3), "generate_s": None,
            }})

        t1 = time.perf_counter()
        for delta in gen.complete_stream(messages):
            yield ("delta", delta)
        yield ("generate_s", round(time.perf_counter() - t1, 2))
        yield ("done", None)

    # ── main entry point ─────────────────────────────────────────────
    def answer(self, query: str, regime_key: str, *, generator: str | None = None,
               retrieval_only: bool = False, overrides: dict | None = None) -> dict:
        if regime_key not in settings.REGIMES:
            raise KeyError(f"Unknown regime {regime_key!r}. "
                           f"Available: {', '.join(settings.REGIMES)}")
        regime = settings.REGIMES[regime_key]
        if overrides:
            regime = {**regime, **{k: v for k, v in overrides.items()
                                   if k in self.OVERRIDABLE_KEYS and v is not None}}

        t0 = time.perf_counter()
        frags = self.retrieval.retrieve(query, regime)
        retrieve_s = time.perf_counter() - t0

        text, generate_s, gen_key, gen_label, n_trimmed = "", 0.0, None, None, 0
        if not frags:
            text = "Nothing in the corpus matched this query."
        elif not retrieval_only:
            gen = self.generator(generator)
            gen_key = gen.key
            gen_label = gen.label
            fitted, messages, _ = self._fit_to_context(query, frags, regime, gen)
            n_trimmed = len(frags) - len(fitted)
            frags = fitted
            t1 = time.perf_counter()
            text = gen.complete(messages)
            generate_s = time.perf_counter() - t1

        weights = [f.weight for f in frags] or [0.0]
        return {
            "regime": regime_key,
            "regime_label": regime["label"],
            "query": query,
            "answer": text,
            "generator": gen_key,
            "generator_label": gen_label,
            "fragments": [asdict(f) for f in frags],
            "metrics": {
                "n_fragments": len(frags),
                "n_expanded": sum(1 for f in frags if f.source == "expanded"),
                "n_trimmed_to_fit": n_trimmed,
                "mean_weight": round(float(np.mean(weights)), 3),
                "max_weight": round(float(np.max(weights)), 3),
                "pct_highlighted": round(
                    100 * sum(1 for f in frags if f.weight >= 1)
                    / max(len(frags), 1), 1),
                "distinct_notes": len({f.note_uid for f in frags}),
                "context_chars": sum(len(f.text) for f in frags),
                "retrieve_s": round(retrieve_s, 3),
                "generate_s": round(generate_s, 2),
            },
        }
