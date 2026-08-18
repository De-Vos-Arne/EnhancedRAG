
"""
RTF Parser for RightNote archives.
Extracts text with formatting metadata (highlight colors, bold, italic)
and converts to the internal bracket format [b][/b], [g][/g], etc.

Handles:
- RTF color tables and highlight commands
- Bold/italic detection
- Break detection (===, xxx, excessive whitespace)
- Produces both structured data and the internal bracket format
"""

import re
import zlib
import struct
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# ── Color mapping ──────────────────────────────────────────────────────
# RightNote highlight colors (from the RTF colortbl) mapped to semantic codes
# These are the RGB values from the bg_color field and RTF highlight colors

HIGHLIGHT_COLOR_MAP = {
    # (R, G, B) -> semantic code
    (255, 153, 204): "p",   # pink — FF99CC
    (204, 255, 255): "b",   # blue (cyan-ish) — CCFFFF  
    (204, 255, 204): "g",   # green — CCFFCC
    (255, 255, 255): None,  # white — no highlight
    (255, 255, 153): "y",   # yellow — FFFF99
    (255, 204, 153): "o",   # orange — FFCC99
    (204, 153, 255): "u",   # purple — CC99FF
    (153, 204, 0):   "g2",  # dark green — 99CC00 (secondary green)
    (255, 255, 0):   "y",   # bright yellow — FFFF00
    (255, 0, 0):     "o",   # red — treat as warning/orange category
    (0, 255, 0):     "g",   # bright green
    (255, 0, 255):   "u",   # magenta → purple category
    (255, 153, 0):   "o",   # bright orange — FF9900
    (255, 204, 0):   "y",   # gold-yellow — FFCC00
}

# Hex shorthand for bg_color field matching
HEX_COLOR_MAP = {
    "FF99CC": "p",
    "CCFFFF": "b",
    "CCFFCC": "g",
    "FFFF99": "y",
    "FFCC99": "o",
    "CC99FF": "u",
    "99CC00": "g2",
    "FFFF00": "y",
    "FF0000": "o",
    "00FF00": "g",
    "FF00FF": "u",
    "FF9900": "o",
    "FFCC00": "y",
    "339966": "g2",
    "808000": "y",
    "99CCFF": "b",
    "008000": "g2",
    "FF6600": "o",
}

# For display/reporting
COLOR_NAMES = {
    "p": "pink",
    "b": "blue", 
    "g": "green",
    "g2": "dark-green",
    "y": "yellow",
    "o": "orange",
    "u": "purple",
}


@dataclass
class TextSpan:
    """A contiguous span of text with uniform formatting."""
    text: str
    highlight: Optional[str] = None   # semantic color code: p, b, g, y, o, u, g2, or None
    bold: bool = False
    italic: bool = False
    highlight_rgb: Optional[tuple] = None  # raw (R,G,B) for analysis
    

@dataclass 
class ParsedNote:
    """A fully parsed note with spans and metadata."""
    spans: list = field(default_factory=list)        # list of TextSpan
    plain_text: str = ""                              # all text concatenated
    internal_format: str = ""                         # bracket-encoded text
    color_stats: dict = field(default_factory=dict)   # char counts per color
    total_chars: int = 0
    highlighted_chars: int = 0
    highlight_ratio: float = 0.0
    has_breaks: bool = False
    break_count: int = 0


class RTFParser:
    """
    Parses RTF content and extracts text with formatting metadata.
    
    Usage:
        parser = RTFParser()
        # From raw RTF string:
        result = parser.parse(rtf_string)
        # From zlib-compressed blob:
        result = parser.parse_compressed(blob)
    """
    
    def __init__(self):
        self.color_table = []  # list of (R, G, B) tuples
        self.reset()
    
    def reset(self):
        self.color_table = []
        self.spans = []
        self.current_text = []
        self.current_highlight = None
        self.current_highlight_rgb = None
        self.current_bold = False
        self.current_italic = False
        self.group_stack = []
        self._pending_surrogate = None
        self._uc_value = 1           # default: skip 1 fallback char after \u
        self._skip_after_unicode = 0  # chars remaining to skip
        self._skip_image_group = False  # set when \pict etc encountered
        
    def parse_compressed(self, blob: bytes) -> ParsedNote:
        """Parse a zlib-compressed RTF blob (from contents.data)."""
        try:
            rtf_bytes = zlib.decompress(blob)
        except zlib.error:
            # Maybe it's not compressed (user mentioned uncompressed option)
            rtf_bytes = blob
        
        # Try multiple encodings
        for encoding in ['utf-8', 'cp1252', 'latin-1']:
            try:
                rtf_string = rtf_bytes.decode(encoding)
                break
            except (UnicodeDecodeError, AttributeError):
                continue
        else:
            rtf_string = rtf_bytes.decode('latin-1', errors='replace')
        
        return self.parse(rtf_string)
    
    def parse(self, rtf: str) -> ParsedNote:
        """Parse an RTF string and extract formatted text spans."""
        self.reset()
        
        if not rtf or not rtf.strip().startswith('{\\rtf'):
            # Not RTF, treat as plain text
            return self._make_result_from_plain(rtf or "")
        
        # Extract color table first
        self._parse_color_table(rtf)
        
        # Now parse the document body
        self._parse_body(rtf)
        
        # Flush any remaining text
        self._flush_span()
        
        return self._build_result()
    
    def _parse_color_table(self, rtf: str):
        """Extract the color table from RTF header."""
        # Find {\colortbl ...}
        match = re.search(r'\{\\colortbl\s*;?(.*?)\}', rtf, re.DOTALL)
        if not match:
            return
        
        color_str = match.group(1)
        # Colors are separated by semicolons
        # Each color: \red255\green153\blue204;
        self.color_table = [(0, 0, 0)]  # index 0 = auto/default (black)
        
        for color_def in color_str.split(';'):
            color_def = color_def.strip()
            if not color_def:
                continue
            r = g = b = 0
            rm = re.search(r'\\red(\d+)', color_def)
            gm = re.search(r'\\green(\d+)', color_def)
            bm = re.search(r'\\blue(\d+)', color_def)
            if rm: r = int(rm.group(1))
            if gm: g = int(gm.group(1))
            if bm: b = int(bm.group(1))
            self.color_table.append((r, g, b))
    
    def _color_index_to_semantic(self, idx: int) -> tuple:
        """Convert a color table index to (semantic_code, rgb_tuple)."""
        if idx <= 0 or idx >= len(self.color_table):
            return (None, None)
        rgb = self.color_table[idx]
        semantic = HIGHLIGHT_COLOR_MAP.get(rgb, None)
        
        # If exact match fails, try nearest neighbor for close colors
        if semantic is None and rgb != (255, 255, 255) and rgb != (0, 0, 0):
            semantic = self._nearest_color(rgb)
        
        return (semantic, rgb)
    
    def _nearest_color(self, rgb: tuple) -> Optional[str]:
        """Find nearest semantic color for non-exact matches."""
        r, g, b = rgb
        min_dist = float('inf')
        best = None
        for ref_rgb, code in HIGHLIGHT_COLOR_MAP.items():
            if code is None:
                continue
            dr = r - ref_rgb[0]
            dg = g - ref_rgb[1]
            db = b - ref_rgb[2]
            dist = dr*dr + dg*dg + db*db
            if dist < min_dist:
                min_dist = dist
                best = code
        # Only match if reasonably close (threshold)
        if min_dist < 10000:  # ~57 per channel
            return best
        return None
        
    def _parse_body(self, rtf: str):
        """Parse RTF body, tracking formatting state and extracting text."""
        i = 0
        length = len(rtf)
        
        # Skip past the header (fonttbl, colortbl, etc.) to find body content
        # We'll parse from the start but handle groups properly
        
        depth = 0
        skip_group = 0  # depth at which we started skipping
        in_header_group = False
        
        while i < length:
            ch = rtf[i]
            
            if ch == '{':
                depth += 1
                # Once already inside a skipped destination, nested groups
                # — e.g. `{\*\panose ...}` inside `{\fonttbl...}` — must
                # NOT be re-evaluated: doing so let an inner ignorable
                # destination overwrite the single skip_group depth
                # tracker, so when the INNER group's closing brace fired,
                # skip mode cleared entirely even though the OUTER group
                # (fonttbl) was still open — leaking the font name that
                # followed. Just track depth while already skipping.
                if skip_group:
                    i += 1
                    continue
                # Check if this is a header group we should skip
                # Look ahead for \fonttbl, \colortbl, \stylesheet, \*\generator, etc.
                # Some generators (Word in particular) pretty-print RTF with
                # a literal newline right after the opening brace, before
                # the destination keyword — harmless to the format, but it
                # broke a plain .startswith() match, which is why a font
                # table pretty-printed this way used to leak as body text.
                lookahead = rtf[i+1:i+40].lstrip('\r\n\t ')
                # `\*\<word>` marks a destination as "ignorable if this
                # reader doesn't recognize it" — the RTF spec's own
                # mechanism for exactly this problem, and the general fix:
                # matching it directly means every current and future
                # ignorable destination (embedded OOXML packages, theme
                # data, field codes, etc.) gets skipped, not just the ones
                # on a hand-maintained list. A raw hex/binary blob leaking
                # into extracted text (e.g. a ZIP/OOXML signature) is the
                # symptom of a `\*`-marked group this list didn't cover —
                # see rnt_crud/clipboard_tool for where that surfaced.
                is_ignorable_destination = lookahead.startswith('\\*\\')
                if is_ignorable_destination or any(
                        lookahead.startswith(skip) for skip in
                        ['\\fonttbl', '\\colortbl', '\\stylesheet',
                         # Image and shape groups — skip entirely
                         '\\pict', '\\shp', '\\shprslt', '\\shpgrp',
                         '\\nonshppict',
                         '\\object', '\\objdata',
                         ]):
                    skip_group = depth
                    i += 1
                    continue

                # Save state for group
                self.group_stack.append({
                    'highlight': self.current_highlight,
                    'highlight_rgb': self.current_highlight_rgb,
                    'bold': self.current_bold,
                    'italic': self.current_italic,
                })
                i += 1
                continue

            elif ch == '}':
                if skip_group:
                    depth -= 1
                    # Exit skip mode only once we've closed back past the
                    # depth the skip started at — a nested group closing
                    # (panose, etc.) must not clear it early.
                    if depth < skip_group:
                        skip_group = 0
                    i += 1
                    continue
                depth -= 1

                # Restore state
                if self.group_stack:
                    state = self.group_stack.pop()
                    # Check if formatting changed
                    if (state['highlight'] != self.current_highlight or
                        state['bold'] != self.current_bold or
                        state['italic'] != self.current_italic):
                        self._flush_span()
                        self.current_highlight = state['highlight']
                        self.current_highlight_rgb = state['highlight_rgb']
                        self.current_bold = state['bold']
                        self.current_italic = state['italic']
                i += 1
                continue
            
            if skip_group:
                i += 1
                continue
                
            if ch == '\\':
                # Control word or symbol
                i += 1
                if i >= length:
                    break
                    
                next_ch = rtf[i]
                
                # Special symbols
                if next_ch == '\\':
                    self.current_text.append('\\')
                    i += 1
                    continue
                elif next_ch == '{':
                    self.current_text.append('{')
                    i += 1
                    continue
                elif next_ch == '}':
                    self.current_text.append('}')
                    i += 1
                    continue
                elif next_ch == '~':
                    self.current_text.append('\u00A0')  # non-breaking space
                    i += 1
                    continue
                elif next_ch == '-':
                    # Optional hyphen
                    i += 1
                    continue
                elif next_ch == '_':
                    self.current_text.append('\u2011')  # non-breaking hyphen
                    i += 1
                    continue
                elif next_ch == '\n' or next_ch == '\r':
                    # \<newline> = \par equivalent
                    self.current_text.append('\n')
                    i += 1
                    continue
                elif next_ch == "'":
                    # Hex escape: \'XX
                    if i + 2 < length:
                        hex_str = rtf[i+1:i+3]
                        try:
                            byte_val = int(hex_str, 16)
                            # cp1252 decode
                            self.current_text.append(bytes([byte_val]).decode('cp1252', errors='replace'))
                        except ValueError:
                            pass
                        i += 3
                    else:
                        i += 1
                    continue
                
                # Control word
                word = []
                while i < length and rtf[i].isalpha():
                    word.append(rtf[i])
                    i += 1
                control = ''.join(word)
                
                # Optional numeric parameter
                param_str = []
                if i < length and (rtf[i] == '-' or rtf[i].isdigit()):
                    if rtf[i] == '-':
                        param_str.append('-')
                        i += 1
                    while i < length and rtf[i].isdigit():
                        param_str.append(rtf[i])
                        i += 1
                param = int(''.join(param_str)) if param_str else None
                
                # Consume delimiter space
                if i < length and rtf[i] == ' ':
                    i += 1
                
                # Handle control words
                self._handle_control(control, param)
                
                # If _handle_control flagged an image group, skip rest of current group
                if self._skip_image_group:
                    self._skip_image_group = False
                    skip_group = depth  # skip everything at this depth and deeper
                
                continue
            
            elif ch == '\n' or ch == '\r':
                # RTF line breaks in source are ignored (not paragraph breaks)
                i += 1
                continue
            else:
                # Regular character
                # Check if we need to skip fallback chars after \u
                if self._skip_after_unicode > 0:
                    self._skip_after_unicode -= 1
                    i += 1
                    continue
                self.current_text.append(ch)
                i += 1
    
    def _handle_control(self, control: str, param):
        """Handle an RTF control word."""
        
        if control == 'par' or control == 'line':
            self.current_text.append('\n')
            
        elif control == 'tab':
            self.current_text.append('\t')
            
        elif control == 'b':
            # Bold: \b = on, \b0 = off
            new_bold = (param != 0) if param is not None else True
            if new_bold != self.current_bold:
                self._flush_span()
                self.current_bold = new_bold
                
        elif control == 'i':
            new_italic = (param != 0) if param is not None else True
            if new_italic != self.current_italic:
                self._flush_span()
                self.current_italic = new_italic
                
        elif control == 'highlight':
            # \highlight<N> = background highlight with color table index N
            if param is not None and param > 0:
                semantic, rgb = self._color_index_to_semantic(param)
            else:
                semantic, rgb = None, None
            
            if semantic != self.current_highlight:
                self._flush_span()
                self.current_highlight = semantic
                self.current_highlight_rgb = rgb
                
        elif control == 'highlight0' or (control == 'highlight' and param == 0):
            if self.current_highlight is not None:
                self._flush_span()
                self.current_highlight = None
                self.current_highlight_rgb = None
                
        elif control == 'cb':
            # Character background color — sometimes used instead of highlight
            if param is not None and param > 0:
                semantic, rgb = self._color_index_to_semantic(param)
            else:
                semantic, rgb = None, None
            if semantic != self.current_highlight:
                self._flush_span()
                self.current_highlight = semantic
                self.current_highlight_rgb = rgb
        
        elif control == 'plain':
            # Reset all formatting
            if self.current_bold or self.current_italic or self.current_highlight:
                self._flush_span()
                self.current_bold = False
                self.current_italic = False
                self.current_highlight = None
                self.current_highlight_rgb = None
        
        elif control == 'pard':
            # Paragraph defaults — reset paragraph formatting
            # Don't reset character formatting here
            pass
        
        elif control in ('pict', 'shp', 'shprslt', 'nonshppict',
                         'blipuid', 'pngblip', 'jpegblip', 'emfblip', 'wmetafile',
                         'shpinst', 'shpgrp', 'object', 'objdata', 'picprop'):
            # Image/shape/object data — skip the rest of this group
            # The hex image data follows these control words and must not be 
            # treated as text. Set _skip_image_group so the main loop skips.
            self._skip_image_group = True
        
        elif control == 'u':
            # Unicode character: \uN
            # RTF uses signed 16-bit, so emoji come as surrogate pairs
            # After \uN, skip self._uc_skip fallback characters
            if param is not None:
                try:
                    if param < 0:
                        param += 65536
                    code = param
                    
                    # Check if this is a high surrogate (first half of emoji)
                    if 0xD800 <= code <= 0xDBFF:
                        self._pending_surrogate = code
                    elif 0xDC00 <= code <= 0xDFFF:
                        # Low surrogate — combine with pending high surrogate
                        if self._pending_surrogate:
                            high = self._pending_surrogate
                            full_code = 0x10000 + (high - 0xD800) * 0x400 + (code - 0xDC00)
                            self.current_text.append(chr(full_code))
                            self._pending_surrogate = None
                        else:
                            self.current_text.append('\uFFFD')
                    else:
                        if self._pending_surrogate:
                            self.current_text.append('\uFFFD')
                            self._pending_surrogate = None
                        self.current_text.append(chr(code))
                except (ValueError, OverflowError):
                    self.current_text.append('\uFFFD')
            
            # Mark that we need to skip fallback chars
            self._skip_after_unicode = self._uc_value
        
        elif control == 'uc':
            # \ucN = number of fallback bytes/chars to skip after \uN
            self._uc_value = param if param is not None else 1
            
        elif control in ('f', 'fs', 'cf', 'lang', 'loch', 'hich', 'dbch',
                         'li', 'ri', 'fi', 'sa', 'sb', 'sl', 'slmult',
                         'qc', 'ql', 'qr', 'qj', 'super', 'sub',
                         'nosupersub', 'strike', 'ul', 'ulnone',
                         'viewkind', 'paperw', 'paperh', 'margl', 'margr',
                         'margt', 'margb', 'deff', 'deflang', 'deflangfe',
                         'ansi', 'ansicpg', 'nouicompat', 'urtf', 'rtf'):
            # Known but unneeded control words — skip silently
            pass
    
    def _flush_span(self):
        """Save current accumulated text as a TextSpan."""
        text = ''.join(self.current_text)
        if text:
            self.spans.append(TextSpan(
                text=text,
                highlight=self.current_highlight,
                bold=self.current_bold,
                italic=self.current_italic,
                highlight_rgb=self.current_highlight_rgb,
            ))
        self.current_text = []
    
    def _make_result_from_plain(self, text: str) -> ParsedNote:
        """Create a ParsedNote from plain (non-RTF) text."""
        return ParsedNote(
            spans=[TextSpan(text=text)] if text else [],
            plain_text=text,
            internal_format=text,
            total_chars=len(text),
            highlighted_chars=0,
            highlight_ratio=0.0,
        )
    
    def _build_result(self) -> ParsedNote:
        """Build final ParsedNote from accumulated spans."""
        # Build plain text
        plain_parts = []
        for span in self.spans:
            plain_parts.append(span.text)
        plain_text = ''.join(plain_parts)
        
        # Build internal bracket format
        internal_format = self._build_internal_format()
        
        # Calculate color stats
        color_stats = {}
        total_chars = 0
        highlighted_chars = 0
        
        for span in self.spans:
            char_count = len(span.text.strip())
            total_chars += char_count
            if span.highlight:
                highlighted_chars += char_count
                color_stats[span.highlight] = color_stats.get(span.highlight, 0) + char_count
        
        highlight_ratio = highlighted_chars / total_chars if total_chars > 0 else 0.0
        
        # Detect breaks
        break_pattern = re.compile(r'(={3,}|x{3,}|\n{4,})')
        breaks = break_pattern.findall(plain_text)
        
        return ParsedNote(
            spans=self.spans,
            plain_text=plain_text,
            internal_format=internal_format,
            color_stats=color_stats,
            total_chars=total_chars,
            highlighted_chars=highlighted_chars,
            highlight_ratio=highlight_ratio,
            has_breaks=len(breaks) > 0,
            break_count=len(breaks),
        )
    
    def _build_internal_format(self) -> str:
        """Convert spans to internal bracket format with break detection."""
        parts = []
        
        for span in self.spans:
            text = span.text
            
            # Detect and encode breaks within the text
            text = self._encode_breaks(text)
            
            # Build opening/closing tags
            tags_open = []
            tags_close = []
            
            if span.highlight:
                tags_open.append(f'[{span.highlight}]')
                tags_close.insert(0, f'[/{span.highlight}]')
            
            if span.bold and not span.highlight:
                # Bold without color = emphasis marker
                tags_open.append('[B]')
                tags_close.insert(0, '[/B]')
            elif span.bold and span.highlight:
                # Bold WITH color = higher salience
                tags_open.append('[B]')
                tags_close.insert(0, '[/B]')
            
            if span.italic:
                tags_open.append('[I]')
                tags_close.insert(0, '[/I]')
            
            if tags_open:
                parts.append(''.join(tags_open))
                parts.append(text)
                parts.append(''.join(tags_close))
            else:
                parts.append(text)
        
        result = ''.join(parts)

        # Clean up: remove decorative tag pairs that wrap nothing but
        # whitespace — but keep the whitespace itself. A highlighted or
        # bold run consisting only of "\n\n" (paragraph breaks that
        # happened to inherit the preceding color/bold state) is common
        # RTF; stripping the whitespace along with the tags used to erase
        # real paragraph breaks and glue adjacent lines together.
        result = re.sub(r'\[(\w+)\](\s*)\[/\1\]', r'\2', result)

        return result
    
    def _encode_breaks(self, text: str) -> str:
        """Detect separator patterns and encode as break tokens."""
        # ========================= or longer → [BR3] (section break)
        text = re.sub(r'={10,}', '\n[BR3]\n', text)
        # === to ========= → [BR2] (block break)
        text = re.sub(r'={3,9}', '\n[BR2]\n', text)
        # xxx or xxxx → [BR2] (placeholder/block break)
        text = re.sub(r'x{3,}', '\n[BR2]\n', text)
        # 4+ consecutive newlines → [BR3]
        text = re.sub(r'\n{5,}', '\n[BR3]\n', text)
        # 3-4 newlines → [BR2]
        text = re.sub(r'\n{3,4}', '\n[BR2]\n', text)
        
        return text
    

def bg_color_to_semantic(hex_color: str) -> Optional[str]:
    """Convert a notes.bg_color hex string to semantic color code."""
    if not hex_color or hex_color.strip() == '':
        return None
    return HEX_COLOR_MAP.get(hex_color.upper().strip(), None)


# ── Convenience functions ─────────────────────────────────────────────

def parse_rtf_string(rtf: str) -> ParsedNote:
    """Parse an RTF string."""
    parser = RTFParser()
    return parser.parse(rtf)

def parse_rtf_blob(blob: bytes) -> ParsedNote:
    """Parse a (possibly compressed) RTF blob."""
    parser = RTFParser()
    return parser.parse_compressed(blob)

def extract_highlighted_only(parsed: ParsedNote, 
                             colors: set = None,
                             keep_tags: bool = True) -> str:
    """
    Extract only the highlighted text from a parsed note.
    
    Args:
        parsed: A ParsedNote object
        colors: Set of color codes to include, e.g. {'g', 'b', 'p'}
                If None, includes all highlighted text.
        keep_tags: If True, wrap extracted text in bracket tags
    Returns:
        String with only the highlighted portions.
    """
    if colors is None:
        colors = set(COLOR_NAMES.keys())
    
    parts = []
    for span in parsed.spans:
        if span.highlight and span.highlight in colors:
            text = span.text.strip()
            if not text:
                continue
            if keep_tags:
                tags_open = f'[{span.highlight}]'
                tags_close = f'[/{span.highlight}]'
                if span.bold:
                    tags_open += '[B]'
                    tags_close = '[/B]' + tags_close
                parts.append(f'{tags_open}{text}{tags_close}')
            else:
                parts.append(text)
    
    return '\n'.join(parts)


def extract_high_salience(parsed: ParsedNote) -> str:
    """Extract green, blue, pink/purple content (the most important stuff)."""
    return extract_highlighted_only(parsed, colors={'g', 'b', 'p', 'u'})


if __name__ == '__main__':
    # Test with the RTF from the clipboard dump
    test_rtf = r"""{\rtf1\ansi\ansicpg1252\deff0\nouicompat{\fonttbl{\f0\fswiss Calibri;}{\f1\fnil\fcharset0 Calibri;}{\f2\fnil Calibri;}}
{\colortbl ;\red255\green153\blue204;\red204\green255\blue255;\red204\green255\blue204;\red255\green255\blue255;\red255\green255\blue153;\red255\green204\blue153;\red204\green153\blue255;}
{\*\generator Riched20 10.0.26100}\viewkind4\uc1 
\pard\b\f0\fs20\lang8192 The overall goal of the system is to \highlight1\f1\lang1033 maximize \f0\lang8192 the \f1\lang1033 long-term strategic-evolutionary advantage\highlight0  and \highlight2 adaptive dominance\highlight0  of the system.\par
\par
\highlight3 This is green text.\highlight0\par
\highlight5 This is yellow text.\highlight0\par
\highlight6 This is orange text.\highlight0\par
\highlight7 This is purple text.\highlight0\par
}"""
    
    result = parse_rtf_string(test_rtf)
    
    print("=== PLAIN TEXT ===")
    print(result.plain_text)
    print("\n=== INTERNAL FORMAT ===")
    print(result.internal_format)
    print("\n=== COLOR STATS ===")
    for color, count in sorted(result.color_stats.items(), key=lambda x: -x[1]):
        print(f"  {COLOR_NAMES.get(color, color)}: {count} chars")
    print(f"\n  Total chars: {result.total_chars}")
    print(f"  Highlighted: {result.highlighted_chars} ({result.highlight_ratio:.1%})")
    print(f"\n=== SPANS ({len(result.spans)}) ===")
    for i, span in enumerate(result.spans):
        fmt = []
        if span.highlight: fmt.append(f"hl={span.highlight}")
        if span.bold: fmt.append("bold")
        if span.italic: fmt.append("italic")
        fmt_str = f" [{', '.join(fmt)}]" if fmt else ""
        preview = span.text[:80].replace('\n', '\\n')
        print(f"  {i}: {preview!r}{fmt_str}")