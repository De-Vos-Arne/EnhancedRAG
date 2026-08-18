#!/usr/bin/env python3
"""
Produce a redacted, shareable subset of the .rnt archive: everything
outside settings.SCOPE_PAGE_IDS removed, leaving a standalone archive that
still opens correctly in RightNote and still validates against
rnt_crud.py's own integrity checks (no orphan notes/contents, no broken
tree links, FTS index rebuilt).

Never touches the original file — copies it first, then deletes from the
copy only, at the raw SQLite level (bulk DELETE, not rnt_crud's one-note
API, since that would be far too slow across a whole archive).

    python scripts/make_subset_archive.py
    python scripts/make_subset_archive.py --out data/PersonalArchiveSubset.rnt --pages 28

    # After manually deleting notes in RightNote itself (or any tool other
    # than this script), run this on the resulting file before sharing it.
    # FTS3 does not physically purge deleted content from its index on a
    # normal delete — only rebuild+VACUUM does. Skipping this step after a
    # manual edit leaves deleted text recoverable from the file's own
    # full-text index even though the notes/contents rows are gone; this
    # is exactly what happened once already in this project's own subset
    # file (573KB of leftover index data for what should have been a
    # ~13-note, ~100KB file) and is the reason this mode exists.
    python scripts/make_subset_archive.py --rebuild-only --out data/PersonalArchiveSubset.rnt
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
from enhanced_rag import settings

ap = argparse.ArgumentParser()
ap.add_argument("--archive", default=str(settings.ARCHIVE))
ap.add_argument("--out", default=None,
                help="default: <archive-name>Subset.rnt next to the original")
ap.add_argument("--pages", nargs="*", type=int, default=list(settings.SCOPE_PAGE_IDS),
                help="page_ids to KEEP; default settings.SCOPE_PAGE_IDS")
ap.add_argument("--treenode-ids", nargs="*", type=int, default=None,
                help="optional further restriction: keep only these treenode_ids "
                     "and their descendants (within --pages), instead of the "
                     "whole page. Use the explorer to find treenode_ids — hover "
                     "a tree node, or check its edit panel.")
ap.add_argument("--rebuild-only", action="store_true",
                help="skip the page/treenode filtering entirely — just rebuild "
                     "the FTS3 index and VACUUM an existing file in place "
                     "(--out). Use this after editing that file directly in "
                     "RightNote, before sharing it.")
a = ap.parse_args()

if a.rebuild_only:
    if not a.out or not Path(a.out).exists():
        print("--rebuild-only needs --out pointing at an existing file.")
        raise SystemExit(1)
    out = Path(a.out)
    con = sqlite3.connect(str(out))
    try:
        before = out.stat().st_size / 1e6
        con.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
        con.commit()
        print("FTS3 index rebuilt — purging any stale segments from deleted content.")
        con.execute("VACUUM")
        con.commit()
    finally:
        con.close()
    after = out.stat().st_size / 1e6
    print(f"{out}: {before:.1f} MB -> {after:.1f} MB")
    raise SystemExit(0)

src = Path(a.archive)
if not src.exists():
    print(f"Archive not found: {src}")
    raise SystemExit(1)
if not a.pages:
    print("No pages to keep — set --pages or settings.SCOPE_PAGE_IDS.")
    raise SystemExit(1)

out = Path(a.out) if a.out else src.with_name(src.stem + "Subset" + src.suffix)
print(f"Copying {src} ({src.stat().st_size / 1e6:.1f} MB) -> {out}")
shutil.copy2(src, out)

con = sqlite3.connect(str(out))
placeholders = ",".join("?" * len(a.pages))
try:
    before = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

    con.execute(f"DELETE FROM treenodes WHERE page_id NOT IN ({placeholders})", a.pages)

    if a.treenode_ids:
        # Recursive descendant walk, then invert: keep only treenodes inside
        # one of the given subtrees, drop everything else on the page(s).
        tn_placeholders = ",".join("?" * len(a.treenode_ids))
        keep = con.execute(f"""
            WITH RECURSIVE subtree(id) AS (
                SELECT id FROM treenodes WHERE id IN ({tn_placeholders})
                UNION ALL
                SELECT tn.id FROM treenodes tn JOIN subtree s ON tn.parent_id = s.id
            )
            SELECT id FROM subtree
        """, a.treenode_ids).fetchall()
        keep_ids = [r[0] for r in keep]
        if not keep_ids:
            print(f"No treenodes found for --treenode-ids {a.treenode_ids} — nothing kept.")
            raise SystemExit(1)
        keep_placeholders = ",".join("?" * len(keep_ids))
        con.execute(f"DELETE FROM treenodes WHERE id NOT IN ({keep_placeholders})", keep_ids)
        print(f"Restricted to {len(keep_ids)} treenode(s) under {a.treenode_ids}")

    con.execute(f"""DELETE FROM notes WHERE uid NOT IN
                    (SELECT note_uid FROM treenodes WHERE note_uid IS NOT NULL)""")
    con.execute("DELETE FROM contents WHERE id NOT IN (SELECT uid FROM notes)")
    try:
        con.execute(f"DELETE FROM pages WHERE id NOT IN ({placeholders})", a.pages)
    except sqlite3.OperationalError:
        pass  # no separate pages table in this archive version

    # notes_fts_content is the shadow table rnt_crud writes through; drop
    # rows for anything just removed, then rebuild the real FTS3 index.
    try:
        con.execute("DELETE FROM notes_fts_content WHERE docid NOT IN (SELECT uid FROM notes)")
        con.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
    except sqlite3.OperationalError as e:
        print(f"  (FTS rebuild skipped: {e})")

    after = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    con.commit()
    print(f"Notes: {before} -> {after} (kept page_id in {a.pages})")
    print("Reclaiming space (VACUUM) — this is the slow part on a large archive...")
    con.execute("VACUUM")
finally:
    con.close()

print(f"\nWrote {out} ({out.stat().st_size / 1e6:.1f} MB).")
print("Run scripts/doctor.py against it, or open it in RightNote, to confirm integrity "
      "before sharing.")
