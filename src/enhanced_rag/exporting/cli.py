#!/usr/bin/env python3
"""
Selective clipboard export from RightNote.

Copy anything in RightNote (Ctrl+C), run this, and pick exactly what you
want out of the selection: only the purple and pink, only the bold, the
whole thing with inline colour tags, or a stripped plain version. The
result goes straight back on the clipboard, ready to paste into a chat.

    python export_gui.py            # popup, reads the current clipboard
    python export_gui.py --watch    # stays open; Reload re-reads the clipboard

Output formats
  inline    ...text [BLU]highlighted bit[/BLU] more text...
              Preserves position. Best when you are editing a document and
              want the model to see which parts you marked.
  line      [BLU*] one line per fragment
              Compact. Best when exporting a lot at once — no duplication
              of surrounding text.
  plain     no markers at all

Windows only for clipboard RTF (uses the Win32 API); the file-in/file-out
mode below works anywhere:

    python export_gui.py --in note.rtf --out selected.txt --colors u,p,b --format line
"""

import argparse
from pathlib import Path

from .. import settings
from ..core import colours
from ..core.rtf_parser import RTFParser

COLOR_ORDER = [(c.code, f"{c.name:<7} ({c.weight:g} · {c.meaning})")
               for c in colours.COLOURS] + [(None, "Unmarked text")]

def _legend() -> str:
    """The colour explanation, prefixed as comments so it reads as a header."""
    return "\n".join("# " + l for l in colours.legend().splitlines()) + "\n"


# ── Rendering ──────────────────────────────────────────────────────────

def render(parsed, colors, bold_only=False, fmt="inline",
           legend=False, keep_breaks=True):
    """colors: set of colour codes to keep; None inside it means unmarked."""
    out = []
    if fmt == "line":
        current, cur_key = [], None
        for sp in parsed.spans:
            if sp.highlight not in colors:
                continue
            if bold_only and not sp.bold:
                continue
            key = (sp.highlight, sp.bold)
            if key != cur_key and current:
                out.append(_line(cur_key, "".join(current)))
                current = []
            cur_key = key
            current.append(sp.text)
        if current:
            out.append(_line(cur_key, "".join(current)))
        body = "\n".join(l for l in out if l.strip())
    else:
        for sp in parsed.spans:
            keep = sp.highlight in colors and not (bold_only and not sp.bold)
            if not keep:
                continue
            if fmt == "plain" or sp.highlight is None:
                out.append(sp.text)
            else:
                tag = colours.TAGS.get(sp.highlight, "?")
                star = "*" if sp.bold else ""
                out.append(f"[{tag}{star}]{sp.text}[/{tag}]")
        body = "".join(out)

    if not keep_breaks:
        body = body.replace("[BR1]", "").replace("[BR2]", "").replace("[BR3]", "")
    body = "\n".join(l.rstrip() for l in body.splitlines())
    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")
    return (_legend() + "\n" + body) if legend else body


def _line(key, text):
    text = " ".join(text.split())
    if not text:
        return ""
    color, bold = key
    if color is None:
        return text
    return f"[{colours.TAGS.get(color,'?')}{'*' if bold else ''}] {text}"


# ── Clipboard (Windows) ────────────────────────────────────────────────

def read_clipboard_rtf():
    import win32clipboard
    cf = win32clipboard.RegisterClipboardFormat("Rich Text Format")
    win32clipboard.OpenClipboard()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(cf):
            return None
        data = win32clipboard.GetClipboardData(cf)
        return data.decode("cp1252", "replace") if isinstance(data, bytes) else str(data)
    finally:
        win32clipboard.CloseClipboard()


def write_clipboard(text):
    import win32clipboard, win32con
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


# ── GUI ────────────────────────────────────────────────────────────────

def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("RightNote export")
    root.geometry("560x620")

    state = {"parsed": None}
    checks, tally = {}, {}

    def reload_clipboard():
        rtf = read_clipboard_rtf()
        if rtf is None:
            messagebox.showwarning(
                "Nothing to convert",
                "The clipboard has no rich text. Copy a selection in "
                "RightNote first, then press Reload.")
            return
        state["parsed"] = RTFParser().parse(rtf)
        stats = state["parsed"].color_stats or {}
        for code, lbl in COLOR_ORDER:
            n = stats.get(code, 0) if code else (
                state["parsed"].total_chars - state["parsed"].highlighted_chars)
            tally[code].config(text=f"{n:,} chars")
            checks[code].state(["!disabled"] if n else ["disabled"])
        preview()

    frm = ttk.Frame(root, padding=14)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Include", font=("", 10, "bold")).pack(anchor="w")
    box = ttk.Frame(frm); box.pack(fill="x", pady=(4, 10))
    for code, label in COLOR_ORDER:
        row = ttk.Frame(box); row.pack(fill="x")
        var = tk.BooleanVar(value=code in ("u", "p", "b", "g"))
        cb = ttk.Checkbutton(row, text=label, variable=var,
                             command=lambda: preview())
        cb.pack(side="left")
        cb.var = var
        checks[code] = cb
        tally[code] = ttk.Label(row, text="—", foreground="#666")
        tally[code].pack(side="right")

    opts = ttk.Frame(frm); opts.pack(fill="x", pady=(0, 10))
    fmt = tk.StringVar(value="line")
    ttk.Label(opts, text="Format").grid(row=0, column=0, sticky="w")
    for i, (val, txt) in enumerate([("line", "One line per fragment"),
                                    ("inline", "Inline tags, keeps position"),
                                    ("plain", "Plain text")]):
        ttk.Radiobutton(opts, text=txt, value=val, variable=fmt,
                        command=lambda: preview()).grid(row=i, column=1, sticky="w")

    bold_only = tk.BooleanVar(value=False)
    legend = tk.BooleanVar(value=True)
    breaks = tk.BooleanVar(value=True)
    for i, (var, txt) in enumerate([
            (bold_only, "Bold text only"),
            (legend, "Explain what the colours mean (for the model)"),
            (breaks, "Keep [BR] break markers")]):
        ttk.Checkbutton(frm, text=txt, variable=var,
                        command=lambda: preview()).pack(anchor="w")

    ttk.Label(frm, text="Preview", font=("", 10, "bold")).pack(anchor="w", pady=(10, 2))
    txt = tk.Text(frm, height=14, wrap="word", font=("Consolas", 9))
    txt.pack(fill="both", expand=True)
    status = ttk.Label(frm, text="", foreground="#666")
    status.pack(anchor="w", pady=(6, 0))

    def selected():
        return {c for c, _ in COLOR_ORDER if checks[c].var.get()}

    def build():
        if not state["parsed"]:
            return ""
        return render(state["parsed"], selected(), bold_only.get(),
                      fmt.get(), legend.get(), breaks.get())

    def preview():
        out = build()
        txt.delete("1.0", "end")
        txt.insert("1.0", out[:6000])
        status.config(text=f"{len(out):,} characters, "
                           f"{out.count(chr(10)) + 1:,} lines")

    def copy_out():
        out = build()
        if not out.strip():
            messagebox.showinfo("Nothing selected",
                                "Tick at least one colour that appears in the "
                                "selection.")
            return
        write_clipboard(out)
        status.config(text=f"Copied {len(out):,} characters to the clipboard.")

    btns = ttk.Frame(frm); btns.pack(fill="x", pady=(10, 0))
    ttk.Button(btns, text="Reload clipboard", command=reload_clipboard).pack(side="left")
    ttk.Button(btns, text="Copy result", command=copy_out).pack(side="right")

    reload_clipboard()
    root.mainloop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", help="read RTF from a file instead")
    ap.add_argument("--out", help="write to a file instead of the clipboard; "
                    "a bare filename lands in exports/")
    ap.add_argument("--colors", default="u,p,b,g",
                    help="comma list of codes; 'none' includes unmarked text")
    ap.add_argument("--format", default="line", choices=["line", "inline", "plain"])
    ap.add_argument("--bold-only", action="store_true")
    ap.add_argument("--legend", action="store_true")
    args = ap.parse_args()

    if not args.infile:
        run_gui()
        return

    colors = {None if c.strip() == "none" else c.strip()
              for c in args.colors.split(",")}
    parsed = RTFParser().parse(Path(args.infile).read_text(encoding="cp1252",
                                                           errors="replace"))
    out = render(parsed, colors, args.bold_only, args.format, args.legend)
    if args.out:
        dest = Path(args.out)
        if not dest.is_absolute() and dest.parent == Path("."):
            dest = settings.EXPORT_DIR / dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(out, encoding="utf-8")
        print(f"Wrote {dest} ({len(out):,} chars)")
    else:
        print(out)


if __name__ == "__main__":
    main()
