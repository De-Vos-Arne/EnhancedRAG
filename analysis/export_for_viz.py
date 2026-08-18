"""
Export corpus.db to JSON for exploratory visualization (treemap + chunk stats).

Reads from the shadow-DB tables (parsed_notes, tree_hierarchy, page_stats) that
build_corpus.py's first stage produces, plus knowledge_units for chunk-level
stats. Scoped to page_id=28 (Belief-System) by default, matching
settings.SCOPE_PAGE_IDS — the rest of the archive is out of scope / private.

Sizing uses chars_total (parsed text length), not packed_size (raw RTF bytes,
which includes embedded images) — so image-heavy notes don't distort a treemap
the way they would if sized by file/blob size.

Usage:
    python analysis/export_for_viz.py [--corpus path] [--page 28] [--out dir]
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401
from enhanced_rag import settings


def build_tree(con, page_id):
    rows = con.execute("""
        SELECT th.treenode_id, th.note_uid, th.parent_treenode_id, th.display_index,
               th.depth, th.child_count, th.is_separator, th.is_marker,
               th.subtree_note_count, th.subtree_chars_total, th.subtree_chars_highlighted,
               th.subtree_salience_score,
               th.subtree_chars_pink, th.subtree_chars_blue, th.subtree_chars_green,
               th.subtree_chars_yellow, th.subtree_chars_orange, th.subtree_chars_purple,
               pn.caption, pn.note_bg_semantic, pn.chars_total, pn.chars_highlighted,
               pn.highlight_ratio, pn.salience_score,
               pn.chars_pink, pn.chars_blue, pn.chars_green, pn.chars_yellow,
               pn.chars_orange, pn.chars_purple
        FROM tree_hierarchy th
        LEFT JOIN parsed_notes pn ON th.note_uid = pn.uid
        WHERE th.page_id = ?
        ORDER BY th.depth, th.display_index
    """, (page_id,)).fetchall()

    by_id = {}
    for r in rows:
        colors = {c: (r[f"chars_{n}"] or 0) for c, n in
                  (("p", "pink"), ("b", "blue"), ("g", "green"),
                   ("y", "yellow"), ("o", "orange"), ("u", "purple"))}
        dominant = max(colors, key=colors.get) if any(colors.values()) else None
        by_id[r["treenode_id"]] = {
            "id": r["treenode_id"], "uid": r["note_uid"], "parent": r["parent_treenode_id"],
            "caption": r["caption"] or "", "depth": r["depth"],
            "is_separator": bool(r["is_separator"]), "is_marker": bool(r["is_marker"]),
            "chars": r["chars_total"] or 0, "chars_hl": r["chars_highlighted"] or 0,
            "hl_ratio": round(r["highlight_ratio"] or 0, 4),
            "salience": round(r["salience_score"] or 0, 2),
            "bg": r["note_bg_semantic"], "dominant_color": dominant,
            "st_chars": r["subtree_chars_total"] or 0,
            "st_salience": round(r["subtree_salience_score"] or 0, 2),
            "children": [],
        }

    roots = []
    for node in by_id.values():
        p = node["parent"]
        (by_id[p]["children"].append(node) if p in by_id else roots.append(node))
    for node in by_id.values():
        node["children"].sort(key=lambda c: c["id"])
    roots.sort(key=lambda r: r["id"])
    return roots


def chunk_stats(con, page_id):
    """Knowledge-unit-level stats: size distribution, weight/color breakdown."""
    where = ("is_trivial=0 AND is_separator=0 AND is_duplicate=0 "
             "AND length(trim(text)) > 12 AND page_id = ?")

    lens = [len(t[0]) for t in con.execute(
        f"SELECT text FROM knowledge_units WHERE {where}", (page_id,))]
    lens.sort()
    n = len(lens)

    def pct(p):
        return lens[min(n - 1, int(n * p))] if n else 0

    buckets = [(0, 20), (20, 40), (40, 80), (80, 120), (120, 200),
               (200, 400), (400, 800), (800, 999999)]
    hist = []
    for lo, hi in buckets:
        cnt = sum(1 for l in lens if lo <= l < hi)
        hist.append({"range": f"{lo}-{hi if hi < 999999 else '+'}", "count": cnt})

    by_color = []
    for row in con.execute(f"""
        SELECT COALESCE(color,'none') AS c, COUNT(*) n, AVG(effective_weight) w,
               AVG(length(text)) al
        FROM knowledge_units WHERE {where} GROUP BY c ORDER BY n DESC
    """, (page_id,)):
        by_color.append({"color": row["c"], "count": row["n"],
                         "avg_weight": round(row["w"] or 0, 2),
                         "avg_len": round(row["al"] or 0, 1)})

    return {
        "total_fragments": n,
        "min": lens[0] if n else 0, "max": lens[-1] if n else 0,
        "p25": pct(0.25), "p50": pct(0.50), "p75": pct(0.75), "p90": pct(0.90),
        "mean": round(sum(lens) / n, 1) if n else 0,
        "histogram": hist,
        "by_color": by_color,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(settings.CORPUS_DB))
    ap.add_argument("--page", type=int, default=28)
    ap.add_argument("--out", default=str(Path(__file__).parent))
    a = ap.parse_args()

    con = sqlite3.connect(a.corpus)
    con.row_factory = sqlite3.Row

    page_row = con.execute("SELECT * FROM page_stats WHERE page_id=?", (a.page,)).fetchone()
    page_meta = dict(page_row) if page_row else {}
    if "color_distribution" in page_meta and page_meta["color_distribution"]:
        page_meta["color_distribution"] = json.loads(page_meta["color_distribution"])

    output = {
        "metadata": {"source": a.corpus, "page_id": a.page},
        "page": page_meta,
        "tree": build_tree(con, a.page),
        "chunks": chunk_stats(con, a.page),
    }

    out_path = Path(a.out) / f"viz_data_page{a.page}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Exported to {out_path} ({out_path.stat().st_size/1024/1024:.1f} MB)")
    con.close()


if __name__ == "__main__":
    main()
