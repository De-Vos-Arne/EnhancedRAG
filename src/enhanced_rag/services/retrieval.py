"""
Retrieval.

One retriever driven entirely by a regime dict, so the baseline and every
enhanced variant run through identical code and differences are
attributable to the regime rather than to two separate implementations.
"""

from __future__ import annotations

import re

import numpy as np

from ..core import colours
from ..repositories.corpus_repository import CorpusRepository, Fragment
from .models import Embedder


class RetrievalService:
    def __init__(self, repo: CorpusRepository, embedder: Embedder):
        self.repo = repo
        self.embedder = embedder

    def retrieve(self, query: str, regime: dict) -> list[Fragment]:
        kind = regime["retriever"]
        if kind == "fts":
            frags = self._lexical(query, regime)
        elif kind == "vector":
            frags = self._dense(query, regime)
        elif kind == "none":
            frags = self._top_weighted(regime)
        elif kind == "pooled":
            frags = self._pooled(query, regime)
        else:
            raise ValueError(f"Unknown retriever {kind!r} in regime config.")

        if regime.get("expand_note"):
            frags = self._expand(frags, regime["expand_note"])
        return frags

    # ── strategies ───────────────────────────────────────────────────
    def _mask(self, regime) -> np.ndarray:
        floor = regime.get("min_weight")
        if floor is None:
            return np.ones(len(self.repo.weights), dtype=bool)
        return self.repo.weights >= floor

    def _dense(self, query: str, regime: dict) -> list[Fragment]:
        field = regime.get("embed_field") or "context"
        mat = self.repo.vectors.get(field)
        if mat is None:
            raise RuntimeError(
                f"This corpus has no '{field}' embeddings. Build them with:  "
                f"python scripts/build_index.py")

        qv = self.embedder.embed_one(query)
        cos = mat @ qv
        mask = self._mask(regime) & self.repo.present[field]

        score = cos.copy()
        alpha = regime.get("weight_alpha", 0.0)
        if alpha:
            score *= 1 + alpha * np.clip(
                self.repo.weights, 0, colours.MAX_WEIGHT) / colours.MAX_WEIGHT
        bonus = regime.get("section_bonus", 0.0)
        if bonus:
            score *= 1 + bonus * np.clip(
                self.repo.section_weights, 0, colours.MAX_WEIGHT) / colours.MAX_WEIGHT
        score = np.where(mask, score, -1e9)

        k = min(regime["top_k"], int(mask.sum()))
        if k <= 0:
            return []
        top = np.argpartition(-score, k - 1)[:k]
        top = top[np.argsort(-score[top])]
        return [self.repo.fragment(int(i), score=float(score[i]),
                                   cosine=float(cos[i])) for i in top]

    def _lexical(self, query: str, regime: dict) -> list[Fragment]:
        terms = re.findall(r"[A-Za-z0-9']{3,}", query)
        if not terms:
            return []
        hits = self.repo.search_fts(terms, regime["top_k"] * 4)
        mask = self._mask(regime)
        out = []
        for row_index, rank in hits:
            if not mask[row_index]:
                continue
            out.append(self.repo.fragment(row_index, score=-rank))
            if len(out) >= regime["top_k"]:
                break
        return out

    def _top_weighted(self, regime: dict) -> list[Fragment]:
        """No query-time retrieval — the long-context control."""
        idx = np.flatnonzero(self._mask(regime))
        order = idx[np.argsort(-self.repo.weights[idx])][:regime["top_k"]]
        return [self.repo.fragment(int(i), score=float(self.repo.weights[i]))
                for i in order]

    def _pooled(self, query: str, regime: dict) -> list[Fragment]:
        """Keyword hits and vector hits pooled and deduped before note
        expansion — the automated version of the manual "curate for a
        question" export tool (keyword + vector, then read the whole note
        around each hit), run per-question instead of by hand. Splits
        top_k evenly between the two strategies rather than trying to
        merge their scores, which aren't on comparable scales (FTS rank
        vs cosine similarity)."""
        half = max(1, regime["top_k"] // 2)
        lex = self._lexical(query, {**regime, "top_k": half})
        vec = self._dense(query, {**regime, "top_k": regime["top_k"] - half})
        seen, out = set(), []
        for f in lex + vec:
            if f.unit_id in seen:
                continue
            seen.add(f.unit_id)
            out.append(f)
        return out

    def _expand(self, frags: list[Fragment], n: int) -> list[Fragment]:
        """Pull high-weight neighbours from each hit's parent note.

        Line-level units are precise but sometimes too small to answer
        from. This restores local context without falling back to whole
        notes, which would reintroduce the noise the line-level index
        exists to avoid.
        """
        seen = {f.unit_id for f in frags}
        extra = []
        for f in list(frags):
            for row_index in self.repo.note_siblings(f.note_uid, f.unit_id, n):
                frag = self.repo.fragment(row_index, score=f.score * 0.5,
                                          source="expanded")
                if frag.unit_id in seen:
                    continue
                seen.add(frag.unit_id)
                extra.append(frag)
        return frags + extra
