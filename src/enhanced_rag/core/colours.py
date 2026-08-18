"""
The colour system — this IS the semantic layer.

Single source of truth. The parser, the retriever, the prompt builder, the
export tool and the UI all read from here, so changing a weight changes it
everywhere at once. Nothing else in the codebase should hardcode a colour
or a weight.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Colour:
    code: str        # parser code
    name: str        # human name
    hex: str         # RightNote's highlight hex
    weight: float    # base semantic weight
    tag: str         # export/prompt tag
    meaning: str


COLOURS = [
    Colour("u",  "Purple", "CC99FF", 5.0, "PUR", "Standout / rare peak"),
    Colour("p",  "Pink",   "FF99CC", 4.0, "PNK", "Exceptional"),
    Colour("b",  "Blue",   "CCFFFF", 3.0, "BLU", "Excellent / high-salience"),
    Colour("g",  "Green",  "CCFFCC", 2.0, "GRN", "Good / validated"),
    Colour("g2", "Green",  "99CC00", 2.0, "GRN", "Good / validated (secondary)"),
    Colour("y",  "Yellow", "FFFF99", 1.0, "YEL", "Noteworthy / provisional"),
    Colour("o",  "Orange", "FFCC99", 0.5, "ORN", "Corrective / needs revision"),
]

BY_CODE = {c.code: c for c in COLOURS}

WEIGHTS = {c.code: c.weight for c in COLOURS}
TAGS = {c.code: c.tag for c in COLOURS}

# Display hexes for the dark UI — the RightNote pastels are unreadable on
# a dark ground, so these are the same hues pushed to usable saturation.
DISPLAY_HEX = {"u": "#CC99FF", "p": "#FF99CC", "b": "#9EE8E8",
               "g": "#A8E6A8", "g2": "#A8E6A8", "y": "#F2E68A", "o": "#F2C48A"}

BOLD_BONUS = 0.5          # bold + colour is more important than colour alone
TREENODE_BONUS = 0.5      # the author explicitly marked the node itself
MAX_WEIGHT = 5.5          # purple + bold


def effective_weight(code: str | None, bold: bool = False,
                     is_treenode: bool = False) -> float:
    """The weight actually used for ranking."""
    w = WEIGHTS.get(code, 0.0)
    if not w:
        return 0.0
    if bold:
        w += BOLD_BONUS
    if is_treenode:
        w += TREENODE_BONUS
    return w


def tag_for(code: str | None, bold: bool = False) -> str:
    """`[BLU*]` style tag, or empty string for unmarked text."""
    if not code or code not in TAGS:
        return ""
    return f"[{TAGS[code]}{'*' if bold else ''}]"


# Break tokens produced by the RTF parser.
BREAK_TOKENS = {
    "[BR1]": "line break",
    "[BR2]": "block break",
    "[BR3]": "section break (resets section context)",
}


def legend(markdown: bool = False) -> str:
    """The explanation handed to a language model, or written into an export.

    Kept in one place so the model, the export tool and the thesis all
    describe the colour system in exactly the same words.
    """
    if markdown:
        lines = ["### The Colour System (this IS the semantic layer)",
                 "**In-note RTF highlighting:**"]
        for c in COLOURS:
            if c.code == "g2":
                continue
            lines.append(f"- **{c.name}** ({c.hex}) = weight {c.weight:g} — {c.meaning}")
        lines += [
            "",
            f"**Bold + colour = +{BOLD_BONUS} effective weight** "
            "(more important than colour alone).",
            f"**Treenode background colours** use the same semantic mapping, "
            f"+{TREENODE_BONUS} extra boost (the author explicitly marked the node).",
            "**Break tokens**: `[BR2]` = block break, `[BR3]` = section break "
            "(resets section context).",
            "**Separator nodes**: root-level nodes captioned like `========` "
            "are visual dividers, not content.",
        ]
        return "\n".join(lines)

    lines = ["Each fragment carries the author's own highlight colour, which "
             "encodes how important the author judged it. This is the semantic "
             "layer of the archive, applied by hand over several years:"]
    for c in COLOURS:
        if c.code == "g2":
            continue
        lines.append(f"  [{c.tag}] weight {c.weight:g} — {c.meaning}")
    lines += [
        f"An asterisk after the tag means bold, worth +{BOLD_BONUS} effective weight; "
        "bold plus colour is more important than colour alone.",
        f"A treenode background colour uses the same mapping with a further "
        f"+{TREENODE_BONUS}, because the author marked that whole node deliberately.",
        "[BR2] marks a block break and [BR3] a section break, which resets "
        "section context.",
        "Untagged fragments carry no author marking.",
        "Weigh higher-weight fragments more heavily, and treat [ORN] as content "
        "the author flagged as needing revision.",
    ]
    return "\n".join(lines)
