#!/usr/bin/env python3
"""Run the experiment.

    python scripts/run_eval.py --dry-run          # retrieval only, seconds
    python scripts/run_eval.py
    python scripts/run_eval.py --generator deepseek --groups A B
    python scripts/run_eval.py --questions        # list the question set
"""
import argparse
import _bootstrap  # noqa: F401
from enhanced_rag import settings
from enhanced_rag.evaluation import questions as Q, runner
from enhanced_rag.repositories.results_repository import ResultsRepository
from enhanced_rag.services.rag_service import RagService

ap = argparse.ArgumentParser()
ap.add_argument("--corpus", default=str(settings.CORPUS_DB))
ap.add_argument("--results", default=str(settings.RESULTS_DB))
ap.add_argument("--regimes", nargs="*", default=None)
ap.add_argument("--groups", nargs="*", default=None)
ap.add_argument("--only", nargs="*", default=None, help="question ids")
ap.add_argument("--generator", default=None)
ap.add_argument("--embedder", default=None)
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--redo", action="store_true")
ap.add_argument("--questions", action="store_true", help="list questions and exit")
a = ap.parse_args()

if a.questions:
    for g, meta in Q.GROUPS.items():
        qs = [q for q in Q.QUESTIONS if q["group"] == g]
        print(f"\n{g} — {meta['name']}  ({len(qs)})\n   {meta['hypothesis']}")
        for q in qs:
            print(f"   {q['id']}  {q['q']}")
    print(f"\nTotal: {len(Q.QUESTIONS)}")
    raise SystemExit

rag = RagService(a.corpus, a.embedder)
print(rag.stats())
out = runner.run(rag, ResultsRepository(a.results), regimes=a.regimes,
                 groups=a.groups, ids=a.only, generator=a.generator,
                 retrieval_only=a.dry_run, redo=a.redo)
runner.print_overlap(out["overlap"])
if out["errors"]:
    print(f"\n{len(out['errors'])} run(s) failed:")
    for qid, rk, err in out["errors"][:10]:
        print(f"  {qid} / {rk}: {err}")
if not a.dry_run:
    print("\nRate the answers:  python scripts/serve.py  ->  /rag/rate")
