"""Data access for experiment runs and ratings."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    regime TEXT NOT NULL,
    question TEXT,
    answer TEXT,
    fragments TEXT,
    metrics TEXT,
    generator TEXT,
    embedder TEXT,
    corpus TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(question_id, regime, generator)
);
CREATE TABLE IF NOT EXISTS ratings (
    run_id INTEGER PRIMARY KEY,
    overall INTEGER,              -- primary metric: single holistic quality score
    correctness INTEGER,
    completeness INTEGER,
    grounding INTEGER,
    notes TEXT,
    blind INTEGER DEFAULT 1,      -- was the regime hidden when rated?
    rated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_runs_q ON runs(question_id);
"""


class ResultsRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.db_path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        try:
            self.con.execute("ALTER TABLE ratings ADD COLUMN overall INTEGER")
            self.con.commit()
        except sqlite3.OperationalError:
            pass  # already migrated — an eval.db from before `overall` existed

    # ── runs ─────────────────────────────────────────────────────────
    def save_run(self, result: dict, corpus: str, generator: str,
                 embedder: str) -> int:
        self.con.execute(
            "INSERT OR REPLACE INTO runs (question_id, regime, question, answer, "
            "fragments, metrics, generator, embedder, corpus) VALUES (?,?,?,?,?,?,?,?,?)",
            (result["question_id"], result["regime"], result["query"],
             result["answer"], json.dumps(result["fragments"]),
             json.dumps(result["metrics"]), generator, embedder, corpus))
        self.con.commit()
        row = self.con.execute(
            "SELECT id FROM runs WHERE question_id=? AND regime=? AND generator=?",
            (result["question_id"], result["regime"], generator)).fetchone()
        return int(row["id"])

    def has_run(self, question_id: str, regime: str, generator: str) -> bool:
        return self.con.execute(
            "SELECT 1 FROM runs WHERE question_id=? AND regime=? AND generator=?",
            (question_id, regime, generator)).fetchone() is not None

    def runs_for_question(self, question_id: str) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM runs WHERE question_id=? ORDER BY regime",
            (question_id,)).fetchall()

    def next_unrated_question(self) -> str | None:
        row = self.con.execute(
            "SELECT question_id FROM runs WHERE id NOT IN "
            "(SELECT run_id FROM ratings) GROUP BY question_id "
            "ORDER BY question_id LIMIT 1").fetchone()
        return row["question_id"] if row else None

    def question_ids(self) -> list[str]:
        return [r[0] for r in self.con.execute(
            "SELECT DISTINCT question_id FROM runs ORDER BY question_id")]

    def rated_run_ids(self) -> set[int]:
        return {r[0] for r in self.con.execute("SELECT run_id FROM ratings")}

    def unrated_count(self) -> int:
        return self.con.execute(
            "SELECT COUNT(*) FROM runs WHERE id NOT IN "
            "(SELECT run_id FROM ratings)").fetchone()[0]

    # ── ratings ──────────────────────────────────────────────────────
    def save_rating(self, run_id: int, overall, correctness, completeness, grounding,
                    notes: str = "", blind: bool = True):
        # correctness/completeness/grounding are optional per-save (the UI
        # only sends them if the breakdown panel was opened) — if omitted,
        # keep whatever was recorded last time instead of blanking it out.
        if correctness is None and completeness is None and grounding is None:
            existing = self.rating_for(run_id)
            if existing:
                correctness, completeness, grounding = (
                    existing["correctness"], existing["completeness"], existing["grounding"])
        self.con.execute(
            "INSERT OR REPLACE INTO ratings (run_id, overall, correctness, completeness, "
            "grounding, notes, blind) VALUES (?,?,?,?,?,?,?)",
            (run_id, overall, correctness, completeness, grounding, notes, int(blind)))
        self.con.commit()

    def rating_for(self, run_id: int) -> sqlite3.Row | None:
        return self.con.execute(
            "SELECT * FROM ratings WHERE run_id=?", (run_id,)).fetchone()

    # ── reporting ────────────────────────────────────────────────────
    def all_rated(self) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT r.regime, r.question_id, r.generator, r.metrics, "
            "t.overall, t.correctness, t.completeness, t.grounding, t.blind "
            "FROM runs r LEFT JOIN ratings t ON t.run_id = r.id").fetchall()

    def summary(self) -> dict:
        by_regime = [dict(r) for r in self.con.execute(
            "SELECT r.regime, COUNT(r.id) runs, COUNT(t.run_id) rated, "
            "AVG(t.overall) overall, "
            "AVG(t.correctness) correctness, AVG(t.completeness) completeness, "
            "AVG(t.grounding) grounding, "
            "AVG(CASE WHEN t.blind=1 THEN t.overall END) overall_blind "
            "FROM runs r LEFT JOIN ratings t ON t.run_id=r.id "
            "GROUP BY r.regime ORDER BY r.regime")]
        totals = self.con.execute(
            "SELECT (SELECT COUNT(*) FROM runs), (SELECT COUNT(*) FROM ratings)"
        ).fetchone()
        return {"by_regime": by_regime, "total_runs": totals[0],
                "total_rated": totals[1]}
