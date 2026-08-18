"""
RightNote Clipboard Tool — tray app for pasting archive content into an LLM chat.

Copy RTF from RightNote (or any RTF-aware app), then either:
  Ctrl+Shift+V  quick-convert using the current saved settings
  Ctrl+Shift+M  open a small menu to pick which colours to include, whether
                to keep unmarked text, and edit the preamble text that gets
                prepended (explains the tag system to whichever model you
                paste into) — then convert
  Ctrl+Shift+Q  quit

The clipboard is replaced with plain text wrapped in [PUR]/[BLU]/[GRN]/…
tags (colours.tag_for — the same tags used everywhere else in this
project), with a preamble explaining what they mean, so you can paste a
chunk of the archive into ChatGPT/Claude/Gemini and have the highlight
signal survive the trip.

Settings persist to tools/clipboard_tool_config.json.

Requirements: pip install pywin32 keyboard pystray Pillow
Usage: python tools/clipboard_tool.py            # tray + hotkeys
       python tools/clipboard_tool.py --once      # one-shot convert, no tray
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

_missing = []
try:
    import win32clipboard
    import win32con
except ImportError:
    _missing.append("pywin32")
try:
    import keyboard
except ImportError:
    _missing.append("keyboard")
try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    _missing.append("pystray Pillow")

if _missing:
    print("Missing packages:", ", ".join(_missing))
    print("Install with:  pip install pywin32 keyboard pystray Pillow")
    sys.exit(1)

import tkinter as tk
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from enhanced_rag.core.rtf_parser import RTFParser
from enhanced_rag.core import colours

CONFIG_PATH = Path(__file__).parent / "clipboard_tool_config.json"

# Qualitative, not the retrieval-system legend (colours.legend()) — that one
# lists numeric weights and break-token mechanics meant for the RAG prompt,
# none of which apply here. This is a much shorter framing for "help me
# read/edit this text," not "rank these fragments."
DEFAULT_PREAMBLE = (
    "The text below uses a personal colour-tagging system, marked with "
    "[TAG]...[/TAG] — not markdown or code syntax. Each colour is a rough "
    "conceptual signal for how strongly I judged the marked content, not a "
    "strict rule:\n"
    "  Purple — standout, rare peak (weight ~5)\n"
    "  Pink — exceptional (weight ~4)\n"
    "  Blue — excellent, high-salience (weight ~3)\n"
    "  Green — good, validated (weight ~2)\n"
    "  Yellow — noteworthy but provisional (weight ~1)\n"
    "  Orange — flagged as needing correction or revision (weight ~0.5)\n"
    "Bold marks something I considered especially important on top of "
    "whatever colour it carries.\n"
    "Orange and yellow generally mean I think that part needs work — but I "
    "sometimes use any colour, orange/yellow included, to mark contrast or "
    "a counterpoint instead of a quality judgement. Use your own reading of "
    "the content when a tag's intent seems ambiguous. Unmarked text carries "
    "no signal either way."
)

DEFAULT_CONFIG = {
    "enabled_colors": [c.code for c in colours.COLOURS if c.code != "g2"],
    "include_unmarked": True,
    "preamble": DEFAULT_PREAMBLE,
}


# ── Config ───────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULT_CONFIG, **cfg}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── Clipboard I/O ────────────────────────────────────────────────────

_CF_RTF = None


def _register_rtf():
    global _CF_RTF
    if _CF_RTF is None:
        _CF_RTF = win32clipboard.RegisterClipboardFormat("Rich Text Format")
    return _CF_RTF


import time as _time

# Guards every clipboard access in this process (hotkey path and menu path
# both go through it) — Win32 clipboard ownership is finicky enough from
# background Python threads that even our own back-to-back read-then-write
# can collide with itself, not just with other apps.
_CLIP_LOCK = threading.RLock()


def _clipboard_op(fn, tries: int = 10, delay: float = 0.06):
    """Run fn() with the clipboard open, retrying the *whole*
    open/work/close sequence on any failure.

    A bare retry on just OpenClipboard() isn't enough: it can report
    success while the ownership doesn't actually stick long enough for
    the very next call (EmptyClipboard/SetClipboardData/CloseClipboard)
    to see it — exactly what "Thread does not have a clipboard open" on
    SetClipboardData means despite OpenClipboard not having raised.
    Retrying the entire block re-establishes ownership cleanly instead of
    limping forward on a handle that never really stuck.
    """
    last = None
    with _CLIP_LOCK:
        for _ in range(tries):
            try:
                win32clipboard.OpenClipboard()
                try:
                    return fn()
                finally:
                    win32clipboard.CloseClipboard()
            except Exception as e:
                last = e
                _time.sleep(delay)
        raise last


def get_clipboard_rtf() -> str | None:
    _register_rtf()

    def _read():
        if win32clipboard.IsClipboardFormatAvailable(_CF_RTF):
            data = win32clipboard.GetClipboardData(_CF_RTF)
            return data.decode("cp1252", errors="replace") if isinstance(data, bytes) else str(data)
        return None

    return _clipboard_op(_read)


def set_clipboard_text(text: str):
    def _write():
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)

    _clipboard_op(_write)


# ── Conversion ───────────────────────────────────────────────────────

def format_spans(parsed, cfg: dict) -> str:
    """Walk parsed spans, keep only enabled colours (+ unmarked if wanted),
    wrap each kept span in the project's standard [TAG]…[/TAG] format."""
    enabled = set(cfg["enabled_colors"])
    parts = []
    for span in parsed.spans:
        text = span.text
        if not text:
            continue
        # Whitespace-only spans (from \par between differently-coloured
        # runs) carry the paragraph break itself — drop them only if truly
        # empty, never just because they're blank, or tags run together.
        if not text.strip():
            parts.append(text)
            continue
        if span.highlight:
            if span.highlight not in enabled:
                continue
            tag = colours.tag_for(span.highlight, bold=span.bold)
            code = colours.BY_CODE.get(span.highlight)
            close = f"[/{code.tag}]" if code else ""
            parts.append(f"{tag}{text}{close}")
        else:
            if not cfg["include_unmarked"]:
                continue
            parts.append(text)
    return "".join(parts)


def convert_clipboard(cfg: dict) -> tuple[bool, str]:
    """Returns (ok, message)."""
    rtf = get_clipboard_rtf()
    if rtf is None:
        return False, "No RTF on clipboard — copy formatted text from RightNote first."

    parsed = RTFParser().parse(rtf)
    body = format_spans(parsed, cfg)
    if not body.strip():
        return False, "Nothing survived the current colour filter — check your settings (Ctrl+Shift+M)."

    preamble = cfg.get("preamble", "").strip()
    output = f"{preamble}\n\n{body}" if preamble else body
    set_clipboard_text(output)
    return True, f"Converted {len(body):,} chars ({parsed.highlighted_chars:,} highlighted)."


# ── Mini menu (Ctrl+Shift+M) ─────────────────────────────────────────
# Styled to match the explorer web UI: white ground, #f3f3f3 panels,
# #2b5797 accent, Segoe UI, thin flat borders — not classic beveled tk.

_BG = "#ffffff"
_PANEL = "#f3f3f3"
_BORDER = "#c8c8c8"
_TEXT = "#1a1a1a"
_DIM = "#777777"
_ACCENT = "#2b5797"


def open_menu(cfg: dict, on_convert):
    """Small always-on-top window: colour checkboxes + editable preamble."""
    root = tk.Tk()
    root.title("Clipboard tool")
    root.attributes("-topmost", True)
    root.configure(bg=_BG)

    w, h = 400, 560
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{max(20,(sh-h)//3)}")

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("Tool.TCheckbutton", background=_BG, foreground=_TEXT,
                    font=("Segoe UI", 10))
    style.map("Tool.TCheckbutton", background=[("active", _BG)])
    style.configure("Ghost.TButton", background=_PANEL, foreground=_DIM,
                    font=("Segoe UI", 9), borderwidth=1, relief="solid", padding=(10, 5))
    style.map("Ghost.TButton", foreground=[("active", _TEXT)],
              bordercolor=[("active", _ACCENT)])
    style.configure("Accent.TButton", background=_ACCENT, foreground="white",
                    font=("Segoe UI", 9, "bold"), borderwidth=0, padding=(14, 6))
    style.map("Accent.TButton", background=[("active", "#1e3f70")])

    LBL = {"font": ("Segoe UI", 10, "bold"), "bg": _BG, "fg": _TEXT}

    body = tk.Frame(root, bg=_BG)
    body.pack(fill="both", expand=True, padx=14, pady=12)

    tk.Label(body, text="Colours to include", **LBL).pack(anchor="w")
    color_vars = {}
    for c in colours.COLOURS:
        if c.code == "g2":
            continue
        v = tk.BooleanVar(value=c.code in cfg["enabled_colors"])
        color_vars[c.code] = v
        ttk.Checkbutton(body, text=f"{c.name}  ({c.tag})", variable=v,
                        style="Tool.TCheckbutton").pack(anchor="w", pady=1)

    unmarked_var = tk.BooleanVar(value=cfg["include_unmarked"])
    ttk.Checkbutton(body, text="Include unmarked (unhighlighted) text",
                    variable=unmarked_var, style="Tool.TCheckbutton").pack(
                    anchor="w", pady=(6, 10))

    tk.Label(body, text="Preamble sent before the content", **LBL).pack(anchor="w")
    text_box = tk.Text(body, height=13, wrap="word", font=("Segoe UI", 10),
                       relief="solid", borderwidth=1, highlightthickness=1,
                       highlightbackground=_BORDER, highlightcolor=_ACCENT,
                       padx=7, pady=7, bg=_BG, fg=_TEXT)
    text_box.pack(fill="both", expand=True, pady=(4, 6))
    text_box.insert("1.0", cfg.get("preamble", ""))

    status = tk.Label(body, text="", fg=_DIM, bg=_BG, font=("Segoe UI", 9))
    status.pack(anchor="w", pady=(0, 4))

    def do_reset_preamble():
        text_box.delete("1.0", "end")
        text_box.insert("1.0", DEFAULT_PREAMBLE)

    def do_convert():
        cfg["enabled_colors"] = [code for code, v in color_vars.items() if v.get()]
        cfg["include_unmarked"] = unmarked_var.get()
        cfg["preamble"] = text_box.get("1.0", "end").strip()
        save_config(cfg)
        ok, msg = convert_clipboard(cfg)
        status.config(text=msg, fg="#2a2" if ok else "#c33")
        if ok:
            root.after(800, root.destroy)

    btns = tk.Frame(body, bg=_BG)
    btns.pack(fill="x")
    ttk.Button(btns, text="Reset preamble", command=do_reset_preamble,
              style="Ghost.TButton").pack(side="left")
    ttk.Button(btns, text="Cancel", command=root.destroy,
              style="Ghost.TButton").pack(side="right")
    ttk.Button(btns, text="Convert now", command=do_convert,
              style="Accent.TButton").pack(side="right", padx=6)

    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()


# ── Tray + hotkeys ───────────────────────────────────────────────────

def make_icon_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # four quadrants in the archive's own highlight colours
    quad_colors = ["#FF99CC", "#9EE8E8", "#A8E6A8", "#F2E68A"]
    for i, col in enumerate(quad_colors):
        x0, y0 = (32 * (i % 2), 32 * (i // 2))
        d.rectangle([x0, y0, x0 + 32, y0 + 32], fill=col)
    return img


def run_tray():
    cfg = load_config()
    lock = threading.Lock()
    last_run = [0.0]

    def quick_convert():
        import time
        now = time.monotonic()
        if now - last_run[0] < 0.5:
            return  # OS key-repeat firing the hotkey multiple times per hold
        last_run[0] = now
        with lock:
            ok, msg = convert_clipboard(cfg)
        print(f"[Clipboard tool] {msg}")

    def open_menu_threadsafe():
        # tkinter needs its own thread here since pystray already owns one
        threading.Thread(target=lambda: open_menu(cfg, quick_convert), daemon=True).start()

    keyboard.add_hotkey("ctrl+shift+v", quick_convert)
    keyboard.add_hotkey("ctrl+shift+m", open_menu_threadsafe)

    def on_quit(icon, item):
        icon.stop()
        keyboard.unhook_all_hotkeys()

    icon = pystray.Icon(
        "rightnote-clipboard",
        make_icon_image(),
        "RightNote Clipboard Tool",
        menu=pystray.Menu(
            pystray.MenuItem("Convert clipboard (Ctrl+Shift+V)", lambda i, x: quick_convert()),
            pystray.MenuItem("Open menu (Ctrl+Shift+M)", lambda i, x: open_menu_threadsafe()),
            pystray.MenuItem("Quit (Ctrl+Shift+Q)", on_quit),
        ),
    )
    keyboard.add_hotkey("ctrl+shift+q", lambda: on_quit(icon, None))

    print("RightNote Clipboard Tool running.")
    print("  Ctrl+Shift+V  convert clipboard now")
    print("  Ctrl+Shift+M  open settings menu")
    print("  Ctrl+Shift+Q  quit")
    icon.run()


if __name__ == "__main__":
    if "--once" in sys.argv:
        cfg = load_config()
        ok, msg = convert_clipboard(cfg)
        print(msg)
        sys.exit(0 if ok else 1)
    run_tray()
