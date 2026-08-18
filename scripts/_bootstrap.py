"""Puts src/ on the path so the scripts run without installing the package."""
import sys
from pathlib import Path

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
