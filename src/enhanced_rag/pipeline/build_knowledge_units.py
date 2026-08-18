"""
Knowledge Unit Extractor for RightNote Archives
=================================================

Phase 1 of the Semantic Knowledge System.

Extracts individual lines/spans from parsed notes as "knowledge units" —
the atomic retrievable elements of the knowledge graph. Each unit carries:
  - Text content
  - Color/weight from the highlight system
  - Bold status (increases effective weight by 0.5)
  - Tree path context (breadcrumb)
  - Section context (what color block this unit is part of)
  - Break context (section/block boundaries)
  - Source metadata (note uid, page, caption, date)

Usage:
    python build_knowledge_units.py <path_to_shadow.db> <path_to.rnt>

Requires: shadow DB already built via build_shadow_db.py
"""

import sqlite3
import json
import sys
import os
import re
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from rtf_parser import RTFParser, ParsedNote, TextSpan, bg_color_to_semantic, COLOR_NAMES


# ── Weight system ─────────────────────────────────────────────────────
COLOR_WEIGHTS = {
    'u': 5.0,   # purple — standout / rare peak
    'p': 4.0,   # pink — exceptional
    'b': 3.0,   # blue — excellent / high-salience
    'g': 2.0,   # green — good / validated
    'g2': 2.0,  # dark green — good variant
    'y': 1.0,   # yellow — noteworthy / provisional
    'o': 0.5,   # orange — corrective / needs revision
}

BOLD_BONUS = 0.5  # added to weight when bold + colored


# ── Delphi date ───────────────────────────────────────────────────────
DELPHI_EPOCH = datetime(1899, 12, 30)

def delphi_to_datetime(val):
    if not val or val <= 0:
        return None
    try:
        return DELPHI_EPOCH + timedelta(days=float(val))
    except:
        return None


# ── Data structures ───────────────────────────────────────────────────

@dataclass
class KnowledgeUnit:
    """A single retrievable unit of knowledge."""
    note_uid: int
    page_id: int
    line_index: int
    char_start: int
    char_end: int
    text: str
    text_normalized: str = ""
    
    # Color/weight
    color: Optional[str] = None
    weight: float = 0.0
    is_bold: bool = False
    effective_weight: float = 0.0
    
    # Inner highlights (e.g., yellow word inside blue line)
    inner_highlights: Optional[str] = None  # JSON: [{"text": "...", "color": "y", "start": 5, "end": 12}]
    
    # Section context
    section_color: Optional[str] = None    # dominant color of the enclosing section
    break_context: str = "mid_block"       # 'section_start', 'block_start', 'mid_block', 'after_break'
    
    # Tree context
    tree_path: str = ""
    caption: str = ""
    
    # Temporal
    date_created: Optional[str] = None
    note_age_days: int = 0
    
    # Quality flags
    is_separator: bool = False
    is_trivial: bool = False
    source_type: str = "content"  # 'content', 'highlight', 'caption'


# ── Separator / trivial detection ─────────────────────────────────────

SEPARATOR_PATTERN = re.compile(r'^[\s=\-_x*#~]{3,}$')
BREAK_TOKEN_PATTERN = re.compile(r'^\[BR[23]\]$')
TRIVIAL_MAX_CHARS = 4  # lines shorter than this (stripped) are trivial unless highlighted


def is_separator_line(text: str) -> bool:
    """Check if a line is a visual separator (====, ----, etc.)."""
    stripped = text.strip()
    if not stripped:
        return False
    return bool(SEPARATOR_PATTERN.match(stripped)) or bool(BREAK_TOKEN_PATTERN.match(stripped))


def is_trivial_line(text: str, has_color: bool) -> bool:
    """Check if a line is too short/empty to be meaningful."""
    stripped = text.strip()
    if not stripped:
        return True
    # Colored lines are never trivial (user explicitly highlighted them)
    if has_color:
        return False
    # Very short unhighlighted lines
    if len(stripped) <= TRIVIAL_MAX_CHARS:
        return True
    return False


def normalize_text(text: str) -> str:
    """Normalize text for dedup: lowercase, collapse whitespace, strip."""
    t = text.lower().strip()
    t = re.sub(r'\s+', ' ', t)
    return t


# ── Core extraction logic ─────────────────────────────────────────────

def extract_units_from_spans(
    spans: List[TextSpan],
    note_uid: int,
    page_id: int,
    caption: str,
    tree_path: str,
    date_created: Optional[str],
    note_age_days: int,
) -> List[KnowledgeUnit]:
    """
    Extract knowledge units from a list of TextSpans.
    
    Strategy:
    - Walk through spans, accumulating text for the current line
    - A "line" ends at \n boundaries in the span text
    - Each line inherits the dominant color of its spans
    - If a line has mixed colors, the dominant one becomes the unit color,
      and minority colors become inner_highlights metadata
    - Contiguous same-color spans on the same line merge into one unit
    """
    units = []
    
    # First: flatten spans into a sequence of (char, color, bold, italic) 
    # but that's expensive for large notes. Instead, work span-by-span.
    
    # We track the current line being built
    current_line_parts = []  # [(text, color, bold)]
    char_offset = 0
    line_index = 0
    
    # Section context tracking
    section_color = None           # current section's dominant color
    section_color_run_chars = 0    # how many chars of current section color
    after_break = False
    break_type = None
    
    for span in spans:
        text = span.text
        color = span.highlight
        bold = span.bold
        
        # Split span text by newlines
        lines_in_span = text.split('\n')
        
        for i, line_text in enumerate(lines_in_span):
            if i > 0:
                # We hit a newline — flush the current line
                if current_line_parts:
                    unit = _build_unit_from_parts(
                        current_line_parts, note_uid, page_id, caption,
                        tree_path, date_created, note_age_days,
                        line_index, char_offset - sum(len(p[0]) for p in current_line_parts),
                        char_offset,
                        section_color, break_type,
                    )
                    if unit:
                        units.append(unit)
                    line_index += 1
                    current_line_parts = []
                    break_type = None
                else:
                    # Empty line — might indicate a break
                    line_index += 1
            
            if line_text:
                # Check for break tokens
                stripped = line_text.strip()
                if stripped in ('[BR2]', '[BR3]'):
                    break_type = 'section_start' if stripped == '[BR3]' else 'block_start'
                    # Reset section color on section break
                    if stripped == '[BR3]':
                        section_color = None
                        section_color_run_chars = 0
                    char_offset += len(line_text)
                    continue
                
                current_line_parts.append((line_text, color, bold))
                
                # Update section color tracking
                if color:
                    if color == section_color:
                        section_color_run_chars += len(line_text)
                    elif section_color is None or len(line_text) > section_color_run_chars:
                        section_color = color
                        section_color_run_chars = len(line_text)
                
                char_offset += len(line_text)
            
            if i < len(lines_in_span) - 1:
                char_offset += 1  # for the \n
    
    # Flush final line
    if current_line_parts:
        unit = _build_unit_from_parts(
            current_line_parts, note_uid, page_id, caption,
            tree_path, date_created, note_age_days,
            line_index, char_offset - sum(len(p[0]) for p in current_line_parts),
            char_offset,
            section_color, break_type,
        )
        if unit:
            units.append(unit)
    
    return units


def _build_unit_from_parts(
    parts: List[Tuple[str, Optional[str], bool]],
    note_uid: int,
    page_id: int,
    caption: str,
    tree_path: str,
    date_created: Optional[str],
    note_age_days: int,
    line_index: int,
    char_start: int,
    char_end: int,
    section_color: Optional[str],
    break_context_override: Optional[str],
) -> Optional[KnowledgeUnit]:
    """
    Build a KnowledgeUnit from accumulated line parts.
    
    Parts: [(text, color, bold), ...]
    
    Determines dominant color, detects inner highlights, computes weight.
    """
    # Combine text
    full_text = ''.join(p[0] for p in parts)
    stripped = full_text.strip()
    
    if not stripped:
        return None
    
    # Determine dominant color and bold status
    # Count chars per color
    color_chars = {}  # color -> char_count
    bold_chars = 0
    total_chars = 0
    any_bold = False
    
    for text, color, bold in parts:
        t = text.strip()
        n = len(t)
        if n == 0:
            continue
        total_chars += n
        if color:
            color_chars[color] = color_chars.get(color, 0) + n
        if bold:
            bold_chars += n
            any_bold = True
    
    if total_chars == 0:
        return None
    
    # Dominant color = the one with most chars
    dominant_color = None
    dominant_chars = 0
    for c, n in color_chars.items():
        if n > dominant_chars:
            dominant_color = c
            dominant_chars = n
    
    # Is bold? True if majority of chars are bold
    is_bold = bold_chars > total_chars * 0.5
    
    # Inner highlights: minority colors within the line
    inner_highlights = None
    if len(color_chars) > 1 or (dominant_color and dominant_chars < total_chars):
        inner_hl = []
        pos = 0
        for text, color, bold in parts:
            t = text.strip()
            if t and color and color != dominant_color:
                inner_hl.append({
                    "text": t[:100],  # cap for storage
                    "color": color,
                    "start": pos,
                    "end": pos + len(t),
                })
            pos += len(text)
        if inner_hl:
            inner_highlights = json.dumps(inner_hl)
    
    # Compute weight
    weight = COLOR_WEIGHTS.get(dominant_color, 0.0) if dominant_color else 0.0
    effective_weight = weight
    if is_bold and dominant_color:
        effective_weight += BOLD_BONUS
    
    # Break context
    if break_context_override:
        break_context = break_context_override
    else:
        break_context = "mid_block"
    
    # Quality flags
    is_sep = is_separator_line(stripped)
    is_triv = is_trivial_line(stripped, dominant_color is not None)
    
    return KnowledgeUnit(
        note_uid=note_uid,
        page_id=page_id,
        line_index=line_index,
        char_start=char_start,
        char_end=char_end,
        text=stripped,
        text_normalized=normalize_text(stripped),
        color=dominant_color,
        weight=weight,
        is_bold=is_bold,
        effective_weight=effective_weight,
        inner_highlights=inner_highlights,
        section_color=section_color,
        break_context=break_context,
        tree_path=tree_path,
        caption=caption,
        date_created=date_created,
        note_age_days=note_age_days,
        is_separator=is_sep,
        is_trivial=is_triv,
        source_type="content",
    )


def make_caption_unit(
    note_uid: int,
    page_id: int,
    caption: str,
    tree_path: str,
    date_created: Optional[str],
    note_age_days: int,
    bg_color: Optional[str],
    bg_semantic: Optional[str],
) -> Optional[KnowledgeUnit]:
    """
    Create a knowledge unit from a note caption (treenode label).
    
    Caption-only nodes are important: they mark things as particularly 
    significant, especially when the treenode has a background color.
    The bg_color uses the same semantic weight scale as in-note highlights.
    """
    stripped = (caption or '').strip()
    if not stripped:
        return None
    
    # Separator captions
    if is_separator_line(stripped):
        return None
    
    # Weight from treenode bg color (same scale as highlights, but even more important)
    color = bg_semantic
    weight = COLOR_WEIGHTS.get(color, 0.0) if color else 0.0
    # bg-colored treenodes get a small extra boost — user explicitly marked the node
    if color:
        weight += 0.5  
    
    return KnowledgeUnit(
        note_uid=note_uid,
        page_id=page_id,
        line_index=-1,  # caption, not a content line
        char_start=0,
        char_end=len(stripped),
        text=stripped,
        text_normalized=normalize_text(stripped),
        color=color,
        weight=weight,
        is_bold=False,
        effective_weight=weight,
        inner_highlights=None,
        section_color=None,
        break_context="caption",
        tree_path=tree_path,
        caption=stripped,
        date_created=date_created,
        note_age_days=note_age_days,
        is_separator=False,
        is_trivial=len(stripped) <= TRIVIAL_MAX_CHARS and not color,
        source_type="caption",
    )


# ── Schema ────────────────────────────────────────────────────────────

KU_SCHEMA = """
-- Knowledge units: the atomic retrievable elements of the knowledge graph
CREATE TABLE IF NOT EXISTS knowledge_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_uid INTEGER NOT NULL,
    page_id INTEGER NOT NULL,
    
    -- Position within note
    line_index INTEGER,           -- line number in note (-1 for captions)
    char_start INTEGER,           -- character offset in plain_text
    char_end INTEGER,
    
    -- Content
    text TEXT NOT NULL,
    text_normalized TEXT,         -- lowercased, collapsed whitespace
    
    -- Semantic weight
    color TEXT,                   -- p/b/g/y/o/u/g2 or NULL
    weight REAL DEFAULT 0.0,     -- base weight from color
    is_bold INTEGER DEFAULT 0,
    effective_weight REAL DEFAULT 0.0,  -- weight + bold_bonus
    
    -- Inner highlights (minority colors within the line)
    inner_highlights TEXT,        -- JSON: [{"text","color","start","end"}]
    
    -- Section context
    section_color TEXT,           -- dominant color of enclosing section
    break_context TEXT DEFAULT 'mid_block',  -- section_start/block_start/mid_block/caption
    
    -- Tree context
    tree_path TEXT,               -- breadcrumb: "Belief-System > Ethics > ..."
    caption TEXT,                 -- parent note caption
    
    -- Temporal
    date_created TEXT,            -- ISO from parent note
    note_age_days INTEGER DEFAULT 0,
    
    -- Quality flags
    is_separator INTEGER DEFAULT 0,
    is_trivial INTEGER DEFAULT 0,
    is_duplicate INTEGER DEFAULT 0,   -- set later by dedup pass
    source_type TEXT DEFAULT 'content',  -- 'content', 'caption'
    
    -- Embedding (Phase 1b — added later)
    embedding BLOB,
    
    -- Clustering (Phase 2 — added later)
    cluster_id INTEGER,
    concept_ids TEXT              -- JSON array
);

CREATE INDEX IF NOT EXISTS idx_ku_note ON knowledge_units(note_uid);
CREATE INDEX IF NOT EXISTS idx_ku_page ON knowledge_units(page_id);
CREATE INDEX IF NOT EXISTS idx_ku_weight ON knowledge_units(effective_weight DESC);
CREATE INDEX IF NOT EXISTS idx_ku_color ON knowledge_units(color);
CREATE INDEX IF NOT EXISTS idx_ku_section_color ON knowledge_units(section_color);
CREATE INDEX IF NOT EXISTS idx_ku_source_type ON knowledge_units(source_type);
CREATE INDEX IF NOT EXISTS idx_ku_normalized ON knowledge_units(text_normalized);
CREATE INDEX IF NOT EXISTS idx_ku_trivial ON knowledge_units(is_trivial, is_separator);

-- Summary stats per note (how many units, weight distribution)
CREATE TABLE IF NOT EXISTS note_unit_stats (
    note_uid INTEGER PRIMARY KEY,
    total_units INTEGER DEFAULT 0,
    highlighted_units INTEGER DEFAULT 0,
    caption_units INTEGER DEFAULT 0,
    avg_weight REAL DEFAULT 0.0,
    max_weight REAL DEFAULT 0.0,
    weight_distribution TEXT       -- JSON: {"p": 5, "b": 12, ...}
);

-- Build metadata
CREATE TABLE IF NOT EXISTS ku_build_info (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


# ── Main builder ──────────────────────────────────────────────────────

def sanitize_text(text):
    """Remove surrogate characters that can't be encoded to UTF-8."""
    if text is None:
        return None
    return text.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')


def build_knowledge_units(
    shadow_db_path: str,
    rnt_path: str,
    verbose: bool = True,
):
    """
    Build knowledge units from the shadow DB + original .rnt file.
    
    We need the .rnt for the raw RTF content (to get spans), and 
    the shadow DB for tree paths, parsed metadata, and to store results.
    
    Args:
        shadow_db_path: Path to the shadow database (will be extended)
        rnt_path: Path to the original .rnt file (read-only)
        verbose: Print progress
    """
    shadow_path = Path(shadow_db_path)
    rnt_path = Path(rnt_path)
    
    if not shadow_path.exists():
        print(f"Error: Shadow DB not found: {shadow_path}")
        print("Run build_shadow_db.py first.")
        sys.exit(1)
    if not rnt_path.exists():
        print(f"Error: .rnt file not found: {rnt_path}")
        sys.exit(1)
    
    if verbose:
        print(f"Shadow DB: {shadow_path}")
        print(f"Source .rnt: {rnt_path}")
    
    # Open connections
    shadow = sqlite3.connect(str(shadow_path))
    shadow.row_factory = sqlite3.Row
    shadow.execute("PRAGMA journal_mode=WAL")
    shadow.execute("PRAGMA synchronous=NORMAL")
    
    rnt = sqlite3.connect(f'file:{rnt_path}?mode=ro', uri=True)
    rnt.row_factory = sqlite3.Row
    
    # Drop old tables if rebuilding
    shadow.execute("DROP TABLE IF EXISTS knowledge_units")
    shadow.execute("DROP TABLE IF EXISTS note_unit_stats")
    shadow.execute("DROP TABLE IF EXISTS ku_build_info")
    shadow.commit()
    
    # Create schema
    shadow.executescript(KU_SCHEMA)
    shadow.commit()
    
    start_time = time.time()
    parser = RTFParser()
    now = datetime.now()
    
    # Load tree paths from shadow DB
    if verbose:
        print("\n── Loading tree paths ──")
    
    tree_paths = {}  # note_uid -> caption_path
    for row in shadow.execute("SELECT note_uid, caption_path FROM tree_hierarchy WHERE note_uid IS NOT NULL"):
        tree_paths[row['note_uid']] = row['caption_path'] or ''
    
    if verbose:
        print(f"  Loaded {len(tree_paths)} tree paths")
    
    # Load note metadata from shadow DB (dates, bg_color)
    note_meta = {}  # uid -> {date_created, bg_color, bg_semantic, caption}
    for row in shadow.execute("""
        SELECT uid, page_id, caption, date_created, date_created_delphi,
               note_bg_color, note_bg_semantic
        FROM parsed_notes
    """):
        dt = delphi_to_datetime(row['date_created_delphi']) if row['date_created_delphi'] else None
        age_days = (now - dt).days if dt else 0
        note_meta[row['uid']] = {
            'page_id': row['page_id'],
            'caption': row['caption'] or '',
            'date_created': row['date_created'],
            'date_created_delphi': row['date_created_delphi'],
            'age_days': max(0, age_days),
            'bg_color': row['note_bg_color'],
            'bg_semantic': row['note_bg_semantic'],
        }
    
    if verbose:
        print(f"  Loaded metadata for {len(note_meta)} notes")
    
    # ── Process notes ─────────────────────────────────────────────────
    if verbose:
        print("\n── Extracting knowledge units ──")
    
    total_notes = rnt.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    
    cursor = rnt.execute("""
        SELECT n.uid, n.page_id, n.caption, c.data
        FROM notes n
        LEFT JOIN contents c ON n.uid = c.id
        ORDER BY n.page_id, n.uid
    """)
    
    all_units = []
    processed = 0
    errors = 0
    total_content_units = 0
    total_caption_units = 0
    notes_with_units = 0
    
    for row in cursor:
        uid = row['uid']
        page_id = row['page_id']
        note_caption = row['caption'] or ''
        blob = row['data']
        
        meta = note_meta.get(uid, {})
        tree_path = tree_paths.get(uid, note_caption[:50])
        date_created = meta.get('date_created')
        age_days = meta.get('age_days', 0)
        bg_semantic = meta.get('bg_semantic')
        bg_color = meta.get('bg_color')
        
        note_units = []
        
        # 1. Caption unit (always, if non-trivial)
        caption_unit = make_caption_unit(
            uid, page_id, note_caption, tree_path,
            date_created, age_days, bg_color, bg_semantic,
        )
        if caption_unit:
            note_units.append(caption_unit)
            total_caption_units += 1
        
        # 2. Content units (from RTF spans)
        if blob:
            try:
                parsed = parser.parse_compressed(blob)
                if parsed.spans:
                    content_units = extract_units_from_spans(
                        parsed.spans, uid, page_id, note_caption,
                        tree_path, date_created, age_days,
                    )
                    note_units.extend(content_units)
                    total_content_units += len(content_units)
            except Exception as e:
                errors += 1
                if verbose and errors <= 10:
                    print(f"  Error parsing uid={uid}: {e}")
        
        if note_units:
            notes_with_units += 1
            all_units.extend(note_units)
        
        processed += 1
        if verbose and processed % 1000 == 0:
            print(f"  Processed {processed}/{total_notes} notes "
                  f"({len(all_units)} units, {errors} errors)")
    
    if verbose:
        print(f"\n  Total: {len(all_units)} units from {notes_with_units} notes")
        print(f"    Content units: {total_content_units}")
        print(f"    Caption units: {total_caption_units}")
        print(f"    Parse errors: {errors}")
    
    # ── Batch insert ──────────────────────────────────────────────────
    if verbose:
        print("\n── Inserting into shadow DB ──")
    
    batch = []
    for u in all_units:
        batch.append((
            u.note_uid, u.page_id,
            u.line_index, u.char_start, u.char_end,
            sanitize_text(u.text),
            sanitize_text(u.text_normalized),
            u.color, u.weight, 1 if u.is_bold else 0,
            u.effective_weight,
            u.inner_highlights,
            u.section_color, u.break_context,
            u.tree_path, u.caption,
            u.date_created, u.note_age_days,
            1 if u.is_separator else 0,
            1 if u.is_trivial else 0,
            0,  # is_duplicate (set later)
            u.source_type,
            None,  # embedding (Phase 1b)
            None,  # cluster_id (Phase 2)
            None,  # concept_ids (Phase 2)
        ))
        
        if len(batch) >= 500:
            shadow.executemany("""
                INSERT INTO knowledge_units (
                    note_uid, page_id,
                    line_index, char_start, char_end,
                    text, text_normalized,
                    color, weight, is_bold, effective_weight,
                    inner_highlights,
                    section_color, break_context,
                    tree_path, caption,
                    date_created, note_age_days,
                    is_separator, is_trivial, is_duplicate,
                    source_type,
                    embedding, cluster_id, concept_ids
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, batch)
            batch = []
    
    if batch:
        shadow.executemany("""
            INSERT INTO knowledge_units (
                note_uid, page_id,
                line_index, char_start, char_end,
                text, text_normalized,
                color, weight, is_bold, effective_weight,
                inner_highlights,
                section_color, break_context,
                tree_path, caption,
                date_created, note_age_days,
                is_separator, is_trivial, is_duplicate,
                source_type,
                embedding, cluster_id, concept_ids
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, batch)
    
    shadow.commit()
    
    if verbose:
        print(f"  Inserted {len(all_units)} knowledge units")
    
    # ── Note-level stats ──────────────────────────────────────────────
    if verbose:
        print("\n── Computing note-level stats ──")
    
    shadow.execute("""
        INSERT INTO note_unit_stats (note_uid, total_units, highlighted_units,
                                     caption_units, avg_weight, max_weight, weight_distribution)
        SELECT 
            note_uid,
            COUNT(*) as total_units,
            SUM(CASE WHEN color IS NOT NULL THEN 1 ELSE 0 END) as highlighted_units,
            SUM(CASE WHEN source_type = 'caption' THEN 1 ELSE 0 END) as caption_units,
            AVG(effective_weight) as avg_weight,
            MAX(effective_weight) as max_weight,
            json_object(
                'u', SUM(CASE WHEN color = 'u' THEN 1 ELSE 0 END),
                'p', SUM(CASE WHEN color = 'p' THEN 1 ELSE 0 END),
                'b', SUM(CASE WHEN color = 'b' THEN 1 ELSE 0 END),
                'g', SUM(CASE WHEN color = 'g' THEN 1 ELSE 0 END),
                'g2', SUM(CASE WHEN color = 'g2' THEN 1 ELSE 0 END),
                'y', SUM(CASE WHEN color = 'y' THEN 1 ELSE 0 END),
                'o', SUM(CASE WHEN color = 'o' THEN 1 ELSE 0 END)
            ) as weight_distribution
        FROM knowledge_units
        GROUP BY note_uid
    """)
    shadow.commit()
    
    # ── Exact dedup pass ──────────────────────────────────────────────
    if verbose:
        print("\n── Running dedup pass ──")
    
    # Mark exact text duplicates (same normalized text, same page)
    # Keep the one with highest effective_weight
    dup_count = shadow.execute("""
        UPDATE knowledge_units SET is_duplicate = 1
        WHERE id NOT IN (
            SELECT MIN(id) FROM knowledge_units
            WHERE is_trivial = 0 AND is_separator = 0
            GROUP BY page_id, text_normalized
        )
        AND is_trivial = 0 AND is_separator = 0
        AND text_normalized IN (
            SELECT text_normalized FROM knowledge_units
            WHERE is_trivial = 0 AND is_separator = 0
            GROUP BY page_id, text_normalized
            HAVING COUNT(*) > 1
        )
    """).rowcount
    shadow.commit()
    
    if verbose:
        print(f"  Marked {dup_count} exact duplicates")
    
    # ── Build info ────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    
    shadow.execute("INSERT INTO ku_build_info VALUES ('built_at', ?)",
                   (datetime.now().isoformat(),))
    shadow.execute("INSERT INTO ku_build_info VALUES ('source_rnt', ?)",
                   (str(rnt_path),))
    shadow.execute("INSERT INTO ku_build_info VALUES ('source_shadow', ?)",
                   (str(shadow_path),))
    shadow.execute("INSERT INTO ku_build_info VALUES ('total_units', ?)",
                   (str(len(all_units)),))
    shadow.execute("INSERT INTO ku_build_info VALUES ('elapsed_seconds', ?)",
                   (f"{elapsed:.1f}",))
    shadow.commit()
    
    # ── Summary report ────────────────────────────────────────────────
    if verbose:
        print(f"\n{'='*60}")
        print(f"Knowledge units built in {elapsed:.1f}s")
        print(f"  Total units: {len(all_units)}")
        
        # Stats
        stats = shadow.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN color IS NOT NULL THEN 1 ELSE 0 END) as highlighted,
                SUM(CASE WHEN is_trivial = 1 THEN 1 ELSE 0 END) as trivial,
                SUM(CASE WHEN is_separator = 1 THEN 1 ELSE 0 END) as separators,
                SUM(CASE WHEN is_duplicate = 1 THEN 1 ELSE 0 END) as duplicates,
                SUM(CASE WHEN source_type = 'caption' THEN 1 ELSE 0 END) as captions,
                AVG(effective_weight) as avg_weight,
                AVG(LENGTH(text)) as avg_length
            FROM knowledge_units
        """).fetchone()
        
        print(f"  Highlighted: {stats['highlighted']} ({stats['highlighted']*100/stats['total']:.1f}%)")
        print(f"  Trivial: {stats['trivial']}")
        print(f"  Separators: {stats['separators']}")
        print(f"  Duplicates: {stats['duplicates']}")
        print(f"  Captions: {stats['captions']}")
        print(f"  Avg effective weight: {stats['avg_weight']:.2f}")
        print(f"  Avg text length: {stats['avg_length']:.0f} chars")
        
        # Weight distribution
        print(f"\n── Weight distribution ──")
        for row in shadow.execute("""
            SELECT color, COUNT(*) as cnt, 
                   AVG(effective_weight) as avg_w,
                   AVG(LENGTH(text)) as avg_len
            FROM knowledge_units
            WHERE is_trivial = 0 AND is_separator = 0
            GROUP BY color
            ORDER BY avg_w DESC
        """):
            cname = COLOR_NAMES.get(row['color'], row['color'] or 'none')
            print(f"  {cname:12s}: {row['cnt']:7d} units, "
                  f"avg_w={row['avg_w']:.2f}, avg_len={row['avg_len']:.0f}")
        
        # Per-page stats
        print(f"\n── Per-page unit counts ──")
        for row in shadow.execute("""
            SELECT ku.page_id, ps.caption, 
                   COUNT(*) as total,
                   SUM(CASE WHEN ku.color IS NOT NULL THEN 1 ELSE 0 END) as highlighted,
                   AVG(ku.effective_weight) as avg_w
            FROM knowledge_units ku
            JOIN page_stats ps ON ku.page_id = ps.page_id
            WHERE ku.is_trivial = 0 AND ku.is_separator = 0
            GROUP BY ku.page_id
            ORDER BY total DESC
        """):
            print(f"  {row['caption']:35s}: {row['total']:7d} units, "
                  f"{row['highlighted']:6d} highlighted, avg_w={row['avg_w']:.2f}")
        
        # Top 10 highest-weight units
        print(f"\n── Top 10 highest-weight units ──")
        for row in shadow.execute("""
            SELECT text, color, effective_weight, is_bold, tree_path, source_type
            FROM knowledge_units
            WHERE is_trivial = 0 AND is_separator = 0 AND is_duplicate = 0
            ORDER BY effective_weight DESC, LENGTH(text) DESC
            LIMIT 10
        """):
            color = COLOR_NAMES.get(row['color'], '?')
            bold = " [BOLD]" if row['is_bold'] else ""
            src = f" ({row['source_type']})" if row['source_type'] != 'content' else ""
            preview = row['text'][:80].replace('\n', ' ')
            print(f"  w={row['effective_weight']:.1f} [{color}]{bold}{src}: {preview}")
        
        # Embeddable units count (weight >= 1, not trivial/separator/duplicate)
        embeddable = shadow.execute("""
            SELECT COUNT(*) FROM knowledge_units
            WHERE effective_weight >= 1.0
              AND is_trivial = 0 AND is_separator = 0 AND is_duplicate = 0
        """).fetchone()[0]
        print(f"\n  Embeddable units (weight >= 1): {embeddable}")
        
        all_nontrivial = shadow.execute("""
            SELECT COUNT(*) FROM knowledge_units
            WHERE is_trivial = 0 AND is_separator = 0 AND is_duplicate = 0
        """).fetchone()[0]
        print(f"  All non-trivial unique units: {all_nontrivial}")
    
    rnt.close()
    shadow.close()
    
    return len(all_units)


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python build_knowledge_units.py <shadow.db> <source.rnt>")
        print("\nExtract line-level knowledge units from a RightNote archive.")
        print("Requires: shadow DB from build_shadow_db.py")
        sys.exit(0)
    
    shadow_db = sys.argv[1]
    rnt_file = sys.argv[2]
    
    count = build_knowledge_units(shadow_db, rnt_file)
    print(f"\nDone. {count} knowledge units extracted.")