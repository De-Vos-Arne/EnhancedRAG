"""
Statistics for the thesis: per regime and per question group, with paired
tests against the baseline.

Pairing matters — every regime answers the same questions, so a paired
test is both correct and far more sensitive than comparing independent
means. No scipy dependency; the t distribution uses a small lookup below
n=30 and a normal approximation above it, which is honest enough at this
sample size as long as the thesis says so.
"""

import csv
import json
import math
import random
import statistics as st
from collections import defaultdict

T95 = {1:12.71,2:4.30,3:3.18,4:2.78,5:2.57,6:2.45,7:2.36,8:2.31,9:2.26,
       10:2.23,12:2.18,15:2.13,20:2.09,25:2.06,30:2.04,40:2.02,60:2.00}


def tcrit(df):
    if df <= 0: return float("nan")
    for k in sorted(T95):
        if df <= k: return T95[k]
    return 1.96


def paired_test(a, b):
    """a, b are aligned lists. Returns (mean_diff, t, df, significant)."""
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    if n < 2: return (float("nan"),) * 3 + (False,)
    m = st.mean(d)
    sd = st.stdev(d)
    if sd == 0: return m, float("inf") if m else 0.0, n - 1, bool(m)
    t = m / (sd / math.sqrt(n))
    return m, t, n - 1, abs(t) > tcrit(n - 1)


def bootstrap_ci(d, iters=4000):
    if len(d) < 2: return (float("nan"), float("nan"))
    means = []
    for _ in range(iters):
        means.append(st.mean(random.choices(d, k=len(d))))
    means.sort()
    return means[int(.025 * iters)], means[int(.975 * iters)]


def fetch(results):
    """{regime: {question_id: {metric: value}}} from a ResultsRepository."""
    data = defaultdict(dict)
    for r in results.all_rated():
        m = json.loads(r["metrics"] or "{}")
        data[r["regime"]][r["question_id"]] = {
            "overall": r["overall"],
            "correctness": r["correctness"], "completeness": r["completeness"],
            "grounding": r["grounding"], "blind": r["blind"],
            "mean_weight": m.get("mean_weight"), "n_fragments": m.get("n_fragments"),
            "pct_highlighted": m.get("pct_highlighted"),
        }
    return data


def table(data, baseline, metric="correctness"):
    rows = []
    base = data.get(baseline, {})
    for regime, qs in sorted(data.items()):
        shared = [q for q in qs
                  if qs[q].get(metric) is not None
                  and base.get(q, {}).get(metric) is not None]
        vals = [qs[q][metric] for q in shared]
        if not vals:
            rows.append(dict(regime=regime, n=0)); continue
        bvals = [base[q][metric] for q in shared]
        diff, t, df, sig = paired_test(vals, bvals)
        lo, hi = bootstrap_ci(vals)
        rows.append(dict(regime=regime, n=len(vals), mean=st.mean(vals),
                         ci_lo=lo, ci_hi=hi, delta=diff, t=t, df=df,
                         sig="yes" if sig and regime != baseline else "—"))
    return rows


def show(rows, title, baseline):
    print(f"\n{title}   (baseline = {baseline})")
    print(f"  {'regime':<24}{'n':>4}{'mean':>8}{'95% CI':>16}{'Δ vs base':>11}{'t':>8}{'p<.05':>7}")
    print("  " + "-" * 78)
    for r in rows:
        if not r.get("n"):
            print(f"  {r['regime']:<24}{0:>4}   (no ratings yet)"); continue
        ci = f"[{r['ci_lo']:.0f}, {r['ci_hi']:.0f}]"
        print(f"  {r['regime']:<24}{r['n']:>4}{r['mean']:>8.1f}{ci:>16}"
              f"{r['delta']:>+11.1f}{r['t']:>8.2f}{r['sig']:>7}")


def by_group(data, baseline, metric="overall"):
    groups = sorted({q[0] for qs in data.values() for q in qs})
    print(f"\nMean {metric} by question group")
    header = f"  {'regime':<24}" + "".join(f"{g:>8}" for g in groups)
    print(header); print("  " + "-" * (24 + 8 * len(groups)))
    for regime, qs in sorted(data.items()):
        line = f"  {regime:<24}"
        for g in groups:
            vals = [v[metric] for q, v in qs.items()
                    if q.startswith(g) and v.get(metric) is not None]
            line += f"{st.mean(vals):>8.1f}" if vals else f"{'—':>8}"
        print(line)
    print("\n  Hypotheses per group are in evaluation/questions.py. Reporting per "
          "group matters:\n  the literature predicts metadata gains are uneven "
          "across question type, so a\n  single averaged number would hide the "
          "actual finding.")


def retrieval_table(data):
    print("\nRetrieval characteristics (no human rating needed)")
    print(f"  {'regime':<24}{'frags':>8}{'mean w':>9}{'highlighted':>13}")
    print("  " + "-" * 54)
    for regime, qs in sorted(data.items()):
        f = [v["n_fragments"] for v in qs.values() if v["n_fragments"] is not None]
        w = [v["mean_weight"] for v in qs.values() if v["mean_weight"] is not None]
        h = [v["pct_highlighted"] for v in qs.values() if v["pct_highlighted"] is not None]
        if not f: continue
        print(f"  {regime:<24}{st.mean(f):>8.1f}{st.mean(w):>9.2f}"
              f"{st.mean(h):>12.0f}%")



def blind_check(data):
    """How many ratings were blind? Unblinded self-rating is the first thing
    a jury questions, so the split belongs in the write-up."""
    blind = unblind = 0
    for qs in data.values():
        for v in qs.values():
            if v.get("correctness") is None:
                continue
            if v.get("blind"):
                blind += 1
            else:
                unblind += 1
    if blind or unblind:
        print(f"\nRatings: {blind} blind, {unblind} unblinded "
              f"({100*blind/max(blind+unblind,1):.0f}% blind). "
              f"Report this split in the methodology.")


def report(results, baseline="R0_baseline_fts", csv_path=None):
    data = fetch(results)
    if not data:
        print("No runs stored yet. Run:  python scripts/run_eval.py")
        return
    retrieval_table(data)
    rated = any(v.get("overall") is not None
                for qs in data.values() for v in qs.values())
    if not rated:
        print("\nNo ratings yet — rate the answers at http://localhost:5000/rag/rate")
        return
    for metric, title in (("overall", "Overall quality (primary metric)"),
                          ("correctness", "Answer correctness (diagnostic)"),
                          ("completeness", "Answer completeness (diagnostic)"),
                          ("grounding", "Grounding in retrieved fragments (diagnostic)")):
        show(table(data, baseline, metric), title, baseline)
    by_group(data, baseline)
    blind_check(data)
    if csv_path:
        rows = table(data, baseline)
        with open(csv_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
        print(f"\nWrote {csv_path}")
