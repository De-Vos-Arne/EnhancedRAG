"""
Tiered-budget bulk export — ported from the standalone export_top_units_v3.py
script, adapted to corpus.db's schema (was written against an older shadow.db)
and to settings.SCOPE_PAGE_IDS.

The idea: dump a large, thematically-coherent slice of the archive for
pasting into a big-context model, respecting a priority order over colours
(purple/pink always in, blue fills what's left, green fills what's left
after that) and a character budget — rather than the line-level knowledge
units used for retrieval, which are precise but too atomised to read as a
document. Grouped by note and top-level tree branch instead.

Also exposed as an MCP tool (mcp_server.py) — the tiering/budgeting logic
here is exactly as useful to an agent deciding what to read as it is to a
human copying into a chat window.
"""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from pathlib import Path

from .. import settings
from ..core import colours as colours_mod
from .models import Generator

# Priority tiers, highest signal first. Each tier fills whatever budget the
# previous tiers left; g2 (secondary dark-green) rides along with green.
# "nb"/"n" are virtual codes (not real DB colour codes) handled specially
# in _fetch_units: text the author bolded without colouring it, and any
# remaining uncoloured text, respectively — for branches that don't have
# much (or any) highlighting but still have signal in what was bolded.
TIERS = [
    (["u", "p"], "purple+pink"),
    (["b"], "blue"),
    (["g", "g2"], "green"),
    (["y"], "yellow"),
    (["o"], "orange"),
    (["nb"], "bold (uncoloured)"),
    (["n"], "uncoloured"),
]

_scope_clause = (
    f"AND page_id IN ({','.join(str(p) for p in settings.SCOPE_PAGE_IDS)}) "
    if settings.SCOPE_PAGE_IDS else ""
)
BASE_WHERE = ("is_trivial = 0 AND is_separator = 0 AND is_duplicate = 0 "
             "AND length(trim(text)) > 12 " + _scope_clause)


def _is_organizational_label(text: str) -> bool:
    text = text.strip()
    if len(text) < 15 and not any(c in text for c in ".!?\""):
        return True
    return False


def resolve_subtree_note_uids(con: sqlite3.Connection, page_id: int,
                              treenode_ids: list[int]) -> list[int]:
    """Given checked treenode ids from the tree selector, expand each to
    its own note plus every descendant note, using tree_hierarchy's
    materialized path column (e.g. "28/1039/1040") for the prefix match —
    avoids walking parent_treenode_id chains by hand."""
    if not treenode_ids:
        return []
    placeholders = ",".join("?" * len(treenode_ids))
    paths = con.execute(
        f"SELECT path FROM tree_hierarchy WHERE page_id=? AND treenode_id IN ({placeholders})",
        [page_id, *treenode_ids]).fetchall()
    note_uids: set[int] = set()
    for (path,) in paths:
        if not path:
            continue
        rows = con.execute(
            "SELECT note_uid FROM tree_hierarchy WHERE page_id=? "
            "AND (path = ? OR path LIKE ?) AND note_uid IS NOT NULL",
            (page_id, path, path + "/%")).fetchall()
        note_uids.update(r[0] for r in rows)
    return list(note_uids)


def colour_stats(con: sqlite3.Connection) -> list[dict]:
    """Per-colour unit/char counts across the in-scope corpus, plus two
    virtual rows for bold-but-uncoloured and any-uncoloured text so the
    export UI can offer them as extra tiers."""
    out = []
    for c in colours_mod.COLOURS:
        row = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(LENGTH(text)),0) FROM knowledge_units "
            f"WHERE {BASE_WHERE} AND color = ?", (c.code,)).fetchone()
        out.append({"code": c.code, "name": c.name, "weight": c.weight,
                    "units": row[0], "chars": row[1]})
    row = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(LENGTH(text)),0) FROM knowledge_units "
        f"WHERE {BASE_WHERE} AND color IS NULL AND is_bold = 1").fetchone()
    out.append({"code": "nb", "name": "bold (uncoloured)", "weight": 0,
                "units": row[0], "chars": row[1]})
    row = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(LENGTH(text)),0) FROM knowledge_units "
        f"WHERE {BASE_WHERE} AND color IS NULL").fetchone()
    out.append({"code": "n", "name": "uncoloured", "weight": 0,
                "units": row[0], "chars": row[1]})
    return out


def _fetch_units(con, colors, min_chars=15, skip_labels=True, note_uids=None):
    real_colors = [c for c in colors if c not in ("n", "nb")]
    want_bold_uncoloured = "nb" in colors
    want_any_uncoloured = "n" in colors

    color_clause = []
    if real_colors:
        placeholders = ",".join("?" * len(real_colors))
        color_clause.append(f"color IN ({placeholders})")
    if want_any_uncoloured:
        color_clause.append("color IS NULL")
    elif want_bold_uncoloured:
        color_clause.append("(color IS NULL AND is_bold = 1)")
    if not color_clause:
        return []
    where_color = "(" + " OR ".join(color_clause) + ")"

    note_clause, note_params = "", []
    if note_uids:
        note_clause = f"AND note_uid IN ({','.join('?' * len(note_uids))}) "
        note_params = list(note_uids)
    rows = con.execute(
        f"SELECT note_uid, caption, tree_path, color, effective_weight, "
        f"text, is_bold, date_created FROM knowledge_units "
        f"WHERE {BASE_WHERE} AND {where_color} {note_clause}"
        f"AND length(text) >= ? ORDER BY note_uid, line_index",
        [*real_colors, *note_params, min_chars]).fetchall()
    units = []
    for uid, caption, tree_path, color, weight, text, bold, date_created in rows:
        text = text.strip()
        if skip_labels and _is_organizational_label(text):
            continue
        units.append({"note_uid": uid, "caption": caption or "",
                      "tree_path": tree_path or "", "color": color,
                      "weight": weight, "text": text, "bold": bold,
                      "date_created": date_created or ""})
    return units


def _group_into_notes(units):
    notes = OrderedDict()
    for u in units:
        uid = u["note_uid"]
        if uid not in notes:
            notes[uid] = {"caption": u["caption"], "tree_path": u["tree_path"],
                          "lines": [], "date_created": u["date_created"]}
        notes[uid]["lines"].append(u)
        if u["date_created"] > notes[uid]["date_created"]:
            notes[uid]["date_created"] = u["date_created"]
    return notes


def _estimate_note_size(note):
    return 80 + sum(len(ln["text"]) + 14 for ln in note["lines"])


def build_tiered_export(con: sqlite3.Connection, enabled_colors: set[str],
                        budget_chars: int = 0, min_chars: int = 15,
                        note_uids: list[int] | None = None,
                        sort_recency: bool = False,
                        adaptive_fallback: bool = False) -> dict:
    """
    enabled_colors: which colour codes are eligible at all (unchecked
    colours are excluded outright, not just deprioritised). "nb"/"n" are
    the virtual bold-uncoloured / any-uncoloured tiers.
    budget_chars: 0 = no cap, else fill tiers in priority order until spent.
    note_uids: restrict to these notes only, if given (tree selector).
    sort_recency: within each tier, fill newest notes first instead of
    note_uid order — so a tight budget captures what was added most
    recently rather than whatever happens to sort first.
    adaptive_fallback: if note_uids is set and nothing in it matches any
    enabled colour (a branch the author never got round to highlighting),
    fall back to bold-uncoloured then any-uncoloured text from that same
    selection instead of returning nothing.
    """
    tiers = [(codes, name) for codes, name in TIERS
             if any(c in enabled_colors for c in codes)]
    remaining = budget_chars if budget_chars > 0 else float("inf")

    selected, tier_report = [], []
    for codes, name in tiers:
        tier_codes = [c for c in codes if c in enabled_colors]
        units = _fetch_units(con, tier_codes, min_chars=min_chars, note_uids=note_uids)
        tier_notes = _group_into_notes(units)
        if sort_recency:
            tier_notes = OrderedDict(
                sorted(tier_notes.items(), key=lambda kv: kv[1]["date_created"], reverse=True))

        added_units = added_chars = added_notes = 0
        full = True
        for uid, note in tier_notes.items():
            size = _estimate_note_size(note)
            if remaining != float("inf") and added_chars + size > remaining and added_notes > 0:
                full = False
                break
            selected.extend({**ln, "note_uid": uid, "caption": note["caption"],
                             "tree_path": note["tree_path"]} for ln in note["lines"])
            added_units += len(note["lines"])
            added_chars += size
            added_notes += 1
        remaining -= added_chars
        tier_report.append({"name": name, "units": added_units, "chars": added_chars,
                            "notes": added_notes, "total_notes": len(tier_notes), "full": full})

    used_fallback = False
    if not selected and note_uids and adaptive_fallback:
        used_fallback = True
        for codes, name in [(["nb"], "bold (uncoloured) — fallback"),
                            (["n"], "uncoloured — fallback")]:
            units = _fetch_units(con, codes, min_chars=min_chars, note_uids=note_uids)
            if not units:
                tier_report.append({"name": name, "units": 0, "chars": 0,
                                    "notes": 0, "total_notes": 0, "full": True})
                continue
            tier_notes = _group_into_notes(units)
            added_units = added_chars = added_notes = 0
            full = True
            budget = budget_chars if budget_chars > 0 else float("inf")
            for uid, note in tier_notes.items():
                size = _estimate_note_size(note)
                if budget != float("inf") and added_chars + size > budget and added_notes > 0:
                    full = False
                    break
                selected.extend({**ln, "note_uid": uid, "caption": note["caption"],
                                 "tree_path": note["tree_path"]} for ln in note["lines"])
                added_units += len(note["lines"])
                added_chars += size
                added_notes += 1
            tier_report.append({"name": name, "units": added_units, "chars": added_chars,
                                "notes": added_notes, "total_notes": len(tier_notes), "full": full})
            if selected:
                break

    return {"units": selected, "tiers": tier_report, "used_fallback": used_fallback}


def _line_tag(ln) -> str:
    code = colours_mod.BY_CODE.get(ln["color"])
    tag = code.tag if code else ("BLD" if ln["bold"] else "?")
    return f"[{tag}{'*' if ln['bold'] and code else ''}]"


def format_export(units: list[dict], sort_recency: bool = False) -> str:
    """Outline-style export: each tree-path segment is printed once, at
    the point it first appears, indented by depth — not repeated on every
    single note the way a flat per-note breadcrumb would.
    """
    notes = _group_into_notes(units)
    total_units = sum(len(n["lines"]) for n in notes.values())
    lines = [f"# {total_units} highlighted units from {len(notes)} notes",
            f"# Tags: [PUR]=purple(5) [PNK]=pink(4) [BLU]=blue(3) [GRN]=green(2) "
            f"[YEL]=yellow(1) [ORN]=orange(0.5) [BLD]=bold/uncoloured  *=bold", ""]

    def head(uid, note, note_lines):
        head_tag = ""
        nl = list(note_lines)
        if nl and nl[0]["text"] == note["caption"]:
            head_tag = _line_tag(nl[0]) + " "
            nl = nl[1:]
        return head_tag, nl

    if sort_recency:
        # Flat, newest-first — recency is orthogonal to the tree, so a tab
        # outline would just repeat every branch header once per note.
        ordered = sorted(notes.items(), key=lambda kv: kv[1]["date_created"], reverse=True)
        for uid, note in ordered:
            head_tag, note_lines = head(uid, note, note["lines"])
            when = note["date_created"][:10] if note["date_created"] else "?"
            lines.append(f"\n[{uid}] {head_tag}{note['caption'] or '(untitled)'}"
                         f"  ({when} · {note['tree_path'] or '(root)'})")
            for ln in note_lines:
                lines.append(f"  {_line_tag(ln)} {ln['text']}")
        return "\n".join(lines)

    # Sort notes by their path so siblings land next to each other, then
    # walk the sorted list tracking which path segments are already open.
    ordered = sorted(notes.items(),
                     key=lambda kv: (kv[1]["tree_path"] or "").split(" > "))
    open_path: list[str] = []

    for uid, note in ordered:
        parts = (note["tree_path"] or "(root)").split(" > ")
        # Print only the segments that differ from what's already open.
        common = 0
        while (common < len(open_path) and common < len(parts)
               and open_path[common] == parts[common]):
            common += 1
        open_path = open_path[:common]
        for depth in range(common, len(parts)):
            indent = "\t" * depth
            lines.append(f"{indent}== {parts[depth]} ==")
            open_path.append(parts[depth])

        indent = "\t" * len(parts)
        head_tag, note_lines = head(uid, note, note["lines"])

        lines.append(f"\n{indent}[{uid}] {head_tag}{note['caption'] or '(untitled)'}")
        for ln in note_lines:
            lines.append(f"{indent}  {_line_tag(ln)} {ln['text']}")
    return "\n".join(lines)


# Optional front-matter blocks a caller can prepend to the export text —
# same wording as the clipboard tool's DEFAULT_PREAMBLE, so pasting from
# either route reads consistently.
LEGEND_PREAMBLE = (
    "The text below uses a personal colour-tagging system, marked with "
    "[TAG] tags — not markdown or code syntax. Each colour is a rough "
    "conceptual signal for how strongly I judged the marked content, not a "
    "strict rule:\n"
    "  Purple [PUR] — standout, rare peak (weight ~5)\n"
    "  Pink [PNK] — exceptional (weight ~4)\n"
    "  Blue [BLU] — excellent, high-salience (weight ~3)\n"
    "  Green [GRN] — good, validated (weight ~2)\n"
    "  Yellow [YEL] — noteworthy but provisional (weight ~1)\n"
    "  Orange [ORN] — flagged as needing correction or revision (weight ~0.5)\n"
    "  [BLD] — bold but otherwise uncoloured; still especially important\n"
    "Bold (*) marks something I considered especially important on top of "
    "whatever colour it carries.\n"
    "Orange and yellow generally mean I think that part needs work — but I "
    "sometimes use any colour, orange/yellow included, to mark contrast or "
    "a counterpoint instead of a quality judgement. Use your own reading of "
    "the content when a tag's intent seems ambiguous. Unmarked text carries "
    "no signal either way."
)

ARCHIVE_INTRO = (
    "This is an export of a personal knowledge archive — a second brain "
    "built up by hand over several years, holding notes, drafts, "
    "philosophical writing, and reference material. It is a working "
    "draft, not a finished or edited document: parts are settled "
    "positions, parts are half-formed, and parts are deliberately "
    "provisional. The colour highlighting (see the tag legend if included "
    "below) is the author's own running judgment of what matters, applied "
    "inconsistently over time rather than by a fixed rulebook — treat it "
    "as a strong hint, not ground truth."
)


def export(con: sqlite3.Connection, colors: list[str], budget_chars: int = 0,
          min_chars: int = 15, note_uids: list[int] | None = None,
          sort_recency: bool = False, adaptive_fallback: bool = False,
          include_legend: bool = False, include_intro: bool = False) -> dict:
    """One-shot: build + format + report. `colors` is the list of colour
    codes to include (order doesn't matter, TIERS controls priority).
    `note_uids`, if given, restricts the export to those notes/subtrees
    (resolved by the caller — e.g. from the tree checkbox selector).
    `include_legend`/`include_intro` prepend the standard framing blocks
    (see LEGEND_PREAMBLE / ARCHIVE_INTRO above) to the returned text."""
    built = build_tiered_export(con, set(colors), budget_chars, min_chars,
                                note_uids=note_uids, sort_recency=sort_recency,
                                adaptive_fallback=adaptive_fallback)
    text = format_export(built["units"], sort_recency=sort_recency)
    front = []
    if include_intro:
        front.append(ARCHIVE_INTRO)
    if include_legend:
        front.append(LEGEND_PREAMBLE)
    if front:
        text = "\n\n".join(front) + "\n\n" + text
    return {"text": text, "tiers": built["tiers"], "chars": len(text),
            "est_tokens": len(text) // 4, "unit_count": len(built["units"]),
            "used_fallback": built["used_fallback"]}


def preview_note_uids(con: sqlite3.Connection, note_uids: list[int],
                      max_chars: int = 4000) -> dict:
    """Cheap read-only peek at what a set of notes (e.g. one tree branch,
    before checking it in) actually contains — every colour plus
    uncoloured, small char cap. Used by the "preview before adding" eye
    icon in the tree selector."""
    built = build_tiered_export(
        con, {"u", "p", "b", "g", "g2", "y", "o", "nb", "n"},
        budget_chars=max_chars, note_uids=note_uids)
    text = format_export(built["units"])
    return {"text": text, "unit_count": len(built["units"]),
            "truncated": len(text) >= max_chars}


# ── branch summarization ──────────────────────────────────────────────
#
# A short LLM-written summary of what a branch/selection actually contains
# — themes, arguments, recurring quotes — rather than the raw tiered dump.
# Uses the local uncensored HauhauCS model by default so refusals on the
# archive's more charged material aren't a routine problem. Primed with
# the archive's own stated purpose/intent (below) so the summary is
# oriented around what the author actually cares about instead of reading
# as a generic outline — this is the same "north star" text the author
# keeps at the top of the archive itself (0. Meta/Core Intent).

CORE_INTENT_CONTEXT = (
    "Background — the archive's own stated purpose, for orientation only "
    "(do not summarize this part, it is not the material to summarize):\n"
    "This archive is the second brain of its founder, built to develop and "
    "record a personal worldview ('Aetherianism') and to further a long-"
    "term project (a planned autonomous society). Its stated core intent: "
    "maximize long-term strategic-evolutionary advantage — capability "
    "growth, cohesion, learning velocity, optionality, adaptive dominance, "
    "and 'elevation of life' (increasing meaningful capacity: spiritual, "
    "biological, intellectual). The archive holds notes, book/manual "
    "drafts, philosophical essays, rhetoric, and training curricula. "
    "Material ranges from settled positions to half-formed drafts — the "
    "colour highlighting marks the author's own judgment of importance, "
    "not correctness or completeness."
)

SUMMARY_SYSTEM_PROMPT = (
    "You are producing a compact reference summary of one branch of a "
    "personal knowledge archive, for the archive's own author to use "
    "later — not for a general reader. Cover: the main themes and "
    "arguments present, any recurring rhetorical style or phrasing worth "
    "reusing, and standout quotes (verbatim, short). Be concrete and "
    "specific to what's actually in the text below, not generic. If the "
    "material is thin or repetitive, say so briefly rather than padding. "
    "Plain prose, no meta-commentary about being an AI.\n\n" + CORE_INTENT_CONTEXT
)


def summarize_branch(con: sqlite3.Connection, colors: list[str],
                     note_uids: list[int] | None, generator_name: str = "lmstudio",
                     max_input_chars: int = 24000) -> dict:
    """Fetch the tiered content for a selection and ask a generator for a
    short summary of what's actually in it. Raises ModelError if the
    generator is unreachable — caller decides how to surface that."""
    built = build_tiered_export(con, set(colors), budget_chars=max_input_chars,
                                note_uids=note_uids, adaptive_fallback=True)
    if not built["units"]:
        return {"summary": "(no matching content in this selection)",
                "unit_count": 0}
    source_text = format_export(built["units"])

    gen = Generator(generator_name)
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": "Summarize this archive excerpt:\n\n" +
                                    source_text[:max_input_chars]},
    ]
    summary = gen.complete(messages)
    return {"summary": summary, "unit_count": len(built["units"]), "generator": gen.label}


# ── question-driven curation ────────────────────────────────────────────
#
# The "clever search" idea: given a question/topic, run retrieval (FTS +
# vector, whatever regimes are handed in) to find which NOTES are
# relevant, then export those notes' full tiered content — not just the
# single matched line — so a downstream model gets the whole context
# around each hit, not isolated fragments. Meant to be pasted into a big-
# context chat (or fed to an MCP-connected model) when writing about a
# specific topic.

def curate_for_question(service, query: str, regimes: list[str] | None = None,
                        colors: list[str] | None = None, budget_chars: int = 150000,
                        top_k: int = 40, include_legend: bool = False,
                        include_intro: bool = False) -> dict:
    """service: a RagService instance (for its retrieval + repo).
    regimes: regime keys to pool results from (default: a keyword + a
    vector regime, so lexical hits and semantic hits both contribute).
    colors: which colours to pull for each matched note once selected
    (default purple+pink+blue+green, since a matched note's supporting
    context matters even if only lightly highlighted)."""
    regimes = regimes or ["R0_baseline_fts", "R6_full_metadata"]
    colors = colors or ["u", "p", "b", "g", "g2"]
    note_uids: set[int] = set()
    per_regime = {}
    for key in regimes:
        reg = dict(settings.REGIMES[key])
        reg["top_k"] = top_k
        frags = service.retrieval.retrieve(query, reg)
        uids = {f.note_uid for f in frags}
        note_uids.update(uids)
        per_regime[key] = len(uids)

    if not note_uids:
        return {"text": "(no matches for this query)", "note_uids": [],
                "per_regime": per_regime, "chars": 0, "unit_count": 0}

    result = export(service.repo.con, colors, budget_chars=budget_chars,
                    note_uids=list(note_uids), adaptive_fallback=True,
                    include_legend=include_legend, include_intro=include_intro)
    result["note_uids"] = sorted(note_uids)
    result["per_regime"] = per_regime
    result["query"] = query
    return result
