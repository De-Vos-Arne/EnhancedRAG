"""
Archive explorer — browse and edit the .rnt file in the browser.

The route bodies are the ones from the original rnt_server.py, moved into
a blueprint so the explorer and the retrieval bench can share one server.
All database work goes through core.rnt_crud; nothing here writes SQL.

Never INSERT or DELETE on the .rnt file's own FTS3 virtual tables — it
destroys the search segments. rnt_crud is the only sanctioned writer.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_from_directory

from ..core.rnt_crud import (RightNoteDB, NoteInfo, TreeNodeInfo,
                             bg_color_to_semantic, delphi_to_datetime)

bp = Blueprint("explorer", __name__)

DB_PATH = None
READONLY = False


def build_blueprint(archive_path):
    """Returns (blueprint, error_message_or_None)."""
    global DB_PATH, READONLY
    from .. import settings
    DB_PATH = str(archive_path)
    READONLY = settings.ARCHIVE_READONLY
    if not Path(DB_PATH).exists():
        return bp, (f"Archive not found at {DB_PATH}. Put your .rnt there, or "
                    f"set RAG_ARCHIVE. The retrieval bench still works without it.")
    try:
        RightNoteDB(DB_PATH, readonly=True).close()
    except Exception as e:
        return bp, f"Could not open {DB_PATH}: {e}"
    return bp, None


@bp.app_errorhandler(FileNotFoundError)
def _missing_archive(e):
    """Answer with a usable message instead of a stack trace when the .rnt
    isn't where the explorer expects it."""
    return jsonify({"error": str(e),
                    "hint": "Put your .rnt at data/PersonalArchive.rnt, or set "
                            "RAG_ARCHIVE. The retrieval bench works without it."}), 503


@bp.route("/explorer")
def explorer_page():
    from .. import settings
    return send_from_directory(settings.STATIC_DIR, "explorer.html")


def get_db(readonly=None) -> RightNoteDB:
    """Get a database connection. Each request gets its own connection."""
    ro = readonly if readonly is not None else READONLY
    return RightNoteDB(DB_PATH, readonly=ro)


def note_to_dict(note: NoteInfo, include_content: bool = True) -> dict:
    """Convert NoteInfo to JSON-safe dict."""
    d = {
        'uid': note.uid,
        'page_id': note.page_id,
        'caption': note.caption or '',
        'bg_color': note.bg_color or '',
        'bg_semantic': note.bg_semantic or '',
        'date_created': note.date_created.isoformat() if note.date_created else None,
        'last_modified': note.last_modified.isoformat() if note.last_modified else None,
        'guid': note.guid or '',
        'highlight_ratio': round(note.highlight_ratio, 4),
        'color_stats': note.color_stats,
    }
    if include_content:
        d['plain_text'] = note.plain_text
        d['internal_format'] = note.internal_format
    return d


def treenode_to_dict(tn: TreeNodeInfo) -> dict:
    """Convert TreeNodeInfo to JSON-safe dict."""
    return {
        'treenode_id': tn.treenode_id,
        'page_id': tn.page_id,
        'parent_id': tn.parent_id,
        'index': tn.index,
        'note_uid': tn.note_uid,
        'child_count': tn.child_count,
        'expanded': tn.expanded,
        'folder': tn.folder,
        'caption': tn.caption or '',
        'bg_color': tn.bg_color or '',
        'bg_semantic': bg_color_to_semantic(tn.bg_color) or '',
    }


# ══════════════════════════════════════════════════════════════════════
# READ ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@bp.route('/api/pages')
def api_pages():
    """List all pages with note counts."""
    with get_db(readonly=True) as db:
        pages = db.get_pages()
        for p in pages:
            count = db.conn.execute(
                "SELECT COUNT(*) FROM notes WHERE page_id = ?", (p['id'],)
            ).fetchone()[0]
            p['note_count'] = count
        return jsonify(pages)


@bp.route('/api/tree/<int:page_id>')
def api_tree_roots(page_id):
    """Get root treenodes for a page."""
    with get_db(readonly=True) as db:
        roots = db.get_page_roots(page_id)
        return jsonify([treenode_to_dict(r) for r in roots])


@bp.route('/api/tree/<int:page_id>/<int:tn_id>')
def api_tree_children(page_id, tn_id):
    """Get children of a treenode."""
    with get_db(readonly=True) as db:
        children = db.get_children(tn_id, page_id)
        return jsonify([treenode_to_dict(c) for c in children])


@bp.route('/api/note/<int:uid>')
def api_note(uid):
    """Get a note with parsed content."""
    parse = request.args.get('parse', 'true').lower() == 'true'
    with get_db(readonly=True) as db:
        note = db.get_note(uid, parse_content=parse)
        if not note:
            return jsonify({'error': 'Note not found'}), 404
        
        d = note_to_dict(note, include_content=parse)
        
        # Also include treenode info
        tn = db.get_treenode_by_uid(uid)
        if tn:
            d['treenode'] = treenode_to_dict(tn)
            path = db.get_path_to_root(tn.treenode_id, tn.page_id)
            d['path'] = [treenode_to_dict(p) for p in path]
            d['path_str'] = ' > '.join(p.caption[:40] for p in path)
        
        return jsonify(d)


@bp.route('/api/note/<int:uid>/rtf')
def api_note_rtf(uid):
    """Get raw RTF content."""
    with get_db(readonly=True) as db:
        rtf = db.get_note_raw_rtf(uid)
        if rtf is None:
            return jsonify({'error': 'Note not found'}), 404
        return Response(rtf, mimetype='text/rtf')


@bp.route('/api/search')
def api_search():
    """Full-text search."""
    query = request.args.get('q', '')
    page_id = request.args.get('page', type=int)
    limit = request.args.get('limit', 50, type=int)
    
    if not query:
        return jsonify({'error': 'Query required (q parameter)'}), 400
    
    with get_db(readonly=True) as db:
        results = db.search(query, page_id, limit)
        items = []
        for r in results:
            d = note_to_dict(r, include_content=False)
            # Get tree path
            tn = db.get_treenode_by_uid(r.uid)
            if tn:
                path = db.get_path_to_root(tn.treenode_id, tn.page_id)
                d['path_str'] = ' > '.join(p.caption[:40] for p in path)
                d['treenode_id'] = tn.treenode_id
            items.append(d)
        
        return jsonify({'query': query, 'count': len(items), 'results': items})


@bp.route('/api/highlights')
def api_highlights():
    """Get highlighted text spans."""
    page_id = request.args.get('page', type=int)
    colors = request.args.get('colors', 'g,b,p,u')
    limit = request.args.get('limit', 500, type=int)
    min_sal = request.args.get('min_salience', 0.0, type=float)
    
    color_set = set(colors.split(','))
    
    with get_db(readonly=True) as db:
        lines = db.get_highlighted_lines(page_id, color_set, min_sal, limit)
        return jsonify({'count': len(lines), 'lines': lines})


@bp.route('/api/standouts')
def api_standouts():
    """Get standout marker nodes."""
    page_id = request.args.get('page', 28, type=int)
    limit = request.args.get('limit', 200, type=int)
    
    with get_db(readonly=True) as db:
        nodes = db.get_standout_nodes(page_id, limit)
        return jsonify({'count': len(nodes), 'nodes': nodes})


@bp.route('/api/path/<int:uid>')
def api_path(uid):
    """Get tree path for a note."""
    with get_db(readonly=True) as db:
        tn = db.get_treenode_by_uid(uid)
        if not tn:
            return jsonify({'error': 'Note not found'}), 404
        path = db.get_path_to_root(tn.treenode_id, tn.page_id)
        return jsonify({
            'path': [treenode_to_dict(p) for p in path],
            'path_str': ' > '.join(p.caption[:40] for p in path),
        })


@bp.route('/api/integrity')
def api_integrity():
    """Run integrity check."""
    with get_db(readonly=True) as db:
        result = db.verify_integrity()
        return jsonify(result)


@bp.route('/api/subtree/<int:page_id>/<int:tn_id>')
def api_subtree(page_id, tn_id):
    """Get a full subtree (nested) up to max_depth."""
    max_depth = request.args.get('depth', 3, type=int)
    parse = request.args.get('parse', 'false').lower() == 'true'
    
    with get_db(readonly=True) as db:
        tree = db.get_subtree(tn_id, page_id, max_depth, parse)
        if not tree:
            return jsonify({'error': 'Treenode not found'}), 404
        
        def serialize_tree(node):
            result = {
                'treenode': treenode_to_dict(node['treenode']),
                'children': [serialize_tree(c) for c in node['children']],
            }
            if node.get('note'):
                result['note'] = note_to_dict(node['note'], include_content=parse)
            return result
        
        return jsonify(serialize_tree(tree))


# ══════════════════════════════════════════════════════════════════════
# WRITE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@bp.route('/api/note', methods=['POST'])
def api_create_note():
    """Create a new note."""
    if READONLY:
        return jsonify({'error': 'Database is in readonly mode'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400
    
    page_id = data.get('page_id')
    if not page_id:
        return jsonify({'error': 'page_id required'}), 400
    
    with get_db(readonly=False) as db:
        try:
            uid = db.create_note(
                page_id=page_id,
                parent_treenode_id=data.get('parent_treenode_id', -1),
                position=data.get('position', -1),
                caption=data.get('caption', ''),
                plain_text=data.get('plain_text', ''),
                rtf_content=data.get('rtf_content'),
                bg_color=data.get('bg_color', ''),
                highlight=data.get('highlight'),
                bold=data.get('bold', False),
            )
            
            note = db.get_note(uid, parse_content=False)
            tn = db.get_treenode_by_uid(uid)
            
            return jsonify({
                'uid': uid,
                'note': note_to_dict(note, include_content=False),
                'treenode': treenode_to_dict(tn) if tn else None,
            }), 201
        except Exception as e:
            return jsonify({'error': str(e)}), 500


@bp.route('/api/note/<int:uid>/caption', methods=['PUT'])
def api_update_caption(uid):
    """Update note caption."""
    if READONLY:
        return jsonify({'error': 'Readonly mode'}), 403
    
    data = request.get_json()
    caption = data.get('caption', '')
    
    with get_db(readonly=False) as db:
        success = db.update_note_caption(uid, caption)
        return jsonify({'success': success, 'caption': caption})


@bp.route('/api/note/<int:uid>/content', methods=['PUT'])
def api_update_content(uid):
    """Update note content (plain text, auto-generates RTF)."""
    if READONLY:
        return jsonify({'error': 'Readonly mode'}), 403
    
    data = request.get_json()
    text = data.get('text', '')
    highlight = data.get('highlight')
    bold = data.get('bold', False)
    
    with get_db(readonly=False) as db:
        success = db.update_note_content_plain(uid, text, highlight, bold)
        return jsonify({'success': success})


@bp.route('/api/note/<int:uid>/rtf', methods=['PUT'])
def api_update_rtf(uid):
    """Update note content (raw RTF)."""
    if READONLY:
        return jsonify({'error': 'Readonly mode'}), 403
    
    data = request.get_json()
    rtf = data.get('rtf', '')
    
    with get_db(readonly=False) as db:
        success = db.update_note_content_rtf(uid, rtf)
        return jsonify({'success': success})


@bp.route('/api/note/<int:uid>/append', methods=['PUT'])
def api_append(uid):
    """Append text to note."""
    if READONLY:
        return jsonify({'error': 'Readonly mode'}), 403
    
    data = request.get_json()
    text = data.get('text', '')
    highlight = data.get('highlight')
    bold = data.get('bold', False)
    
    with get_db(readonly=False) as db:
        success = db.append_to_note(uid, text, highlight, bold)
        return jsonify({'success': success})


@bp.route('/api/note/<int:uid>/bg_color', methods=['PUT'])
def api_update_bg_color(uid):
    """Update background color."""
    if READONLY:
        return jsonify({'error': 'Readonly mode'}), 403
    
    data = request.get_json()
    color = data.get('bg_color', '')
    
    with get_db(readonly=False) as db:
        success = db.update_note_bg_color(uid, color)
        return jsonify({'success': success})


@bp.route('/api/note/<int:uid>/move', methods=['PUT'])
def api_move(uid):
    """Move note to new parent."""
    if READONLY:
        return jsonify({'error': 'Readonly mode'}), 403
    
    data = request.get_json()
    new_parent = data.get('parent_treenode_id')
    if new_parent is None:
        return jsonify({'error': 'parent_treenode_id required'}), 400
    
    new_page = data.get('page_id')
    position = data.get('position', -1)
    
    with get_db(readonly=False) as db:
        success = db.move_note(uid, new_parent, new_page, position)
        return jsonify({'success': success})


@bp.route('/api/note/<int:uid>/reorder', methods=['PUT'])
def api_reorder(uid):
    """Reorder note among siblings."""
    if READONLY:
        return jsonify({'error': 'Readonly mode'}), 403
    
    data = request.get_json()
    position = data.get('position')
    if position is None:
        return jsonify({'error': 'position required'}), 400
    
    with get_db(readonly=False) as db:
        success = db.reorder_note(uid, position)
        return jsonify({'success': success})


@bp.route('/api/note/<int:uid>', methods=['DELETE'])
def api_delete(uid):
    """Delete a note."""
    if READONLY:
        return jsonify({'error': 'Readonly mode'}), 403
    
    recursive = request.args.get('recursive', 'false').lower() == 'true'
    
    with get_db(readonly=False) as db:
        success = db.delete_note(uid, recursive=recursive)
        return jsonify({'success': success})


@bp.route('/api/fts/rebuild', methods=['POST'])
def api_rebuild_fts():
    """Rebuild the FTS index."""
    if READONLY:
        return jsonify({'error': 'Readonly mode'}), 403
    
    with get_db(readonly=False) as db:
        db.rebuild_fts()
        return jsonify({'success': True})


@bp.route('/api/backup', methods=['POST'])
def api_backup():
    """Create a database backup."""
    with get_db(readonly=False) as db:
        path = db.backup()
        return jsonify({'backup_path': path})


# ══════════════════════════════════════════════════════════════════════
# STATIC FILE SERVING
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

