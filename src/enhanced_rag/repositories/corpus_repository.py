"""
Data access for the corpus (the shadow DB).

Everything that touches SQLite for knowledge units lives here. Services
above this layer never write SQL, so swapping SQLite for something else
later means changing this file only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import settings

# Fragments too short or marked as noise are never retrievable.
# Also excludes anything outside settings.SCOPE_PAGE_IDS — only
# Belief-System is in scope for the thesis, the rest of the archive
# is private.
_scope_clause = (
    f"AND page_id IN ({','.join(str(p) for p in settings.SCOPE_PAGE_IDS)}) "
    if settings.SCOPE_PAGE_IDS else ""
)
RETRIEVABLE = ("is_trivial = 0 AND is_separator = 0 AND is_duplicate = 0 "
               "AND length(trim(text)) > 12 " + _scope_clause)


@dataclass
class Fragment:
    """One retrievable line, with its metadata."""
    unit_id: int
    text: str
    color: str | None
    weight: float
    is_bold: int
    tree_path: str
    caption: str
    note_uid: int
    section_color: str | None
    score: float = 0.0
    cosine: float = 0.0
    source: str = "retrieved"      # 'retrieved' | 'expanded'


class CorpusRepository:
    """Reads the corpus and holds its vectors in memory."""

    def __init__(self, db_path: str | Path, dims: int):
        self.db_path = str(db_path)
        self.dims = dims
        if not Path(self.db_path).exists():
            raise FileNotFoundError(
                f"Corpus not found: {self.db_path}\n"
                f"Build it with:  python scripts/build_corpus.py")
        self.con = sqlite3.connect(self.db_path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self._load()

    # ── loading ──────────────────────────────────────────────────────
    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}

    def _load(self):
        cols = self._columns("knowledge_units")
        self.has_plain = "embedding_plain" in cols
        select = ("id, note_uid, text, color, effective_weight, is_bold, "
                  "tree_path, caption, section_color, embedding"
                  + (", embedding_plain" if self.has_plain else ""))
        rows = self.con.execute(
            f"SELECT {select} FROM knowledge_units WHERE {RETRIEVABLE}").fetchall()
        if not rows:
            raise RuntimeError(
                f"{self.db_path} has no retrievable fragments. "
                "Did build_knowledge_units run?")

        self._rows = rows
        self.row_of_id = {int(r["id"]): i for i, r in enumerate(rows)}
        self.weights = np.array([r["effective_weight"] or 0.0 for r in rows],
                                dtype=np.float32)

        from ..core import colours
        self.section_weights = np.array(
            [colours.WEIGHTS.get(r["section_color"], 0.0) for r in rows],
            dtype=np.float32)

        self.vectors: dict[str, np.ndarray | None] = {}
        self.present: dict[str, np.ndarray] = {}
        for field, col in (("context", "embedding"), ("plain", "embedding_plain")):
            if col not in cols:
                self.vectors[field] = None
                self.present[field] = np.zeros(len(rows), dtype=bool)
                continue
            mat = np.zeros((len(rows), self.dims), dtype=np.float32)
            ok = np.zeros(len(rows), dtype=bool)
            wrong_dims = 0
            for i, r in enumerate(rows):
                blob = r[col]
                if not blob:
                    continue
                v = np.frombuffer(blob, dtype=np.float32)
                if v.size == self.dims:
                    mat[i] = v
                    ok[i] = True
                else:
                    wrong_dims += 1
            if wrong_dims:
                raise RuntimeError(
                    f"{wrong_dims} vectors in '{col}' have the wrong size. The "
                    f"corpus was indexed with a different embedding model than "
                    f"the one configured now ({self.dims} dims). Re-run "
                    f"scripts/build_index.py --force, or switch RAG_EMBEDDER back.")
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self.vectors[field] = mat / norms
            self.present[field] = ok

    # ── queries ──────────────────────────────────────────────────────
    def fragment(self, row_index: int, **overrides) -> Fragment:
        r = self._rows[row_index]
        return Fragment(
            unit_id=int(r["id"]), text=r["text"], color=r["color"],
            weight=float(r["effective_weight"] or 0.0),
            is_bold=int(r["is_bold"] or 0), tree_path=r["tree_path"] or "",
            caption=r["caption"] or "", note_uid=int(r["note_uid"]),
            section_color=r["section_color"], **overrides)

    def search_fts(self, terms: list[str], limit: int) -> list[tuple[int, float]]:
        """Returns (row_index, bm25_rank). Lower rank is better."""
        match = " OR ".join(f'"{t}"' for t in terms)
        try:
            rows = self.con.execute(
                "SELECT rowid, bm25(ku_fts) AS rank FROM ku_fts "
                "WHERE ku_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit)).fetchall()
        except sqlite3.OperationalError as e:
            raise RuntimeError(
                "The keyword index is missing. Build it with:  "
                "python scripts/build_index.py") from e
        out = []
        for r in rows:
            i = self.row_of_id.get(int(r["rowid"]))
            if i is not None:
                out.append((i, float(r["rank"])))
        return out

    def note_siblings(self, note_uid: int, exclude_id: int, limit: int
                      ) -> list[int]:
        """Row indices of the highest-weight other fragments in the same note."""
        rows = self.con.execute(
            f"SELECT id FROM knowledge_units WHERE note_uid=? AND id!=? "
            f"AND {RETRIEVABLE} ORDER BY effective_weight DESC, id LIMIT ?",
            (note_uid, exclude_id, limit)).fetchall()
        return [i for i in (self.row_of_id.get(int(r["id"])) for r in rows)
                if i is not None]

    def stats(self) -> dict:
        notes = len({r["note_uid"] for r in self._rows})
        by_colour = {(r["color"] or "unmarked"): 0 for r in self._rows}
        for r in self._rows:
            by_colour[r["color"] or "unmarked"] += 1
        avg_chars = (sum(len(r["text"]) for r in self._rows) / len(self._rows)
                    if self._rows else 0)
        return {
            "corpus": Path(self.db_path).name,
            "fragments": len(self._rows),
            "highlighted": int((self.weights >= 1).sum()),
            "notes": notes,
            "embedded": {k: int(v.sum()) for k, v in self.present.items()},
            "by_colour": by_colour,
            "avg_chars_per_fragment": round(avg_chars, 1),
        }
