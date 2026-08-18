"""
RightNote CRUD Toolkit v2 (FIXED)
===================================

CRITICAL FIX: When creating notes, we clone ALL field values from an existing 
real note. This ensures fields like flags, protected, keywords, ct, etc. are
in exactly the format RightNote expects.

CRITICAL FIX #2: FTS updates go through notes_fts_content (the shadow content
table) directly, NOT through INSERT/DELETE on the FTS virtual table, which
corrupts the B-tree segment structure.
"""

import sqlite3
import zlib
import re
import os
import sys
import uuid
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Set, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rtf_parser import (RTFParser, ParsedNote, bg_color_to_semantic, 
                         COLOR_NAMES, extract_highlighted_only, extract_high_salience)

DELPHI_EPOCH = datetime(1899, 12, 30)

def datetime_to_delphi(dt: datetime) -> float:
    delta = dt - DELPHI_EPOCH
    return delta.total_seconds() / 86400.0

def delphi_to_datetime(val: float) -> Optional[datetime]:
    if not val or val <= 0: return None
    try: return DELPHI_EPOCH + timedelta(days=float(val))
    except: return None

def delphi_now() -> float:
    return datetime_to_delphi(datetime.now())

def make_guid() -> str:
    raw = str(uuid.uuid4())
    return 'abcd' + raw[4:]

MINIMAL_RTF_TEMPLATE = (
    r"{\rtf1\ansi\deff0"
    r"{\fonttbl{\f0\fnil\fcharset0 Calibri;}}"
    r"{\colortbl ;"
    r"\red255\green153\blue204;"
    r"\red204\green255\blue255;"
    r"\red204\green255\blue204;"
    r"\red255\green255\blue255;"
    r"\red255\green255\blue153;"
    r"\red255\green204\blue153;"
    r"\red204\green153\blue255;"
    r"}"
    r"\viewkind4\uc1"
    r"\pard\f0\fs20 {TEXT}}"
)

HIGHLIGHT_INDEX = {'p': 1, 'b': 2, 'g': 3, 'y': 5, 'o': 6, 'u': 7}
COLOR_RGB = {
    'p': (255, 153, 204), 'b': (204, 255, 255), 'g': (204, 255, 204),
    'y': (255, 255, 153), 'o': (255, 204, 153), 'u': (204, 153, 255),
}


class NoteInfo:
    def __init__(self, row: dict, parsed: ParsedNote = None):
        self.uid = row.get('uid')
        self.page_id = row.get('page_id')
        self.caption = row.get('caption', '')
        self.bg_color = row.get('bg_color', '')
        self.bg_semantic = bg_color_to_semantic(self.bg_color)
        self.date_created = delphi_to_datetime(row.get('date_created'))
        self.last_modified = delphi_to_datetime(row.get('last_modified'))
        self.flags = row.get('flags', '')
        self.tags = row.get('tags', '')
        self.guid = row.get('guid', '')
        self.parsed = parsed
        self.plain_text = parsed.plain_text if parsed else ''
        self.internal_format = parsed.internal_format if parsed else ''
        self.color_stats = parsed.color_stats if parsed else {}
        self.salience = 0.0
        self.highlight_ratio = parsed.highlight_ratio if parsed else 0.0
    def __repr__(self):
        return f"NoteInfo(uid={self.uid}, caption='{self.caption[:50]}')"


class TreeNodeInfo:
    def __init__(self, row: dict):
        self.treenode_id = row.get('id')
        self.page_id = row.get('page_id')
        self.parent_id = row.get('parent_id')
        self.index = row.get('index')
        self.note_uid = row.get('note_uid')
        self.child_count = row.get('child_count', 0)
        self.expanded = row.get('expanded', 0)
        self.folder = row.get('folder', 0)
        self.caption = row.get('caption', '')
        self.bg_color = row.get('bg_color', '')
    def __repr__(self):
        return f"TreeNode(id={self.treenode_id}, page={self.page_id}, '{self.caption[:40]}')"


class RightNoteDB:
    def __init__(self, path: str, readonly: bool = False):
        self.path = Path(path)
        self.readonly = readonly
        self.parser = RTFParser()
        if not self.path.exists():
            raise FileNotFoundError(f"Database not found: {self.path}")
        if readonly:
            self.conn = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
        else:
            self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._ref_note = None

    def close(self):
        self.conn.close()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()

    # ── Reference note (for safe cloning) ─────────────────────────────
    def _get_reference_note(self) -> dict:
        if self._ref_note: return self._ref_note
        row = self.conn.execute("SELECT * FROM notes ORDER BY uid DESC LIMIT 1").fetchone()
        if row: self._ref_note = dict(row)
        return self._ref_note

    # ── ID allocation ─────────────────────────────────────────────────
    def _next_note_uid(self) -> int:
        row = self.conn.execute("SELECT MAX(uid) FROM notes").fetchone()
        return (row[0] or 0) + 1
    
    def _next_treenode_id(self, page_id: int) -> int:
        row = self.conn.execute(
            "SELECT MAX(id) FROM treenodes WHERE page_id = ?", (page_id,)
        ).fetchone()
        return (row[0] or 0) + 1

    # ══════════════════════════════════════════════════════════════════
    # READ OPERATIONS
    # ══════════════════════════════════════════════════════════════════
    
    def get_pages(self) -> List[dict]:
        rows = self.conn.execute(
            'SELECT id, "index", caption, color, last_edit FROM pages ORDER BY "index"'
        ).fetchall()
        return [dict(r) for r in rows]
    
    def get_note(self, uid: int, parse_content: bool = True) -> Optional[NoteInfo]:
        row = self.conn.execute("""
            SELECT n.uid, n.page_id, n.caption, n.bg_color, n.font, n.tags,
                   n.flags, n.date_created, n.last_modified, n.guid,
                   c.data, c.packed_size, c.size, c.flags as c_flags
            FROM notes n LEFT JOIN contents c ON n.uid = c.id
            WHERE n.uid = ?
        """, (uid,)).fetchone()
        if not row: return None
        parsed = None
        if parse_content and row['data']:
            parsed = self._parse_blob(row['data'])
        return NoteInfo(dict(row), parsed)
    
    def get_note_raw_rtf(self, uid: int) -> Optional[str]:
        row = self.conn.execute("SELECT data FROM contents WHERE id = ?", (uid,)).fetchone()
        if not row or not row['data']: return None
        return self._decompress_blob(row['data'])
    
    def get_treenode(self, treenode_id: int, page_id: int = None) -> Optional[TreeNodeInfo]:
        if page_id is not None:
            row = self.conn.execute("""
                SELECT tn.id, tn.page_id, tn.parent_id, tn."index", tn.note_uid,
                       tn.child_count, tn.expanded, tn.folder, n.caption, n.bg_color
                FROM treenodes tn LEFT JOIN notes n ON tn.note_uid = n.uid
                WHERE tn.id = ? AND tn.page_id = ?
            """, (treenode_id, page_id)).fetchone()
        else:
            row = self.conn.execute("""
                SELECT tn.id, tn.page_id, tn.parent_id, tn."index", tn.note_uid,
                       tn.child_count, tn.expanded, tn.folder, n.caption, n.bg_color
                FROM treenodes tn LEFT JOIN notes n ON tn.note_uid = n.uid
                WHERE tn.id = ?
            """, (treenode_id,)).fetchone()
        return TreeNodeInfo(dict(row)) if row else None
    
    def get_treenode_by_uid(self, note_uid: int) -> Optional[TreeNodeInfo]:
        row = self.conn.execute("""
            SELECT tn.id, tn.page_id, tn.parent_id, tn."index", tn.note_uid,
                   tn.child_count, tn.expanded, tn.folder, n.caption, n.bg_color
            FROM treenodes tn LEFT JOIN notes n ON tn.note_uid = n.uid
            WHERE tn.note_uid = ?
        """, (note_uid,)).fetchone()
        return TreeNodeInfo(dict(row)) if row else None
    
    def get_children(self, treenode_id: int, page_id: int = None) -> List[TreeNodeInfo]:
        if page_id is not None:
            rows = self.conn.execute("""
                SELECT tn.id, tn.page_id, tn.parent_id, tn."index", tn.note_uid,
                       tn.child_count, tn.expanded, tn.folder, n.caption, n.bg_color
                FROM treenodes tn LEFT JOIN notes n ON tn.note_uid = n.uid
                WHERE tn.parent_id = ? AND tn.page_id = ? ORDER BY tn."index"
            """, (treenode_id, page_id)).fetchall()
        else:
            rows = self.conn.execute("""
                SELECT tn.id, tn.page_id, tn.parent_id, tn."index", tn.note_uid,
                       tn.child_count, tn.expanded, tn.folder, n.caption, n.bg_color
                FROM treenodes tn LEFT JOIN notes n ON tn.note_uid = n.uid
                WHERE tn.parent_id = ? ORDER BY tn."index"
            """, (treenode_id,)).fetchall()
        return [TreeNodeInfo(dict(r)) for r in rows]
    
    def get_page_roots(self, page_id: int) -> List[TreeNodeInfo]:
        return self.get_children(-1, page_id)
    
    def get_subtree(self, treenode_id: int, page_id: int = None,
                    max_depth: int = 10, parse_content: bool = False) -> dict:
        tn = self.get_treenode(treenode_id, page_id)
        if not tn: return None
        note = None
        if parse_content and tn.note_uid:
            note = self.get_note(tn.note_uid, parse_content=True)
        children = []
        if max_depth > 0:
            for child in self.get_children(treenode_id, tn.page_id):
                ct = self.get_subtree(child.treenode_id, tn.page_id, max_depth-1, parse_content)
                if ct: children.append(ct)
        return {'treenode': tn, 'note': note, 'children': children}
    
    def get_path_to_root(self, treenode_id: int, page_id: int = None) -> List[TreeNodeInfo]:
        path, current_id, seen = [], treenode_id, set()
        if page_id is None:
            tn = self.get_treenode(treenode_id)
            if tn: page_id = tn.page_id
        while current_id != -1 and current_id not in seen:
            seen.add(current_id)
            tn = self.get_treenode(current_id, page_id)
            if not tn: break
            path.append(tn)
            current_id = tn.parent_id
        return list(reversed(path))
    
    # ── SEARCH ────────────────────────────────────────────────────────
    def search(self, query: str, page_id: int = None, limit: int = 50) -> List[NoteInfo]:
        try: return self._search_fts(query, page_id, limit)
        except: return self._search_like(query, page_id, limit)
    
    def _search_fts(self, query, page_id, limit):
        fts_query = ' '.join(f'"{w}"' for w in query.split() if w)
        sql = """SELECT n.uid, n.page_id, n.caption, n.bg_color, n.tags, n.flags,
                        n.date_created, n.last_modified, n.guid
                 FROM notes n WHERE n.uid IN (
                     SELECT docid FROM notes_fts WHERE notes_fts MATCH ?
                 )"""
        params = [fts_query]
        if page_id is not None:
            sql += " AND n.page_id = ?"; params.append(page_id)
        sql += f" LIMIT {limit}"
        return [NoteInfo(dict(r)) for r in self.conn.execute(sql, params).fetchall()]
    
    def _search_like(self, query, page_id, limit):
        sql = """SELECT uid, page_id, caption, bg_color, tags, flags, 
                        date_created, last_modified, guid FROM notes WHERE caption LIKE ?"""
        params = [f'%{query}%']
        if page_id is not None:
            sql += " AND page_id = ?"; params.append(page_id)
        sql += f" ORDER BY last_modified DESC LIMIT {limit}"
        return [NoteInfo(dict(r)) for r in self.conn.execute(sql, params).fetchall()]
    
    def search_by_color(self, bg_color, page_id=None, limit=100):
        sql = "SELECT uid, page_id, caption, bg_color, tags, flags, date_created, last_modified, guid FROM notes WHERE bg_color = ?"
        params = [bg_color]
        if page_id: sql += " AND page_id = ?"; params.append(page_id)
        sql += f" ORDER BY last_modified DESC LIMIT {limit}"
        return [NoteInfo(dict(r)) for r in self.conn.execute(sql, params).fetchall()]
    
    def search_by_semantic_color(self, color_code, page_id=None, limit=100):
        hex_map = {'p':'FF99CC','b':'CCFFFF','g':'CCFFCC','y':'FFFF99','o':'FFCC99','u':'CC99FF'}
        return self.search_by_color(hex_map.get(color_code, color_code), page_id, limit)

    # ── HIGHLIGHTED CONTENT ───────────────────────────────────────────
    def get_highlighted_lines(self, page_id=None, colors=None, min_salience=0.0, limit=500):
        if colors is None: colors = {'g','b','p','u'}
        sql = "SELECT n.uid, n.page_id, n.caption, n.bg_color, n.date_created, c.data FROM notes n JOIN contents c ON n.uid = c.id WHERE c.data IS NOT NULL"
        params = []
        if page_id: sql += " AND n.page_id = ?"; params.append(page_id)
        sql += f" LIMIT {limit}"
        results = []
        for row in self.conn.execute(sql, params):
            parsed = self._parse_blob(row['data'])
            if not parsed: continue
            sal = self._compute_salience(parsed.color_stats, parsed.total_chars)
            if sal < min_salience: continue
            for span in parsed.spans:
                if not span.highlight or span.highlight not in colors: continue
                text = span.text.strip()
                if len(text) < 3: continue
                results.append({'uid': row['uid'], 'caption': row['caption'] or '', 'color': span.highlight,
                    'bold': span.bold, 'text': text, 'salience': round(sal,1),
                    'bg_color': bg_color_to_semantic(row['bg_color']), 'page_id': row['page_id']})
        cw = {'u':5,'p':4,'b':3,'g':2,'y':1,'o':0.5}
        results.sort(key=lambda r: (-r['salience'], -cw.get(r['color'],0), -int(r['bold'])))
        return results

    def get_standout_nodes(self, page_id=28, limit=200):
        rows = self.conn.execute("""
            SELECT tn.id as treenode_id, tn.parent_id, tn."index", n.uid, n.caption, n.bg_color, n.date_created, c.packed_size
            FROM treenodes tn JOIN notes n ON tn.note_uid = n.uid LEFT JOIN contents c ON n.uid = c.id
            WHERE tn.page_id = ? AND n.bg_color IS NOT NULL AND n.bg_color != '' AND (c.packed_size IS NULL OR c.packed_size < 2000)
            ORDER BY tn.parent_id, tn."index" LIMIT ?
        """, (page_id, limit)).fetchall()
        return [{'treenode_id': r['treenode_id'], 'parent_id': r['parent_id'], 'uid': r['uid'],
                 'caption': r['caption'] or '', 'bg_color': r['bg_color'],
                 'bg_semantic': bg_color_to_semantic(r['bg_color']), 'size': r['packed_size'] or 0} for r in rows]

    def get_topic_notes(self, topic, page_id=None, include_content=True, limit=30):
        notes = self.search(topic, page_id, limit)
        results = []
        for ni in notes:
            tn = self.get_treenode_by_uid(ni.uid)
            path_str = ''
            if tn:
                path = self.get_path_to_root(tn.treenode_id, tn.page_id)
                path_str = ' > '.join(t.caption[:40] for t in path)
            result = {'uid': ni.uid, 'caption': ni.caption, 'bg_color': ni.bg_semantic, 'path': path_str, 'page_id': ni.page_id}
            if include_content:
                full = self.get_note(ni.uid, parse_content=True)
                if full and full.parsed:
                    result.update({'internal_format': full.internal_format[:2000], 'plain_text': full.plain_text[:1000],
                                   'highlight_ratio': full.highlight_ratio, 'color_stats': full.color_stats})
            results.append(result)
        return results

    def build_topic_context(self, topic, max_tokens=4000):
        notes = self.get_topic_notes(topic, include_content=True, limit=20)
        if not notes: return f"No notes found about '{topic}'."
        parts = [f"## Context: {topic}\nFound {len(notes)} related notes.\n"]
        cc = 0
        for n in notes:
            if cc > max_tokens*4: break
            s = f"\n### {n['caption']}"
            if n.get('bg_color'): s += f" [{n['bg_color']}]"
            if n.get('path'): s += f"\n_Path: {n['path']}_"
            if n.get('internal_format'): s += f"\n{n['internal_format']}"
            elif n.get('plain_text'): s += f"\n{n['plain_text'][:500]}"
            parts.append(s); cc += len(s)
        return '\n'.join(parts)

    def build_essence_summary(self, page_id=28, top_n=100):
        lines = self.get_highlighted_lines(page_id=page_id, colors={'u','p','b','g'}, limit=2000)
        if not lines: return "No highlighted content found."
        by_color = {'u':[],'p':[],'b':[],'g':[]}
        for l in lines[:top_n*3]:
            c = l['color']
            if c in by_color: by_color[c].append(l)
        parts = ["# Essence of the Archive\n"]
        labels = {'u':'Purple — Standout','p':'Pink — Exceptional','b':'Blue — Excellent','g':'Green — Good'}
        for c in ['u','p','b','g']:
            items = by_color[c][:top_n//4]
            if not items: continue
            parts.append(f"\n## {labels[c]} ({len(items)} items)\n")
            for i in items:
                bm = '**' if i['bold'] else ''
                bg = f" [{i['bg_color']}]" if i['bg_color'] else ''
                parts.append(f"- {bm}{i['text'][:200]}{bm}  _(from: {i['caption'][:50]}{bg})_")
        return '\n'.join(parts)

    # ══════════════════════════════════════════════════════════════════
    # WRITE OPERATIONS
    # ══════════════════════════════════════════════════════════════════
    
    def _check_writable(self):
        if self.readonly:
            raise PermissionError("Database opened in readonly mode")

    def _touch_main(self):
        try: self.conn.execute("UPDATE main SET last_write = datetime('now')")
        except: pass

    def _update_fts_content(self, uid, caption='', tags='', keywords='', data=''):
        """Safely update FTS by writing directly to the content table."""
        try:
            existing = self.conn.execute("SELECT docid FROM notes_fts_content WHERE docid = ?", (uid,)).fetchone()
            if existing:
                self.conn.execute("UPDATE notes_fts_content SET c0caption=?, c1tags=?, c2keywords=?, c3data=? WHERE docid=?",
                                  (caption, tags, keywords, data, uid))
            else:
                self.conn.execute("INSERT INTO notes_fts_content (docid, c0caption, c1tags, c2keywords, c3data) VALUES (?,?,?,?,?)",
                                  (uid, caption, tags, keywords, data))
        except Exception as e:
            print(f"Warning: FTS content update failed for uid={uid}: {e}")

    def _delete_fts_content(self, uid):
        try: self.conn.execute("DELETE FROM notes_fts_content WHERE docid = ?", (uid,))
        except: pass

    def rebuild_fts(self):
        """Rebuild the FTS3 inverted index from notes_fts_content.

        _update_fts_content() writes new/changed text straight into
        notes_fts_content to avoid INSERT/DELETE on the notes_fts virtual
        table itself (which corrupts its segment B-tree — see module
        docstring). That keeps the raw content current but never touches
        the actual searchable index (notes_fts_segments/notes_fts_segdir),
        so MATCH queries silently miss anything created or edited since
        the last rebuild. This is the fix: FTS3's 'rebuild' special
        command regenerates the index from the content table in one pass.
        """
        self._check_writable()
        self.conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
        self.conn.commit()

    def _reindex_siblings(self, parent_id, page_id):
        siblings = self.conn.execute('SELECT id FROM treenodes WHERE parent_id=? AND page_id=? ORDER BY "index"', (parent_id, page_id)).fetchall()
        for i, row in enumerate(siblings):
            self.conn.execute('UPDATE treenodes SET "index"=? WHERE id=? AND page_id=?', (i, row['id'], page_id))

    def _update_child_count(self, parent_treenode_id, page_id):
        if parent_treenode_id == -1: return
        count = self.conn.execute("SELECT COUNT(*) FROM treenodes WHERE parent_id=? AND page_id=?", (parent_treenode_id, page_id)).fetchone()[0]
        self.conn.execute("UPDATE treenodes SET child_count=? WHERE id=? AND page_id=?", (count, parent_treenode_id, page_id))

    # ── UPDATE ────────────────────────────────────────────────────────
    def update_note_caption(self, uid, new_caption):
        self._check_writable()
        now = delphi_now()
        cursor = self.conn.execute("UPDATE notes SET caption=?, last_modified=? WHERE uid=?", (new_caption, now, uid))
        if cursor.rowcount > 0:
            note = self.get_note(uid, parse_content=True)
            self._update_fts_content(uid, caption=new_caption, data=note.plain_text if note else '')
            self._touch_main()
        self.conn.commit()
        if cursor.rowcount > 0:
            self.rebuild_fts()
        return cursor.rowcount > 0

    def update_note_bg_color(self, uid, hex_color):
        self._check_writable()
        cursor = self.conn.execute("UPDATE notes SET bg_color=?, last_modified=? WHERE uid=?", (hex_color, delphi_now(), uid))
        if cursor.rowcount > 0: self._touch_main()
        self.conn.commit()
        return cursor.rowcount > 0

    def update_note_content_rtf(self, uid, new_rtf):
        self._check_writable()
        now = delphi_now()
        rtf_bytes = new_rtf.encode('cp1252', errors='replace')
        cursor = self.conn.execute("UPDATE contents SET data=?, size=?, packed_size=? WHERE id=?",
                                    (rtf_bytes, len(rtf_bytes), len(rtf_bytes), uid))
        self.conn.execute("UPDATE notes SET last_modified=? WHERE uid=?", (now, uid))
        if cursor.rowcount > 0:
            parsed = self.parser.parse(new_rtf)
            cap_row = self.conn.execute("SELECT caption FROM notes WHERE uid=?", (uid,)).fetchone()
            self._update_fts_content(uid, caption=cap_row[0] if cap_row else '', data=parsed.plain_text if parsed else '')
            self._touch_main()
        self.conn.commit()
        if cursor.rowcount > 0:
            self.rebuild_fts()
        return cursor.rowcount > 0

    def update_note_content_plain(self, uid, plain_text, highlight=None, bold=False):
        return self.update_note_content_rtf(uid, self._plain_to_rtf(plain_text, highlight, bold))

    def append_to_note(self, uid, text, highlight=None, bold=False):
        self._check_writable()
        current_rtf = self.get_note_raw_rtf(uid)
        if not current_rtf: return False
        if highlight: current_rtf = self._ensure_color_in_table(current_rtf, highlight)
        snippet = r'\par '
        if bold: snippet += r'\b '
        if highlight:
            hl = self._find_highlight_index(highlight, current_rtf)
            if hl: snippet += hl
        snippet += text.replace('\\','\\\\').replace('{','\\{').replace('}','\\}')
        if highlight: snippet += r'\highlight0 '
        if bold: snippet += r'\b0 '
        s = current_rtf.rstrip()
        new_rtf = (s[:-1] + snippet + '}') if s.endswith('}') else (current_rtf + snippet)
        return self.update_note_content_rtf(uid, new_rtf)

    # ── CREATE ────────────────────────────────────────────────────────
    def create_note(self, page_id, parent_treenode_id=-1, position=-1, caption='',
                    plain_text='', rtf_content=None, bg_color='', highlight=None, bold=False):
        """Create a note by CLONING all fields from an existing real note."""
        self._check_writable()
        page = self.conn.execute("SELECT id FROM pages WHERE id=?", (page_id,)).fetchone()
        if not page: raise ValueError(f"Page {page_id} does not exist")
        
        ref = self._get_reference_note()
        if not ref: raise RuntimeError("No existing notes to clone from")
        ref_content = self.conn.execute("SELECT * FROM contents WHERE id=?", (ref['uid'],)).fetchone()
        
        uid = self._next_note_uid()
        tn_id = self._next_treenode_id(page_id)
        guid = make_guid()
        now = delphi_now()
        
        if position == -1:
            row = self.conn.execute('SELECT MAX("index") FROM treenodes WHERE parent_id=? AND page_id=?', (parent_treenode_id, page_id)).fetchone()
            position = (row[0]+1) if row[0] is not None else 0
        else:
            self.conn.execute('UPDATE treenodes SET "index"="index"+1 WHERE parent_id=? AND page_id=? AND "index">=?', (parent_treenode_id, page_id, position))
        
        if rtf_content: rtf = rtf_content
        elif plain_text: rtf = self._plain_to_rtf(plain_text, highlight, bold)
        else: rtf = self._plain_to_rtf('')
        rtf_bytes = rtf.encode('cp1252', errors='replace')
        
        try:
            # CLONE all columns from reference note, override only what we need
            col_names = list(ref.keys())
            new_note = dict(ref)
            new_note['uid'] = uid
            new_note['page_id'] = page_id
            new_note['caption'] = caption
            new_note['url'] = ''
            new_note['icon_index'] = -1
            new_note['date_created'] = now
            new_note['last_modified'] = now
            new_note['last_accessed'] = now
            new_note['access_count'] = 1
            new_note['bg_color'] = bg_color
            new_note['guid'] = guid
            # Everything else (flags, protected, keywords, ct, font, tags, 
            # en_*, folder, pnote_uid, done_date, due_date, properties, 
            # last_edit, etc.) stays EXACTLY as the reference note has it
            
            placeholders = ', '.join(['?'] * len(col_names))
            col_str = ', '.join(f'"{c}"' for c in col_names)
            values = [new_note[c] for c in col_names]
            self.conn.execute(f"INSERT INTO notes ({col_str}) VALUES ({placeholders})", values)
            
            # Contents — clone flags from reference
            content_props = "startpos=0\nbgcolor=clNone\n"
            self.conn.execute("INSERT INTO contents (id, properties, data, flags, size, packed_size) VALUES (?,?,?,?,?,?)",
                              (uid, content_props, rtf_bytes, ref_content['flags'] if ref_content else '2000000000',
                               len(rtf_bytes), len(rtf_bytes)))
            
            # Treenode
            self.conn.execute('INSERT INTO treenodes (id, page_id, parent_id, "index", note_uid, child_count, expanded, checked, folder, pnote_uid) VALUES (?,?,?,?,?,0,0,0,0,-1)',
                              (tn_id, page_id, parent_treenode_id, position, uid))
            
            self._update_child_count(parent_treenode_id, page_id)
            parsed = self.parser.parse(rtf)
            self._update_fts_content(uid, caption=caption, data=parsed.plain_text if parsed else '')
            self._touch_main()
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(f"Failed to create note: {e}") from e
        self.rebuild_fts()
        return uid

    # ── DELETE ────────────────────────────────────────────────────────
    def delete_note(self, uid, recursive=False):
        self._check_writable()
        tn = self.get_treenode_by_uid(uid)
        if not tn: return False
        page_id, parent_id = tn.page_id, tn.parent_id
        if recursive:
            self._delete_subtree(tn.treenode_id, page_id)
        else:
            for child in self.get_children(tn.treenode_id, page_id):
                self.conn.execute('UPDATE treenodes SET parent_id=? WHERE id=? AND page_id=?', (parent_id, child.treenode_id, page_id))
            self._delete_single(uid, tn.treenode_id, page_id)
        self._update_child_count(parent_id, page_id)
        self._reindex_siblings(parent_id, page_id)
        self._touch_main()
        self.conn.commit()
        self.rebuild_fts()
        return True

    def _delete_single(self, uid, tn_id, page_id):
        self.conn.execute("DELETE FROM treenodes WHERE id=? AND page_id=?", (tn_id, page_id))
        self.conn.execute("DELETE FROM contents WHERE id=?", (uid,))
        self.conn.execute("DELETE FROM notes WHERE uid=?", (uid,))
        self._delete_fts_content(uid)

    def _delete_subtree(self, tn_id, page_id):
        for child in self.get_children(tn_id, page_id):
            self._delete_subtree(child.treenode_id, page_id)
        row = self.conn.execute("SELECT note_uid FROM treenodes WHERE id=? AND page_id=?", (tn_id, page_id)).fetchone()
        if row: self._delete_single(row['note_uid'], tn_id, page_id)

    # ── MOVE / REORDER ────────────────────────────────────────────────
    def move_note(self, uid, new_parent_tn_id, new_page_id=None, position=-1):
        self._check_writable()
        tn = self.get_treenode_by_uid(uid)
        if not tn: return False
        old_parent, old_page = tn.parent_id, tn.page_id
        new_page_id = new_page_id or old_page
        now = delphi_now()
        if position == -1:
            row = self.conn.execute('SELECT MAX("index") FROM treenodes WHERE parent_id=? AND page_id=?', (new_parent_tn_id, new_page_id)).fetchone()
            position = (row[0]+1) if row[0] is not None else 0
        else:
            self.conn.execute('UPDATE treenodes SET "index"="index"+1 WHERE parent_id=? AND page_id=? AND "index">=?', (new_parent_tn_id, new_page_id, position))
        if old_page != new_page_id:
            new_tn_id = self._next_treenode_id(new_page_id)
            self.conn.execute('UPDATE treenodes SET id=?, page_id=?, parent_id=?, "index"=? WHERE id=? AND page_id=?',
                              (new_tn_id, new_page_id, new_parent_tn_id, position, tn.treenode_id, old_page))
            self.conn.execute("UPDATE notes SET page_id=?, last_modified=? WHERE uid=?", (new_page_id, now, uid))
        else:
            self.conn.execute('UPDATE treenodes SET parent_id=?, "index"=? WHERE id=? AND page_id=?',
                              (new_parent_tn_id, position, tn.treenode_id, old_page))
            self.conn.execute("UPDATE notes SET last_modified=? WHERE uid=?", (now, uid))
        self._update_child_count(old_parent, old_page)
        self._reindex_siblings(old_parent, old_page)
        self._update_child_count(new_parent_tn_id, new_page_id)
        self._touch_main()
        self.conn.commit()
        return True

    def reorder_note(self, uid, new_position):
        self._check_writable()
        tn = self.get_treenode_by_uid(uid)
        if not tn: return False
        if tn.index == new_position: return True
        page_id, parent_id = tn.page_id, tn.parent_id
        self.conn.execute('UPDATE treenodes SET "index"="index"-1 WHERE parent_id=? AND page_id=? AND "index">?', (parent_id, page_id, tn.index))
        self.conn.execute('UPDATE treenodes SET "index"="index"+1 WHERE parent_id=? AND page_id=? AND "index">=?', (parent_id, page_id, new_position))
        self.conn.execute('UPDATE treenodes SET "index"=? WHERE id=? AND page_id=?', (new_position, tn.treenode_id, page_id))
        self._touch_main()
        self.conn.commit()
        return True

    # ── RTF helpers ───────────────────────────────────────────────────
    def _plain_to_rtf(self, text, highlight=None, bold=False):
        escaped = text.replace('\\','\\\\').replace('{','\\{').replace('}','\\}').replace('\n', r'\par ')
        content = ''
        if bold: content += r'\b '
        if highlight and highlight in HIGHLIGHT_INDEX: content += f'\\highlight{HIGHLIGHT_INDEX[highlight]} '
        content += escaped
        if highlight: content += r'\highlight0 '
        if bold: content += r'\b0 '
        return MINIMAL_RTF_TEMPLATE.replace('{TEXT}', content)

    def _ensure_color_in_table(self, rtf, color_code):
        rgb = COLOR_RGB.get(color_code)
        if not rgb or self._find_highlight_index(color_code, rtf): return rtf
        entry = f'\\red{rgb[0]}\\green{rgb[1]}\\blue{rgb[2]};'
        m = re.search(r'(\{\\colortbl\s*;?)(.*?)(\})', rtf, re.DOTALL)
        if m: return rtf[:m.start()] + m.group(1) + m.group(2) + entry + m.group(3) + rtf[m.end():]
        return rtf  # no colortbl found, return as-is

    def _find_highlight_index(self, color_code, rtf):
        target = COLOR_RGB.get(color_code)
        if not target: return ''
        m = re.search(r'\{\\colortbl\s*;?(.*?)\}', rtf, re.DOTALL)
        if not m: return ''
        for i, entry in enumerate(m.group(1).split(';')):
            rm = re.search(r'\\red(\d+)', entry); gm = re.search(r'\\green(\d+)', entry); bm = re.search(r'\\blue(\d+)', entry)
            if rm and gm and bm and (int(rm.group(1)), int(gm.group(1)), int(bm.group(1))) == target:
                return f'\\highlight{i+1} '
        return ''

    # ── Safety ────────────────────────────────────────────────────────
    def verify_integrity(self):
        r = {}
        r['notes'] = self.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        r['treenodes'] = self.conn.execute("SELECT COUNT(*) FROM treenodes").fetchone()[0]
        r['contents'] = self.conn.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
        r['counts_match'] = r['notes'] == r['treenodes'] == r['contents']
        r['orphan_notes'] = self.conn.execute("SELECT COUNT(*) FROM notes n LEFT JOIN treenodes tn ON tn.note_uid=n.uid WHERE tn.id IS NULL").fetchone()[0]
        r['orphan_contents'] = self.conn.execute("SELECT COUNT(*) FROM contents c LEFT JOIN notes n ON n.uid=c.id WHERE n.uid IS NULL").fetchone()[0]
        r['orphan_treenodes'] = self.conn.execute("SELECT COUNT(*) FROM treenodes tn LEFT JOIN notes n ON n.uid=tn.note_uid WHERE n.uid IS NULL").fetchone()[0]
        r['broken_parents'] = self.conn.execute("SELECT COUNT(*) FROM treenodes t1 WHERE t1.parent_id != -1 AND NOT EXISTS (SELECT 1 FROM treenodes t2 WHERE t2.id=t1.parent_id AND t2.page_id=t1.page_id)").fetchone()[0]
        r['wrong_child_counts'] = self.conn.execute("SELECT COUNT(*) FROM treenodes t1 WHERE t1.child_count != (SELECT COUNT(*) FROM treenodes t2 WHERE t2.parent_id=t1.id AND t2.page_id=t1.page_id)").fetchone()[0]
        try: r['fts_content_entries'] = self.conn.execute("SELECT COUNT(*) FROM notes_fts_content").fetchone()[0]
        except: r['fts_content_entries'] = 'error'
        r['sqlite_ok'] = self.conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
        return r

    def backup(self, backup_path=None):
        if backup_path is None:
            backup_path = str(self.path.parent / f"{self.path.stem}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{self.path.suffix}")
        try: self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except: pass
        shutil.copy2(str(self.path), backup_path)
        return backup_path

    def dump_note_raw(self, uid):
        """Dump ALL raw fields for debugging."""
        import json
        result = {}
        for tbl, col, val in [('notes','uid',uid), ('contents','id',uid)]:
            row = self.conn.execute(f"SELECT * FROM {tbl} WHERE {col}=?", (val,)).fetchone()
            if row: result[tbl] = {k: repr(row[k])[:200] for k in row.keys()}
        row = self.conn.execute("SELECT * FROM treenodes WHERE note_uid=?", (uid,)).fetchone()
        if row: result['treenodes'] = {k: repr(row[k]) for k in row.keys()}
        row = self.conn.execute("SELECT * FROM notes_fts_content WHERE docid=?", (uid,)).fetchone()
        if row: result['fts_content'] = {k: repr(row[k])[:100] for k in row.keys()}
        return result

    # ── Internal ──────────────────────────────────────────────────────
    def _decompress_blob(self, blob):
        try: data = zlib.decompress(blob)
        except zlib.error: data = blob
        for enc in ['utf-8','cp1252','latin-1']:
            try: return data.decode(enc)
            except UnicodeDecodeError: continue
        return data.decode('latin-1', errors='replace')

    def _parse_blob(self, blob):
        try: return self.parser.parse_compressed(blob)
        except: return None

    def _compute_salience(self, color_stats, total_chars):
        if total_chars == 0: return 0.0
        w = {'u':5,'p':4,'b':3,'g':2,'g2':2,'y':1,'o':0.5}
        return min(100, sum(w.get(c,0)*v for c,v in color_stats.items()) / (5.0*total_chars) * 100)


# ── CLI ───────────────────────────────────────────────────────────────
def main():
    import argparse
    p = argparse.ArgumentParser(description='RightNote CRUD v2 (fixed)')
    p.add_argument('rnt_file'); p.add_argument('command', choices=['info','search','verify','backup','note','tree','dump','create','delete','update-caption','update-content','append','move','reorder','topic','highlights','standouts','essence'])
    p.add_argument('--query','-q'); p.add_argument('--uid',type=int); p.add_argument('--treenode','-t',type=int)
    p.add_argument('--page','-p',type=int,default=28); p.add_argument('--parent',type=int,default=-1)
    p.add_argument('--position',type=int,default=-1); p.add_argument('--caption'); p.add_argument('--text')
    p.add_argument('--color'); p.add_argument('--bg'); p.add_argument('--bold',action='store_true')
    p.add_argument('--recursive',action='store_true'); p.add_argument('--limit','-n',type=int,default=30)
    p.add_argument('--colors',default='g,b,p,u')
    args = p.parse_args()
    write_cmds = {'create','delete','update-caption','update-content','append','move','reorder','backup'}
    db = RightNoteDB(args.rnt_file, readonly=args.command not in write_cmds)
    try:
        if args.command == 'info':
            for pg in db.get_pages():
                cnt = db.conn.execute("SELECT COUNT(*) FROM notes WHERE page_id=?", (pg['id'],)).fetchone()[0]
                print(f"  Page {pg['id']:3d}: {pg['caption']:<25s} ({cnt} notes)")
        elif args.command == 'verify':
            for k,v in db.verify_integrity().items(): print(f"  {k}: {v}")
        elif args.command == 'dump':
            if not args.uid: print("--uid required"); return
            import json; print(json.dumps(db.dump_note_raw(args.uid), indent=2))
        elif args.command == 'create':
            if not args.caption: print("--caption required"); return
            uid = db.create_note(page_id=args.page, parent_treenode_id=args.parent, position=args.position, caption=args.caption, plain_text=args.text or '', bg_color=args.bg or '', highlight=args.color, bold=args.bold)
            print(f"Created uid={uid}")
        elif args.command == 'delete':
            if not args.uid: print("--uid required"); return
            print(f"{'Deleted' if db.delete_note(args.uid, recursive=args.recursive) else 'Failed'}")
        elif args.command == 'search':
            if not args.query: print("--query required"); return
            for r in db.search(args.query, args.page, args.limit): print(f"  uid={r.uid:>6d} {r.caption[:70]}")
        elif args.command == 'note':
            if not args.uid: print("--uid required"); return
            n = db.get_note(args.uid, parse_content=True)
            if n: print(f"Caption: {n.caption}\nPage: {n.page_id}\nChars: {len(n.plain_text)}\n\n{n.internal_format[:2000]}")
            else: print("Not found")
        elif args.command == 'tree':
            tid = args.treenode or -1
            for c in (db.get_page_roots(args.page) if tid==-1 else db.get_children(tid, args.page)):
                bg = f" [{bg_color_to_semantic(c.bg_color)}]" if c.bg_color else ""
                print(f"  tn={c.treenode_id:>6d} [{c.index:3d}]{bg} {c.caption[:60]}")
        elif args.command == 'update-caption':
            print(f"{'OK' if db.update_note_caption(args.uid, args.caption) else 'Failed'}")
        elif args.command == 'update-content':
            print(f"{'OK' if db.update_note_content_plain(args.uid, args.text, args.color, args.bold) else 'Failed'}")
        elif args.command == 'append':
            print(f"{'OK' if db.append_to_note(args.uid, args.text, args.color, args.bold) else 'Failed'}")
        elif args.command == 'backup': print(f"Backup: {db.backup()}")
        elif args.command == 'topic': print(db.build_topic_context(args.query))
        elif args.command == 'highlights':
            for l in db.get_highlighted_lines(args.page, set(args.colors.split(',')), limit=args.limit*10)[:args.limit]:
                print(f"  [{l['color']}] {l['text'][:80]}")
        elif args.command == 'standouts':
            for n in db.get_standout_nodes(args.page, args.limit): print(f"  [{n['bg_semantic']}] {n['caption'][:70]}")
        elif args.command == 'essence': print(db.build_essence_summary(args.page, args.limit))
        elif args.command == 'move': print(f"{'OK' if db.move_note(args.uid, args.parent, args.page, args.position) else 'Failed'}")
        elif args.command == 'reorder': print(f"{'OK' if db.reorder_note(args.uid, args.position) else 'Failed'}")
    finally:
        db.close()

if __name__ == '__main__':
    main()