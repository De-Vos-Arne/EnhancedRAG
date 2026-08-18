#!/usr/bin/env python3
"""Print the tables for the thesis.

    python scripts/analyse.py
    python scripts/analyse.py --baseline R1_baseline_vector --csv results/table.csv
"""
import argparse
import _bootstrap  # noqa: F401
from enhanced_rag import settings
from enhanced_rag.evaluation import statistics
from enhanced_rag.repositories.results_repository import ResultsRepository

ap = argparse.ArgumentParser()
ap.add_argument("--results", default=str(settings.RESULTS_DB))
ap.add_argument("--baseline", default="R0_baseline_fts")
ap.add_argument("--csv", default=None)
a = ap.parse_args()

statistics.report(ResultsRepository(a.results), a.baseline, a.csv)
