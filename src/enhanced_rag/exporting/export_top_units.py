#!/usr/bin/env python3
"""
export_top_units_v3.py — Tiered export of high-value knowledge units for LLM analysis.

v3: Tiered budget system with granular control over which colors fill remaining space.
    Purple + Pink always included first, then Blue, then Green — each filling
    available budget in priority order.

Usage:
    python export_top_units_v3.py Operation-Copy_shadow.db --stats-only

    # === Presets (easy mode) ===
    python export_top_units_v3.py Operation-Copy_shadow.db --preset compact     # purple+pink only
    python export_top_units_v3.py Operation-Copy_shadow.db --preset standard    # u+p+b in 180K
    python export_top_units_v3.py Operation-Copy_shadow.db --preset full        # u+p+b+g in 600K (splits)
    python export_top_units_v3.py Operation-Copy_shadow.db --preset max         # u+p+b+g, no budget cap

    # === Manual control ===
    python export_top_units_v3.py Operation-Copy_shadow.db --colors u p                 # just purple+pink
    python export_top_units_v3.py Operation-Copy_shadow.db --colors u p b               # + all blue
    python export_top_units_v3.py Operation-Copy_shadow.db --colors u p b g             # + all green
    python export_top_units_v3.py Operation-Copy_shadow.db --budget 180000              # u+p guaranteed, b fills rest
    python export_top_units_v3.py Operation-Copy_shadow.db --budget 400000              # u+p+b guaranteed, g fills rest
    python export_top_units_v3.py Operation-Copy_shadow.db --budget 600000 --split 180000  # big export, split for pasting
    python export_top_units_v3.py Operation-Copy_shadow.db --green-pct 50               # u+p+b + 50% of green
    python export_top_units_v3.py Operation-Copy_shadow.db --green-pct 25 --budget 300000  # combined

    # === Output control ===
    python export_top_units_v3.py Operation-Copy_shadow.db --preset standard -o my_export.txt
    python export_top_units_v3.py Operation-Copy_shadow.db --preset full --split 180000
"""

import sqlite3
import argparse
import sys
import os
import re
import random
from collections import defaultdict, OrderedDict

# ── Color definitions ──────────────────────────────────────────

COLOR_NAMES = {
    "u": "PUR",    # weight 5 — standout / rare peak
    "p": "PNK",    # weight 4 — exceptional
    "b": "BLU",    # weight 3 — excellent / high-salience
    "g": "GRN",    # weight 2 — good / validated
    "g2": "DGR",
    "y": "YLW",    # weight 1 — noteworthy / provisional
    "o": "ORA",    # weight 0.5 — corrective / needs revision
}

COLOR_FULL = {
    "u": "PURPLE (5)", "p": "PINK (4)", "b": "BLUE (3)",
    "g": "GREEN (2)", "g2": "DARK-GREEN", "y": "YELLOW (1)", "o": "ORANGE (0.5)",
}

# Priority order: higher priority colors are always included first
COLOR_TIERS = [
    (["u", "p"], "purple+pink"),   # Tier 1: always included
    (["b"],      "blue"),          # Tier 2: fills next
    (["g"],      "green"),         # Tier 3: fills remaining
]

# ── Label filtering ────────────────────────────────────────────

def is_organizational_label(text, source_type):
    """Check if a unit is just a label/heading rather than substantive content."""
    text = text.strip()
    # Caption-only units under 50 chars with no sentence structure
    if source_type == 'caption' and len(text) < 50 and '.' not in text and '"' not in text:
        return True
    # Very short content that looks like a heading
    if len(text) < 15 and not any(c in text for c in '.!?"'):
        return True
    return False


# ── Data extraction ────────────────────────────────────────────

def get_branch_key(tree_path, depth=1):
    """Get the top N levels of the tree path as a grouping key."""
    if not tree_path:
        return "(root)"
    parts = tree_path.split(" > ")
    return " > ".join(parts[:depth])


def fetch_units(db, page_id, colors, min_chars=15, skip_labels=True):
    """
    Pull units from DB, return as list of dicts (not yet grouped).
    """
    color_placeholders = ",".join(["?"] * len(colors))
    
    query = f"""
        SELECT 
            note_uid, caption, tree_path, 
            color, effective_weight, text, is_bold,
            line_index, source_type
        FROM knowledge_units
        WHERE page_id = ?
          AND color IN ({color_placeholders})
          AND is_trivial = 0
          AND is_separator = 0
          AND LENGTH(text) >= ?
        ORDER BY note_uid, line_index
    """
    
    params = [page_id] + list(colors) + [min_chars]
    rows = db.execute(query, params).fetchall()
    
    units = []
    skipped = 0
    for r in rows:
        text = r[5].strip()
        source_type = r[8]
        if skip_labels and is_organizational_label(text, source_type):
            skipped += 1
            continue
        units.append({
            "note_uid": r[0],
            "caption": r[1] or "",
            "tree_path": r[2] or "",
            "color": r[3],
            "weight": r[4],
            "text": text,
            "bold": r[6],
        })
    
    return units, skipped


def group_into_notes(units):
    """Group a flat list of units into an OrderedDict keyed by note_uid."""
    notes = OrderedDict()
    for u in units:
        uid = u["note_uid"]
        if uid not in notes:
            notes[uid] = {
                "caption": u["caption"],
                "tree_path": u["tree_path"],
                "lines": [],
            }
        notes[uid]["lines"].append({
            "color": u["color"],
            "weight": u["weight"],
            "text": u["text"],
            "bold": u["bold"],
        })
    return notes


def estimate_note_size(note):
    """Estimate character size of a note when formatted."""
    size = 80  # header overhead
    for ln in note["lines"]:
        size += len(ln["text"]) + 14  # tag + newline overhead
    return size


# ── Formatting ─────────────────────────────────────────────────

def format_compact(notes, colors_label, include_note_ids=True):
    """Compact format: group by top-level branch, minimal separators."""
    lines = []
    total_units = sum(len(n["lines"]) for n in notes.values())
    
    lines.append(f"# {total_units} highlighted units from {len(notes)} notes")
    lines.append(f"# Colors included: {colors_label}")
    lines.append(f"# Tags: [PUR]=purple(5) [PNK]=pink(4) [BLU]=blue(3) [GRN]=green(2)  *=bold")
    lines.append("")
    
    # Group notes by top-level branch
    branches = OrderedDict()
    for uid, note in notes.items():
        branch = get_branch_key(note["tree_path"], depth=1)
        if branch not in branches:
            branches[branch] = []
        branches[branch].append((uid, note))
    
    for branch, branch_notes in branches.items():
        lines.append(f"\n== {branch} ==")
        
        for uid, note in branch_notes:
            tree = note["tree_path"]
            caption = note["caption"]
            parts = tree.split(" > ")
            sub_path = " > ".join(parts[1:]) if len(parts) > 1 else ""
            
            id_str = f"[{uid}] " if include_note_ids else ""
            if sub_path and sub_path != caption[:len(sub_path)]:
                lines.append(f"\n  {id_str}{sub_path}")
                if caption and caption not in sub_path:
                    lines.append(f"  # {caption}")
            elif caption:
                lines.append(f"\n  {id_str}{caption}")
            
            for ln in note["lines"]:
                tag = COLOR_NAMES.get(ln["color"], "?")
                bold = "*" if ln["bold"] else ""
                text = ln["text"]
                if text == caption and len(note["lines"]) == 1:
                    continue
                lines.append(f"    [{tag}{bold}] {text}")
    
    return "\n".join(lines)


# ── Tiered budget logic ───────────────────────────────────────

def build_tiered_export(all_units, budget_chars, green_pct=100):
    """
    Build export with tiered priority:
      Tier 1 (purple+pink): always fully included
      Tier 2 (blue): fills next available budget
      Tier 3 (green): fills remaining budget (with optional percentage sampling)
    
    Returns: (selected_units, stats_dict)
    """
    # Separate units by tier
    tier1 = [u for u in all_units if u["color"] in ("u", "p")]
    tier2 = [u for u in all_units if u["color"] == "b"]
    tier3 = [u for u in all_units if u["color"] == "g"]
    
    # Apply green percentage sampling if < 100
    if green_pct < 100 and tier3:
        # Sample by note to keep context together (don't split notes)
        green_notes = OrderedDict()
        for u in tier3:
            uid = u["note_uid"]
            if uid not in green_notes:
                green_notes[uid] = []
            green_notes[uid].append(u)
        
        note_uids = list(green_notes.keys())
        n_keep = max(1, int(len(note_uids) * green_pct / 100))
        
        # Sample highest-weight notes preferentially
        note_weights = {}
        for uid, units in green_notes.items():
            note_weights[uid] = max(u["weight"] for u in units)
        sorted_uids = sorted(note_uids, key=lambda uid: note_weights[uid], reverse=True)
        kept_uids = set(sorted_uids[:n_keep])
        
        tier3 = [u for u in tier3 if u["note_uid"] in kept_uids]
    
    # Build up selected units tier by tier within budget
    selected = []
    tier_stats = {}
    remaining_budget = budget_chars if budget_chars > 0 else float('inf')
    
    for i, (tier_units, tier_name) in enumerate([(tier1, "purple+pink"), (tier2, "blue"), (tier3, "green")]):
        if not tier_units:
            tier_stats[tier_name] = {"units": 0, "chars": 0, "notes": 0, "full": True}
            continue
        
        # Group into notes to estimate sizes
        tier_notes = group_into_notes(tier_units)
        
        added_units = 0
        added_chars = 0
        added_notes = 0
        full = True
        
        for uid, note in tier_notes.items():
            note_size = estimate_note_size(note)
            if remaining_budget != float('inf') and added_chars + note_size > remaining_budget and added_notes > 0:
                full = False
                break
            # Add all units from this note
            for ln in note["lines"]:
                selected.append({
                    "note_uid": uid,
                    "caption": note["caption"],
                    "tree_path": note["tree_path"],
                    **ln,
                })
                added_units += 1
            added_chars += note_size
            added_notes += 1
        
        remaining_budget -= added_chars
        tier_stats[tier_name] = {
            "units": added_units,
            "chars": added_chars,
            "notes": added_notes,
            "total_notes": len(tier_notes),
            "full": full,
        }
    
    return selected, tier_stats


# ── Splitting ──────────────────────────────────────────────────

def split_by_branch(text, max_chars):
    """Split at branch boundaries (== lines)."""
    sections = text.split("\n== ")
    header = sections[0]
    
    chunks = []
    current = header
    
    for section in sections[1:]:
        section_text = "\n== " + section
        if len(current) + len(section_text) > max_chars and len(current) > len(header) + 100:
            chunks.append(current)
            current = header + "\n\n(continued)\n"
        current += section_text
    
    if current.strip():
        chunks.append(current)
    
    return chunks


# ── Presets ────────────────────────────────────────────────────

PRESETS = {
    "compact": {
        "desc": "Purple + Pink only (~10K tokens)",
        "colors": ["u", "p"],
        "budget": 0,
        "green_pct": 100,
    },
    "standard": {
        "desc": "Purple + Pink + Blue within 180K chars (~45K tokens)",
        "colors": ["u", "p", "b"],
        "budget": 180000,
        "green_pct": 100,
    },
    "rich": {
        "desc": "Purple + Pink + Blue + 50% Green within 400K chars (~100K tokens)",
        "colors": ["u", "p", "b", "g"],
        "budget": 400000,
        "green_pct": 50,
    },
    "full": {
        "desc": "All top colors, split into pasteable chunks",
        "colors": ["u", "p", "b", "g"],
        "budget": 600000,
        "split": 180000,
        "green_pct": 100,
    },
    "max": {
        "desc": "Everything purple through green, no budget limit",
        "colors": ["u", "p", "b", "g"],
        "budget": 0,
        "green_pct": 100,
    },
}


# ── Stats ──────────────────────────────────────────────────────

def print_stats(db, page_id, selected_colors):
    """Print color distribution stats."""
    print(f"\n── Stats for page_id={page_id} ──")
    total_chars = 0
    for c in ["u", "p", "b", "g", "y", "o"]:
        cur = db.execute("""
            SELECT COUNT(*), SUM(LENGTH(text)) 
            FROM knowledge_units 
            WHERE page_id = ? AND color = ? AND is_trivial = 0 AND is_separator = 0
        """, (page_id, c))
        row = cur.fetchone()
        name = COLOR_FULL.get(c, c).ljust(14)
        count = row[0] or 0
        chars = row[1] or 0
        total_chars += chars
        sel = " ← SELECTED" if c in selected_colors else ""
        print(f"  {name}: {count:>6} units, {chars:>10,} chars (~{chars//4:,} tok){sel}")
    
    cur = db.execute("""
        SELECT COUNT(*), SUM(LENGTH(text)) FROM knowledge_units 
        WHERE page_id = ? AND color IS NULL AND is_trivial = 0 AND is_separator = 0
    """, (page_id,))
    row = cur.fetchone()
    none_chars = row[1] or 0
    print(f"  {'(none)'.ljust(14)}: {(row[0] or 0):>6} units, {none_chars:>10,} chars")
    print(f"  {'TOTAL'.ljust(14)}: {' ':>6}       {total_chars + none_chars:>10,} chars")


# ── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Export high-value KUs with tiered budget control (v3)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Presets:
  compact    Purple + Pink only (~10K tokens)
  standard   P+P+Blue in 180K chars (~45K tokens, single paste)
  rich       P+P+Blue + 50%% Green in 400K chars (~100K tokens)
  full       P+P+B+G in 600K, auto-split for pasting
  max        Everything purple→green, no cap
        """)
    
    parser.add_argument("shadow_db", help="Path to shadow database")
    parser.add_argument("--page-id", type=int, default=28, help="Page ID (default: 28)")
    
    # Preset or manual
    parser.add_argument("--preset", choices=PRESETS.keys(),
                        help="Use a preset configuration")
    parser.add_argument("--colors", nargs="+", default=None,
                        help="Color codes to include (default: u p b)")
    parser.add_argument("--budget", type=int, default=None,
                        help="Target char budget (0 = no limit)")
    parser.add_argument("--green-pct", type=int, default=None,
                        help="Percentage of green units to include (1-100, samples highest-weight first)")
    
    # Output control
    parser.add_argument("--min-chars", type=int, default=15,
                        help="Minimum text length (default: 15)")
    parser.add_argument("--split", type=int, default=None,
                        help="Split into chunks of ~N chars")
    parser.add_argument("--output", "-o", default=None, help="Output file path")
    parser.add_argument("--stats-only", action="store_true", help="Just show stats")
    parser.add_argument("--keep-labels", action="store_true",
                        help="Include organizational labels")
    
    args = parser.parse_args()
    
    # Apply preset defaults, then override with explicit args
    if args.preset:
        preset = PRESETS[args.preset]
        if args.colors is None:
            args.colors = preset["colors"]
        if args.budget is None:
            args.budget = preset["budget"]
        if args.green_pct is None:
            args.green_pct = preset.get("green_pct", 100)
        if args.split is None:
            args.split = preset.get("split", 0)
        print(f"  Preset '{args.preset}': {preset['desc']}")
    
    # Final defaults
    if args.colors is None:
        args.colors = ["u", "p", "b"]
    if args.budget is None:
        args.budget = 0
    if args.green_pct is None:
        args.green_pct = 100
    if args.split is None:
        args.split = 0
    
    if not os.path.exists(args.shadow_db):
        print(f"ERROR: {args.shadow_db} not found"); sys.exit(1)
    
    db = sqlite3.connect(args.shadow_db)
    page_id = args.page_id
    
    print_stats(db, page_id, args.colors)
    
    if args.stats_only:
        db.close(); return
    
    # ── Fetch all requested units ──
    color_str = ", ".join(COLOR_FULL.get(c, c) for c in args.colors)
    print(f"\n── Fetching {color_str} ──")
    
    all_units, skipped = fetch_units(
        db, page_id, args.colors,
        min_chars=args.min_chars,
        skip_labels=not args.keep_labels,
    )
    db.close()
    
    if not all_units:
        print("  No units found!"); return
    
    print(f"  {len(all_units)} content units fetched ({skipped} labels filtered)")
    
    # ── Apply tiered budget ──
    green_pct = args.green_pct if "g" in args.colors else 100
    if green_pct < 100:
        print(f"  Green sampling: {green_pct}% (highest-weight notes first)")
    
    selected_units, tier_stats = build_tiered_export(
        all_units, args.budget, green_pct=green_pct
    )
    
    # Report tier breakdown
    print(f"\n── Tier breakdown ──")
    for tier_name, stats in tier_stats.items():
        if stats["units"] == 0 and stats.get("total_notes", 0) == 0:
            continue
        total_n = stats.get("total_notes", stats["notes"])
        pct = f"{stats['notes']}/{total_n}" if total_n > 0 else "0"
        status = "✓ all" if stats["full"] else f"⚠ partial ({pct} notes)"
        print(f"  {tier_name.ljust(14)}: {stats['units']:>6} units, {stats['chars']:>10,} chars — {status}")
    
    total_selected = sum(s["units"] for s in tier_stats.values())
    total_chars_est = sum(s["chars"] for s in tier_stats.values())
    print(f"  {'TOTAL'.ljust(14)}: {total_selected:>6} units, {total_chars_est:>10,} chars (~{total_chars_est//4:,} tok)")
    
    # ── Format ──
    notes = group_into_notes(selected_units)
    colors_label = ", ".join(
        f"{COLOR_FULL.get(c, c)}" + (f" [{green_pct}%]" if c == "g" and green_pct < 100 else "")
        for c in args.colors if any(s["units"] > 0 for tn, s in tier_stats.items() 
                                      if c in dict([(cc, tn) for tier_colors, tn in COLOR_TIERS for cc in tier_colors]).get(c, ""))
    )
    # Simpler label fallback
    if not colors_label:
        colors_label = color_str
    
    output_text = format_compact(notes, color_str, include_note_ids=True)
    total_chars = len(output_text)
    
    print(f"\n── Output: {total_chars:,} chars (~{total_chars//4:,} tokens) ──")
    
    # ── Output path ──
    if args.output:
        out_base = args.output
    else:
        color_codes = "_".join(args.colors)
        suffix_parts = []
        if args.budget:
            suffix_parts.append(f"budget{args.budget//1000}k")
        if green_pct < 100:
            suffix_parts.append(f"g{green_pct}pct")
        if args.preset:
            suffix_parts.append(args.preset)
        suffix = "_" + "_".join(suffix_parts) if suffix_parts else ""
        out_base = f"export_v3_page{page_id}_{color_codes}{suffix}.txt"
    
    # ── Write (with optional split) ──
    if args.split and total_chars > args.split:
        chunks = split_by_branch(output_text, args.split)
        print(f"  Split into {len(chunks)} parts")
        for i, chunk in enumerate(chunks):
            path = out_base.replace(".txt", f"_part{i+1}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(chunk)
            print(f"    Part {i+1}: {path} ({len(chunk):,} chars, ~{len(chunk)//4:,} tok)")
    else:
        with open(out_base, "w", encoding="utf-8") as f:
            f.write(output_text)
        print(f"  Written to: {out_base}")
        
        if total_chars > 200000:
            print(f"\n  ⚠ Large output. Consider:")
            print(f"    --split 180000    (split for pasting)")
            print(f"    --budget {min(total_chars, 180000)}  (cap total size)")
            if "g" in args.colors and green_pct == 100:
                print(f"    --green-pct 50    (sample 50% of green)")


if __name__ == "__main__":
    main()
