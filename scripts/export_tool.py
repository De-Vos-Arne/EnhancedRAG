#!/usr/bin/env python3
"""Selective export from the RightNote clipboard.

    python scripts/export_tool.py                       # popup
    python scripts/export_tool.py --in note.rtf --colors u,p,b --format line
"""
import _bootstrap  # noqa: F401
from enhanced_rag.exporting.cli import main

main()
