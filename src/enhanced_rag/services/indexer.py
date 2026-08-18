"""
Builds what retrieval needs on top of a corpus DB:

  ku_fts            FTS5 keyword index, for the lexical baseline
  embedding_plain   each fragment embedded as bare text
  embedding         each fragment embedded with its breadcrumb prefix

Two embedding columns over the *same* fragment set is the point: it makes
"does structural metadata help in embedding space?" a controlled A/B, and
stops the dense baseline being quietly handicapped by a smaller pool.

The FTS index lives here in the corpus DB. The .rnt file's own FTS3 tables
are never written to — doing so destroys its search segments.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import numpy as np

from ..repositories.corpus_repository import RETRIEVABLE
from .models import Embedder


def ensure_columns(con: sqlite3.Connection, log=print):
    have = {r[1] for r in con.execute("PRAGMA table_info(knowledge_units)")}
    for col in ("embedding", "embedding_plain"):
        if col not in have:
            con.execute(f"ALTER TABLE knowledge_units ADD COLUMN {col} BLOB")
            log(f"  added column {col}")
    con.commit()


def build_fts(con: sqlite3.Connection, log=print):
    log("Building keyword index...")
    con.execute("DROP TABLE IF EXISTS ku_fts")
    con.execute("CREATE VIRTUAL TABLE ku_fts USING fts5("
                "text, content='knowledge_units', content_rowid='id')")
    con.execute(f"INSERT INTO ku_fts(rowid, text) SELECT id, text "
                f"FROM knowledge_units WHERE {RETRIEVABLE}")
    con.commit()
    # NOTE: COUNT(*) FROM ku_fts without a MATCH clause reads the external
    # content table directly (all of knowledge_units), not just what was
    # inserted — count the source selection instead to report correctly.
    n = con.execute(
        f"SELECT COUNT(*) FROM knowledge_units WHERE {RETRIEVABLE}").fetchone()[0]
    log(f"  indexed {n} fragments")


def embed_column(con, embedder: Embedder, column: str, contextual: bool,
                 batch: int = 32, force: bool = False, sample: int = None, log=print):
    where = RETRIEVABLE if force else f"{RETRIEVABLE} AND {column} IS NULL"
    # Highest-weight fragments first, so a small --sample still covers the
    # content most likely to matter for a quick retrieval-quality check.
    order_limit = " ORDER BY effective_weight DESC"
    if sample:
        order_limit += f" LIMIT {int(sample)}"
    rows = con.execute(
        f"SELECT id, text, tree_path, caption FROM knowledge_units "
        f"WHERE {where}{order_limit}"
    ).fetchall()
    if not rows:
        log(f"  {column}: already complete")
        return

    log(f"  {column}: {len(rows)} fragments")
    start, done = time.time(), 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        texts = ([f"[{(r[2] or r[3] or '').strip()}] {r[1]}" for r in chunk]
                 if contextual else [r[1] for r in chunk])
        vecs = embedder.embed(texts, batch=batch)
        con.executemany(f"UPDATE knowledge_units SET {column}=? WHERE id=?",
                        [(v.astype(np.float32).tobytes(), r[0])
                         for v, r in zip(vecs, chunk)])
        con.commit()          # committing per batch keeps this resumable
        done += len(chunk)
        rate = done / max(time.time() - start, 1e-6)
        log(f"\r    {done}/{len(rows)}  {rate:.0f}/s  "
            f"eta {(len(rows)-done)/max(rate,1e-6)/60:.1f}m", end="", flush=True)
    log("")


def summarise(con, log=print):
    one = lambda q: con.execute(q).fetchone()[0]
    total = one(f"SELECT COUNT(*) FROM knowledge_units WHERE {RETRIEVABLE}")
    log("\nCorpus ready:")
    log(f"  retrievable fragments  {total}")
    log(f"  highlighted            "
        f"{one(f'SELECT COUNT(*) FROM knowledge_units WHERE {RETRIEVABLE} AND effective_weight >= 1')}")
    log(f"  plain embeddings       "
        f"{one(f'SELECT COUNT(*) FROM knowledge_units WHERE {RETRIEVABLE} AND embedding_plain IS NOT NULL')}")
    log(f"  context embeddings     "
        f"{one(f'SELECT COUNT(*) FROM knowledge_units WHERE {RETRIEVABLE} AND embedding IS NOT NULL')}")
    log("\n  by colour:")
    for color, n, w in con.execute(
            f"SELECT COALESCE(color,'unmarked'), COUNT(*), AVG(effective_weight) "
            f"FROM knowledge_units WHERE {RETRIEVABLE} GROUP BY color "
            f"ORDER BY COUNT(*) DESC"):
        log(f"    {color:<10} {n:>7}   mean weight {w:.2f}")


def build(corpus_db, embedder_name=None, *, batch=32, force=False,
          fts_only=False, sample=None, log=print):
    db = Path(corpus_db)
    if not db.exists():
        raise FileNotFoundError(
            f"Corpus not found: {db}\nBuild it with:  python scripts/build_corpus.py")
    con = sqlite3.connect(db)
    ensure_columns(con, log)
    build_fts(con, log)
    if not fts_only:
        embedder = Embedder(embedder_name)
        embedder.check()
        log(f"Embedding with {embedder.model} ({embedder.dims}d)"
            f"{f', sample={sample}' if sample else ''}...")
        embed_column(con, embedder, "embedding_plain", False, batch, force, sample, log)
        embed_column(con, embedder, "embedding", True, batch, force, sample, log)
    summarise(con, log)
    con.close()
