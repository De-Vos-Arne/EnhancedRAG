#!/usr/bin/env python3
"""Parse the .rnt archive into the corpus DB (shadow DB + knowledge units).

    python scripts/build_corpus.py
    python scripts/build_corpus.py --archive data/PersonalArchive.rnt

Wraps the two pipeline stages so there is one command to remember.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path
import _bootstrap  # noqa: F401
from enhanced_rag import settings

ap = argparse.ArgumentParser()
ap.add_argument("--archive", default=str(settings.ARCHIVE))
ap.add_argument("--corpus", default=str(settings.CORPUS_DB))
a = ap.parse_args()

archive = Path(a.archive)
if not archive.exists():
    sys.exit(f"Archive not found: {archive}\n"
             f"Put your .rnt file there, or pass --archive.")

pipeline = Path(__file__).resolve().parents[1] / "src/enhanced_rag/pipeline"
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
for stage, args in (("build_shadow_db.py", [str(archive), a.corpus]),
                    ("build_knowledge_units.py", [a.corpus, str(archive)])):
    print(f"\n=== {stage} ===")
    r = subprocess.run([sys.executable, str(pipeline / stage), *args], env=env)
    if r.returncode:
        sys.exit(f"{stage} failed. Fix the error above and re-run.")

print(f"\nCorpus written to {a.corpus}")
print("Next:  python scripts/build_index.py")
