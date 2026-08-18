#!/usr/bin/env python3
"""Build the keyword index and both embedding variants.

    python scripts/build_index.py
    python scripts/build_index.py --embedder minilm --force
    python scripts/build_index.py --fts-only        # skip Ollama entirely
    python scripts/build_index.py --sample 5000      # fast iteration: only
                                                       # the 5000 highest-
                                                       # weight fragments
"""
import argparse
import _bootstrap  # noqa: F401
from enhanced_rag import settings
from enhanced_rag.services import indexer

ap = argparse.ArgumentParser()
ap.add_argument("--corpus", default=str(settings.CORPUS_DB))
ap.add_argument("--embedder", default=None)
ap.add_argument("--batch", type=int, default=32)
ap.add_argument("--force", action="store_true")
ap.add_argument("--fts-only", action="store_true")
ap.add_argument("--sample", type=int, default=None,
                help="Only embed the N highest-weight fragments — for fast "
                     "iteration while the parser/pipeline is still changing.")
a = ap.parse_args()

indexer.build(a.corpus, a.embedder, batch=a.batch, force=a.force,
              fts_only=a.fts_only, sample=a.sample)
print("\nNext:  python scripts/serve.py")
