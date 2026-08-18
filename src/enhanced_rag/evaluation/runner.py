"""
Runs the experiment: every regime against every question.

Deliberately has no CLI of its own — `scripts/run_eval.py` is the thin
entry point. That keeps the logic callable from a notebook or from the web
layer later without shelling out.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .. import settings
from ..repositories.results_repository import ResultsRepository
from ..services.rag_service import RagService
from . import questions as Q


def select_questions(groups=None, ids=None):
    qs = Q.QUESTIONS
    if groups:
        want = {g.upper() for g in groups}
        qs = [q for q in qs if q["group"] in want]
    if ids:
        keep = set(ids)
        qs = [q for q in qs if q["id"] in keep]
    return qs


def overlap_matrix(retrieved: dict[str, dict[str, set]]) -> dict:
    """Jaccard overlap of retrieved fragment sets between regimes.

    If two regimes overlap near 1.0 they are the same experiment twice, and
    any downstream difference in answer quality is noise. Worth knowing
    before spending hours on generation and rating.
    """
    keys = sorted(retrieved)
    out = {a: {} for a in keys}
    for a in keys:
        for b in keys:
            scores = []
            for qid in retrieved[a]:
                sa, sb = retrieved[a].get(qid, set()), retrieved[b].get(qid, set())
                if sa or sb:
                    scores.append(len(sa & sb) / len(sa | sb))
            out[a][b] = round(sum(scores) / max(len(scores), 1), 3)
    return out


def run(rag: RagService, results: ResultsRepository, *,
        regimes=None, groups=None, ids=None, generator=None,
        retrieval_only=False, redo=False, on_event=print) -> dict:
    regimes = regimes or list(settings.REGIMES)
    unknown = [r for r in regimes if r not in settings.REGIMES]
    if unknown:
        raise KeyError(f"Unknown regime(s): {', '.join(unknown)}")

    qs = select_questions(groups, ids)
    if not qs:
        raise ValueError("No questions selected.")

    gen_key = generator or settings.DEFAULT_GENERATOR
    corpus_name = Path(rag.repo.db_path).name
    retrieved: dict[str, dict[str, set]] = defaultdict(dict)
    errors = []

    on_event(f"{len(qs)} questions x {len(regimes)} regimes = "
             f"{len(qs) * len(regimes)} runs"
             + (" (retrieval only)" if retrieval_only else f" via {gen_key}"))

    for q in qs:
        on_event(f"\n{q['id']}  {q['q'][:70]}")
        for rk in regimes:
            if not redo and not retrieval_only and \
                    results.has_run(q["id"], rk, gen_key):
                on_event(f"    {rk:<22} (already stored)")
                continue
            try:
                res = rag.answer(q["q"], rk, generator=gen_key,
                                 retrieval_only=retrieval_only)
            except Exception as e:
                on_event(f"    {rk:<22} FAILED: {e}")
                errors.append((q["id"], rk, str(e)))
                continue

            res["question_id"] = q["id"]
            retrieved[rk][q["id"]] = {f["unit_id"] for f in res["fragments"]}
            m = res["metrics"]
            on_event(f"    {rk:<22} {m['n_fragments']:>3} frags  "
                     f"w={m['mean_weight']:<6} hl={m['pct_highlighted']:>5.1f}%  "
                     f"{m['retrieve_s']}s+{m['generate_s']}s")
            if not retrieval_only:
                results.save_run(res, corpus_name, gen_key, rag.embedder.key)

    return {"overlap": overlap_matrix(retrieved) if retrieved else {},
            "errors": errors, "questions": len(qs), "regimes": regimes}


def print_overlap(matrix: dict, out=print):
    if not matrix:
        return
    keys = sorted(matrix)
    out("\nRetrieval overlap (Jaccard of retrieved fragment sets):")
    out("            " + "".join(f"{k.split('_')[0]:>8}" for k in keys))
    for a in keys:
        out(f"  {a.split('_')[0]:<10}" + "".join(f"{matrix[a][b]:>8.2f}" for b in keys))
    out("\n  Anything above ~0.90 off the diagonal means those two regimes are "
        "\n  retrieving the same thing, and comparing their answers will not "
        "\n  tell you anything.")
