#!/usr/bin/env python3
"""
Diagnostic only — dumps the raw RTF currently on the clipboard to a file,
verbatim, so a parsing bug can be pinpointed against real bytes instead of
guessed at. Not part of the normal tool chain.

    python tools/dump_clipboard_rtf.py
"""
import sys
from pathlib import Path

import win32clipboard

CF_RTF = win32clipboard.RegisterClipboardFormat("Rich Text Format")

win32clipboard.OpenClipboard()
try:
    if not win32clipboard.IsClipboardFormatAvailable(CF_RTF):
        print("No RTF on the clipboard — copy formatted text first.")
        sys.exit(1)
    data = win32clipboard.GetClipboardData(CF_RTF)
finally:
    win32clipboard.CloseClipboard()

raw = data if isinstance(data, bytes) else data.encode("cp1252", errors="replace")
out = Path("clipboard_dump.rtf")
out.write_bytes(raw)
print(f"Wrote {len(raw):,} bytes to {out.resolve()}")
print("\nFirst 2000 chars (cp1252-decoded):\n")
print(raw[:2000].decode("cp1252", errors="replace"))
