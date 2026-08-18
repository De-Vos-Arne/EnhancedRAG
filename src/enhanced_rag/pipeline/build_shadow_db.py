"""
Shadow Database Builder for RightNote Archives.

Reads an .rnt (SQLite) database and creates a parallel 'shadow' database
containing parsed text, internal format, color statistics, and hierarchy
metadata — without touching the original file.

Usage:
    python build_shadow_db.py <path_to_rnt_file> [output_path]

The shadow DB will be created at output_path (default: same dir, name_shadow.db)
"""

import sqlite3
import json
import sys
import os
import re
import time
import zlib

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
from pathlib import Path
from datetime import datetime, timedelta

# Add core/ to path for imports — rtf_parser now lives in src/enhanced_rag/core/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from rtf_parser import RTFParser, bg_color_to_semantic, HEX_COLOR_MAP, COLOR_NAMES


# ── Delphi date conversion ─────────────────────────────────────────────
DELPHI_EPOCH = datetime(1899, 12, 30)

def delphi_to_datetime(delphi_float):
    """Convert Delphi TDateTime float to Python datetime."""
    if not delphi_float or delphi_float <= 0:
        return None
    try:
        return DELPHI_EPOCH + timedelta(days=float(delphi_float))
    except (ValueError, OverflowError):
        return None

def delphi_to_iso(delphi_float):
    """Convert Delphi TDateTime to ISO date string."""
    dt = delphi_to_datetime(delphi_float)
    return dt.isoformat() if dt else None


def sanitize_text(text):
    """Remove surrogate characters that can't be encoded to UTF-8 for SQLite."""
    if text is None:
        return None
    # Remove lone surrogates (U+D800 to U+DFFF)
    return text.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')

SHADOW_SCHEMA = """
-- Parsed notes with extracted text and formatting metadata
CREATE TABLE IF NOT EXISTS parsed_notes (
    uid INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL,
    caption TEXT,
    plain_text TEXT,
    internal_format TEXT,
    
    -- Color statistics (character counts)
    chars_total INTEGER DEFAULT 0,
    chars_highlighted INTEGER DEFAULT 0,
    highlight_ratio REAL DEFAULT 0.0,
    chars_pink INTEGER DEFAULT 0,
    chars_blue INTEGER DEFAULT 0,
    chars_green INTEGER DEFAULT 0,
    chars_yellow INTEGER DEFAULT 0,
    chars_orange INTEGER DEFAULT 0,
    chars_purple INTEGER DEFAULT 0,
    chars_dark_green INTEGER DEFAULT 0,
    
    -- Note-level color (bg_color from tree)
    note_bg_color TEXT,           -- raw hex
    note_bg_semantic TEXT,        -- semantic code (p/b/g/y/o/u)
    
    -- Dates (converted)
    date_created TEXT,            -- ISO format
    date_modified TEXT,
    date_created_delphi REAL,     -- original float for sorting
    
    -- Content metrics
    word_count INTEGER DEFAULT 0,
    line_count INTEGER DEFAULT 0,
    has_breaks INTEGER DEFAULT 0,
    break_count INTEGER DEFAULT 0,
    
    -- Compression info
    packed_size INTEGER,
    
    -- Salience score (computed: weighted combination of high-value colors)
    salience_score REAL DEFAULT 0.0,
    
    -- Parse status
    parse_error TEXT
);

-- Materialized hierarchy with paths
-- NOTE: treenode_id is NOT globally unique (only unique per page_id)
CREATE TABLE IF NOT EXISTS tree_hierarchy (
    treenode_id INTEGER,
    note_uid INTEGER,
    page_id INTEGER,
    parent_treenode_id INTEGER,
    display_index INTEGER,
    depth INTEGER DEFAULT 0,
    child_count INTEGER DEFAULT 0,
    is_folder INTEGER DEFAULT 0,
    is_separator INTEGER DEFAULT 0,    -- =====, separator nodes
    is_marker INTEGER DEFAULT 0,       -- treenode-only, minimal content
    
    -- Materialized path (e.g., "28/1039/1040/1041")
    path TEXT,
    -- Path using captions for readability
    caption_path TEXT,
    
    -- Subtree aggregates (filled in second pass)
    subtree_note_count INTEGER DEFAULT 0,
    subtree_chars_total INTEGER DEFAULT 0,
    subtree_chars_highlighted INTEGER DEFAULT 0,
    subtree_highlight_ratio REAL DEFAULT 0.0,
    subtree_salience_score REAL DEFAULT 0.0,

    -- Color aggregates for subtree
    subtree_chars_pink INTEGER DEFAULT 0,
    subtree_chars_blue INTEGER DEFAULT 0,
    subtree_chars_green INTEGER DEFAULT 0,
    subtree_chars_yellow INTEGER DEFAULT 0,
    subtree_chars_orange INTEGER DEFAULT 0,
    subtree_chars_purple INTEGER DEFAULT 0,

    -- Unique per (page_id, treenode_id)
    UNIQUE(page_id, treenode_id)
);

-- Per-page summary statistics
CREATE TABLE IF NOT EXISTS page_stats (
    page_id INTEGER PRIMARY KEY,
    caption TEXT,
    note_count INTEGER,
    total_chars INTEGER,
    highlighted_chars INTEGER,
    highlight_ratio REAL,
    avg_salience REAL,
    max_depth INTEGER,
    color_distribution TEXT   -- JSON {color: char_count}
);

-- Build metadata
CREATE TABLE IF NOT EXISTS build_info (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_parsed_page ON parsed_notes(page_id);
CREATE INDEX IF NOT EXISTS idx_parsed_salience ON parsed_notes(salience_score DESC);
CREATE INDEX IF NOT EXISTS idx_tree_page ON tree_hierarchy(page_id);
CREATE INDEX IF NOT EXISTS idx_tree_parent ON tree_hierarchy(parent_treenode_id);
CREATE INDEX IF NOT EXISTS idx_tree_noteuid ON tree_hierarchy(note_uid);
CREATE INDEX IF NOT EXISTS idx_tree_depth ON tree_hierarchy(depth);
"""


def compute_salience(color_stats: dict, total_chars: int) -> float:
    """
    Compute a salience score based on color distribution.
    
    Higher-value colors get more weight:
      pink (p) = 5, purple (u) = 4, blue (b) = 3, green (g) = 2, 
      yellow (y) = 1, orange (o) = 0.5, dark-green (g2) = 2
    
    Returns a 0-100 score.
    """
    if total_chars == 0:
        return 0.0
    
    weights = {
        'p': 5.0,   # pink — exceptional
        'u': 4.0,   # purple — standout
        'b': 3.0,   # blue — excellent
        'g': 2.0,   # green — good
        'g2': 2.0,  # dark green — good variant
        'y': 1.0,   # yellow — noteworthy
        'o': 0.5,   # orange — corrective/problem
    }
    
    weighted_sum = 0.0
    for color, chars in color_stats.items():
        w = weights.get(color, 0.0)
        weighted_sum += w * chars
    
    # Normalize: max possible = 5.0 * total_chars
    # Scale to 0-100
    score = (weighted_sum / (5.0 * total_chars)) * 100.0
    return min(score, 100.0)


def build_shadow_database(rnt_path: str, output_path: str = None, 
                          verbose: bool = True):
    """
    Build the shadow database from an .rnt file.
    
    Args:
        rnt_path: Path to the RightNote .rnt database
        output_path: Path for the shadow DB (default: auto-generated)
        verbose: Print progress
    """
    rnt_path = Path(rnt_path)
    if not rnt_path.exists():
        print(f"Error: {rnt_path} not found")
        sys.exit(1)
    
    if output_path is None:
        output_path = rnt_path.parent / f"{rnt_path.stem}_shadow.db"
    output_path = Path(output_path)
    
    if verbose:
        print(f"Source: {rnt_path}")
        print(f"Shadow DB: {output_path}")
    
    # Open source (read-only)
    src = sqlite3.connect(f'file:{rnt_path}?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    
    # Create shadow DB
    if output_path.exists():
        output_path.unlink()
    shadow = sqlite3.connect(str(output_path))
    shadow.executescript(SHADOW_SCHEMA)
    shadow.execute("PRAGMA journal_mode=WAL")
    shadow.execute("PRAGMA synchronous=NORMAL")
    
    start_time = time.time()
    parser = RTFParser()
    
    # ── Phase 1: Parse all notes ──────────────────────────────────────
    if verbose:
        print("\n── Phase 1: Parsing notes ──")
    
    total_notes = src.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    
    cursor = src.execute("""
        SELECT n.uid, n.page_id, n.caption, n.bg_color, 
               n.date_created, n.last_modified,
               c.data, c.packed_size, c.size
        FROM notes n
        LEFT JOIN contents c ON n.uid = c.id
        ORDER BY n.page_id, n.uid
    """)
    
    batch = []
    processed = 0
    errors = 0
    
    for row in cursor:
        uid = row['uid']
        page_id = row['page_id']
        caption = row['caption'] or ''
        bg_color = row['bg_color'] or ''
        date_created = row['date_created']
        date_modified = row['last_modified']
        blob = row['data']
        packed_size = row['packed_size']
        
        parse_error = None
        parsed = None
        
        if blob:
            try:
                parsed = parser.parse_compressed(blob)
            except Exception as e:
                parse_error = str(e)
                errors += 1
        
        if parsed is None:
            parsed = parser._make_result_from_plain(caption)
            if not parse_error:
                parse_error = "no_content"
        
        # Word/line counts
        plain = parsed.plain_text
        word_count = len(plain.split()) if plain else 0
        line_count = plain.count('\n') + 1 if plain else 0
        
        # Salience
        salience = compute_salience(parsed.color_stats, parsed.total_chars)
        
        batch.append((
            uid, page_id, caption,
            sanitize_text(parsed.plain_text),
            sanitize_text(parsed.internal_format),
            parsed.total_chars,
            parsed.highlighted_chars,
            parsed.highlight_ratio,
            parsed.color_stats.get('p', 0),
            parsed.color_stats.get('b', 0),
            parsed.color_stats.get('g', 0),
            parsed.color_stats.get('y', 0),
            parsed.color_stats.get('o', 0),
            parsed.color_stats.get('u', 0),
            parsed.color_stats.get('g2', 0),
            bg_color,
            bg_color_to_semantic(bg_color),
            delphi_to_iso(date_created),
            delphi_to_iso(date_modified),
            date_created,
            word_count,
            line_count,
            1 if parsed.has_breaks else 0,
            parsed.break_count,
            packed_size,
            salience,
            parse_error,
        ))
        
        processed += 1
        if verbose and processed % 500 == 0:
            print(f"  Parsed {processed}/{total_notes} ({errors} errors)")
        
        # Batch insert every 200
        if len(batch) >= 200:
            shadow.executemany("""
                INSERT INTO parsed_notes VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
            """, batch)
            batch = []
    
    # Final batch
    if batch:
        shadow.executemany("""
            INSERT INTO parsed_notes VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
        """, batch)
    
    shadow.commit()
    if verbose:
        elapsed = time.time() - start_time
        print(f"  Done: {processed} notes in {elapsed:.1f}s ({errors} errors)")
    
    # ── Phase 2: Build tree hierarchy ─────────────────────────────────
    if verbose:
        print("\n── Phase 2: Building tree hierarchy ──")
    
    # Load all treenodes — KEY FIX: use (page_id, id) as composite key
    # because treenode IDs are only unique PER PAGE, not globally!
    treenodes = {}  # key = (page_id, id)
    # Also build a children lookup: (page_id, parent_id) -> [list of treenodes]
    children_lookup = {}  # (page_id, parent_id) -> [tn_dict, ...]
    
    for row in src.execute("""
        SELECT tn.id, tn.note_uid, tn.page_id, tn.parent_id, 
               tn."index", tn.child_count, tn.folder,
               n.caption
        FROM treenodes tn
        LEFT JOIN notes n ON tn.note_uid = n.uid
        ORDER BY tn.page_id, tn.parent_id, tn."index"
    """):
        d = dict(row)
        key = (d['page_id'], d['id'])
        treenodes[key] = d
        parent_key = (d['page_id'], d['parent_id'])
        if parent_key not in children_lookup:
            children_lookup[parent_key] = []
        children_lookup[parent_key].append(d)
    
    # Build hierarchy using iterative BFS
    # Find roots (parent_id = -1) per page
    hierarchy_rows = []
    queue = []
    
    # Detect separator nodes
    sep_pattern = re.compile(r'^[=\-_x]{3,}\s*$')
    
    for page in src.execute("SELECT id FROM pages ORDER BY \"index\""):
        page_id = page['id']
        roots = children_lookup.get((page_id, -1), [])
        roots.sort(key=lambda x: x['index'] or 0)
        
        for root in roots:
            caption = root['caption'] or ''
            is_sep = 1 if sep_pattern.match(caption.strip()) else 0
            is_marker = 1 if len(caption.strip()) < 3 and not caption.strip() else 0
            
            entry = {
                'treenode_id': root['id'],
                'note_uid': root['note_uid'],
                'page_id': page_id,
                'parent_treenode_id': -1,
                'display_index': root['index'],
                'depth': 0,
                'child_count': root['child_count'],
                'is_folder': root['folder'] or 0,
                'is_separator': is_sep,
                'is_marker': is_marker,
                'path': str(root['id']),
                'caption_path': caption[:50],
            }
            hierarchy_rows.append(entry)
            queue.append(entry)
    
    # BFS to build full hierarchy — always scoped to page_id
    while queue:
        parent = queue.pop(0)
        parent_id = parent['treenode_id']
        parent_page = parent['page_id']
        parent_depth = parent['depth']
        parent_path = parent['path']
        parent_caption_path = parent['caption_path']
        
        # Find children SCOPED to same page
        children = children_lookup.get((parent_page, parent_id), [])
        children.sort(key=lambda x: x['index'] or 0)
        
        for child in children:
            caption = child['caption'] or ''
            is_sep = 1 if sep_pattern.match(caption.strip()) else 0
            is_marker = 1 if len(caption.strip()) < 3 and not caption.strip() else 0
            
            child_path = f"{parent_path}/{child['id']}"
            child_caption_path = f"{parent_caption_path} > {caption[:30]}"
            
            entry = {
                'treenode_id': child['id'],
                'note_uid': child['note_uid'],
                'page_id': parent_page,
                'parent_treenode_id': parent_id,
                'display_index': child['index'],
                'depth': parent_depth + 1,
                'child_count': child['child_count'],
                'is_folder': child['folder'] or 0,
                'is_separator': is_sep,
                'is_marker': is_marker,
                'path': child_path,
                'caption_path': child_caption_path,
            }
            hierarchy_rows.append(entry)
            queue.append(entry)
    
    # Insert hierarchy
    for entry in hierarchy_rows:
        shadow.execute("""
            INSERT INTO tree_hierarchy (
                treenode_id, note_uid, page_id, parent_treenode_id,
                display_index, depth, child_count, is_folder,
                is_separator, is_marker, path, caption_path
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            entry['treenode_id'], entry['note_uid'], entry['page_id'],
            entry['parent_treenode_id'], entry['display_index'],
            entry['depth'], entry['child_count'], entry['is_folder'],
            entry['is_separator'], entry['is_marker'],
            entry['path'], entry['caption_path'],
        ))
    
    shadow.commit()
    
    if verbose:
        max_depth = max(e['depth'] for e in hierarchy_rows) if hierarchy_rows else 0
        print(f"  Built hierarchy: {len(hierarchy_rows)} nodes, max depth {max_depth}")
    
    # ── Phase 3: Aggregate subtree stats ──────────────────────────────
    if verbose:
        print("\n── Phase 3: Computing subtree aggregates ──")
    
    # Process bottom-up: start from deepest nodes
    # First, set leaf node stats from parsed_notes
    shadow.execute("""
        UPDATE tree_hierarchy SET
            subtree_note_count = 1,
            subtree_chars_total = COALESCE((
                SELECT chars_total FROM parsed_notes WHERE uid = tree_hierarchy.note_uid
            ), 0),
            subtree_chars_highlighted = COALESCE((
                SELECT chars_highlighted FROM parsed_notes WHERE uid = tree_hierarchy.note_uid
            ), 0),
            subtree_salience_score = COALESCE((
                SELECT salience_score FROM parsed_notes WHERE uid = tree_hierarchy.note_uid
            ), 0),
            subtree_chars_pink = COALESCE((
                SELECT chars_pink FROM parsed_notes WHERE uid = tree_hierarchy.note_uid
            ), 0),
            subtree_chars_blue = COALESCE((
                SELECT chars_blue FROM parsed_notes WHERE uid = tree_hierarchy.note_uid
            ), 0),
            subtree_chars_green = COALESCE((
                SELECT chars_green FROM parsed_notes WHERE uid = tree_hierarchy.note_uid
            ), 0),
            subtree_chars_yellow = COALESCE((
                SELECT chars_yellow FROM parsed_notes WHERE uid = tree_hierarchy.note_uid
            ), 0),
            subtree_chars_orange = COALESCE((
                SELECT chars_orange FROM parsed_notes WHERE uid = tree_hierarchy.note_uid
            ), 0),
            subtree_chars_purple = COALESCE((
                SELECT chars_purple FROM parsed_notes WHERE uid = tree_hierarchy.note_uid
            ), 0)
    """)
    shadow.commit()
    
    # Now roll up from leaves to roots
    # Get max depth
    max_depth_row = shadow.execute(
        "SELECT MAX(depth) FROM tree_hierarchy"
    ).fetchone()
    max_depth = max_depth_row[0] or 0
    
    for d in range(max_depth - 1, -1, -1):
        shadow.execute("""
            UPDATE tree_hierarchy SET
                subtree_note_count = subtree_note_count + COALESCE((
                    SELECT SUM(subtree_note_count) FROM tree_hierarchy c 
                    WHERE c.parent_treenode_id = tree_hierarchy.treenode_id
                      AND c.page_id = tree_hierarchy.page_id
                ), 0),
                subtree_chars_total = subtree_chars_total + COALESCE((
                    SELECT SUM(subtree_chars_total) FROM tree_hierarchy c 
                    WHERE c.parent_treenode_id = tree_hierarchy.treenode_id
                      AND c.page_id = tree_hierarchy.page_id
                ), 0),
                subtree_chars_highlighted = subtree_chars_highlighted + COALESCE((
                    SELECT SUM(subtree_chars_highlighted) FROM tree_hierarchy c 
                    WHERE c.parent_treenode_id = tree_hierarchy.treenode_id
                      AND c.page_id = tree_hierarchy.page_id
                ), 0),
                subtree_chars_pink = subtree_chars_pink + COALESCE((
                    SELECT SUM(subtree_chars_pink) FROM tree_hierarchy c 
                    WHERE c.parent_treenode_id = tree_hierarchy.treenode_id
                      AND c.page_id = tree_hierarchy.page_id
                ), 0),
                subtree_chars_blue = subtree_chars_blue + COALESCE((
                    SELECT SUM(subtree_chars_blue) FROM tree_hierarchy c 
                    WHERE c.parent_treenode_id = tree_hierarchy.treenode_id
                      AND c.page_id = tree_hierarchy.page_id
                ), 0),
                subtree_chars_green = subtree_chars_green + COALESCE((
                    SELECT SUM(subtree_chars_green) FROM tree_hierarchy c 
                    WHERE c.parent_treenode_id = tree_hierarchy.treenode_id
                      AND c.page_id = tree_hierarchy.page_id
                ), 0),
                subtree_chars_yellow = subtree_chars_yellow + COALESCE((
                    SELECT SUM(subtree_chars_yellow) FROM tree_hierarchy c 
                    WHERE c.parent_treenode_id = tree_hierarchy.treenode_id
                      AND c.page_id = tree_hierarchy.page_id
                ), 0),
                subtree_chars_orange = subtree_chars_orange + COALESCE((
                    SELECT SUM(subtree_chars_orange) FROM tree_hierarchy c 
                    WHERE c.parent_treenode_id = tree_hierarchy.treenode_id
                      AND c.page_id = tree_hierarchy.page_id
                ), 0),
                subtree_chars_purple = subtree_chars_purple + COALESCE((
                    SELECT SUM(subtree_chars_purple) FROM tree_hierarchy c 
                    WHERE c.parent_treenode_id = tree_hierarchy.treenode_id
                      AND c.page_id = tree_hierarchy.page_id
                ), 0)
            WHERE depth = ?
        """, (d,))
        shadow.commit()
    
    # Update ratios
    shadow.execute("""
        UPDATE tree_hierarchy SET
            subtree_highlight_ratio = CASE 
                WHEN subtree_chars_total > 0 
                THEN CAST(subtree_chars_highlighted AS REAL) / subtree_chars_total
                ELSE 0.0 
            END,
            subtree_salience_score = CASE
                WHEN subtree_chars_total > 0
                THEN (
                    (subtree_chars_pink * 5.0 + subtree_chars_purple * 4.0 + 
                     subtree_chars_blue * 3.0 + subtree_chars_green * 2.0 +
                     subtree_chars_yellow * 1.0 + subtree_chars_orange * 0.5)
                    / (5.0 * subtree_chars_total) * 100.0
                )
                ELSE 0.0
            END
    """)
    shadow.commit()
    
    if verbose:
        print("  Subtree aggregates computed")
    
    # ── Phase 4: Page statistics ──────────────────────────────────────
    if verbose:
        print("\n── Phase 4: Page statistics ──")
    
    for page in src.execute("SELECT id, caption FROM pages"):
        page_id = page['id']
        stats = shadow.execute("""
            SELECT COUNT(*) as cnt,
                   SUM(chars_total) as total_c,
                   SUM(chars_highlighted) as hl_c,
                   AVG(salience_score) as avg_sal
            FROM parsed_notes WHERE page_id = ?
        """, (page_id,)).fetchone()
        
        max_d = shadow.execute("""
            SELECT MAX(depth) FROM tree_hierarchy WHERE page_id = ?
        """, (page_id,)).fetchone()[0] or 0
        
        # Color distribution
        color_dist = shadow.execute("""
            SELECT SUM(chars_pink) as p, SUM(chars_blue) as b,
                   SUM(chars_green) as g, SUM(chars_yellow) as y,
                   SUM(chars_orange) as o, SUM(chars_purple) as u,
                   SUM(chars_dark_green) as g2
            FROM parsed_notes WHERE page_id = ?
        """, (page_id,)).fetchone()
        
        dist = {
            'pink': color_dist[0] or 0,
            'blue': color_dist[1] or 0,
            'green': color_dist[2] or 0,
            'yellow': color_dist[3] or 0,
            'orange': color_dist[4] or 0,
            'purple': color_dist[5] or 0,
            'dark_green': color_dist[6] or 0,
        }
        
        total_c = stats[1] or 0
        hl_c = stats[2] or 0
        
        shadow.execute("""
            INSERT OR REPLACE INTO page_stats VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            page_id, page['caption'], stats[0],
            total_c, hl_c,
            hl_c / total_c if total_c > 0 else 0.0,
            stats[3] or 0.0,
            max_d,
            json.dumps(dist),
        ))
    
    shadow.commit()
    
    # ── Build info ────────────────────────────────────────────────────
    shadow.execute("INSERT INTO build_info VALUES ('source', ?)", (str(rnt_path),))
    shadow.execute("INSERT INTO build_info VALUES ('built_at', ?)", 
                   (datetime.now().isoformat(),))
    shadow.execute("INSERT INTO build_info VALUES ('total_notes', ?)", (str(processed),))
    shadow.execute("INSERT INTO build_info VALUES ('parse_errors', ?)", (str(errors),))
    shadow.commit()
    
    # ── Summary ───────────────────────────────────────────────────────
    if verbose:
        total_time = time.time() - start_time
        print(f"\n{'='*60}")
        print(f"Shadow database built in {total_time:.1f}s")
        print(f"  Notes parsed: {processed} ({errors} errors)")
        print(f"  Tree nodes: {len(hierarchy_rows)}")
        print(f"  Output: {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
        
        # Quick stats
        print(f"\n── Page Summary ──")
        for row in shadow.execute("""
            SELECT caption, note_count, total_chars, highlight_ratio, 
                   avg_salience, max_depth
            FROM page_stats ORDER BY note_count DESC
        """):
            print(f"  {row[0]:35s} {row[1]:5d} notes, "
                  f"{row[2]:>10,} chars, "
                  f"{row[3]*100:5.1f}% hl, "
                  f"sal={row[4]:5.1f}, depth={row[5]}")
    
    src.close()
    shadow.close()
    
    return str(output_path)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python build_shadow_db.py <path_to.rnt> [output.db]")
        print("\nThis will create a shadow analysis database from your RightNote archive.")
        print("The original .rnt file is opened read-only and never modified.")
        sys.exit(0)
    
    rnt_file = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else None
    build_shadow_database(rnt_file, output)