# Analysis & Evaluation

How the evaluation was run, scored, and turned into the published report
and figures.

## Contents
- [Pipeline](#pipeline)
- [The question set](#the-question-set)
- [Rating](#rating)
- [Statistics](#statistics)
- [Figures](#figures)
- [The published report](#the-published-report)

## Pipeline

```bash
python scripts/run_eval.py --questions     # list the question set
python scripts/run_eval.py --dry-run       # retrieval only, seconds — sanity check first
python scripts/run_eval.py                 # full run, resumable
python scripts/serve.py                    # then rate at /rag/rate
python scripts/analyse.py                  # statistical tables
python analysis/generate_thesis_figures.py # print-ready figures
```
Every step reads/writes `results/eval.db`. The dry run is worth doing
first: it prints a Jaccard overlap matrix of what each regime actually
retrieved, and two regimes overlapping above ~0.90 means comparing their
answers won't show anything, before any generation time is spent finding
that out.

## The question set

`evaluation/questions.py` — 27 questions (of an original 34; six were
trimmed to keep the rating workload manageable, see below), grouped by
*retrieval challenge*, not by topic:

| Group | Tests | Hypothesis |
|---|---|---|
| A — Factual/definitional | can the baseline already do this | metadata gives at most a modest edge here |
| B — Structural/hierarchical | does tree-path metadata matter | should show a clear edge |
| C — Synthesis | fragments span many sources | largest expected edge |
| D — Contrastive | distinguishing doctrine from comparison | metadata should stop conflation |
| E — Inferential | premises, not a single passage | hardest for the baseline |
| F — Implicit/distributed | no single passage has the answer | tests recall across scattered fragments |
| X — Stress/edge | out-of-scope handling, invented terms | characterises behaviour, not scored for the headline claim |

Reporting per group is the actual finding, not an averaged single number —
the literature predicts metadata gains are uneven across question type,
and the results bear that out (see the published report's group × regime
breakdown).

Dropped from the original 34: C7, F2, F4, X1, X3, X4 — trimmed to control
rating time (rating is manual and takes real hours; see
`docs/REPLICATION.md`) while keeping every group represented. C5 was
generated but not rated.

## Rating

Done through `/rag/rate` (see `docs/USER_GUIDE.md#5-rating-ui`). One
"Overall" score (0–100) per answer is the primary metric; a
correctness/completeness/grounding breakdown is optional and only stored
if actually filled in. Blind by default — answers shuffled, regime labels
hidden — with an explicit `blind` flag stored per rating so blind and open
ratings are never silently mixed in the reported numbers.

An LLM judge was deliberately not used to automate rating: an automated
judge cannot reliably score answers about an archive whose correct
interpretation depends on private, personal context the judge doesn't
have.

## Statistics

`evaluation/statistics.py`: paired t-tests against a baseline regime (no
scipy dependency — a small lookup table below n=30, normal approximation
above), bootstrap 95% confidence intervals, and per-group breakdowns.
Pairing matters here specifically because every regime answers the *same*
questions, which makes a paired test both valid and considerably more
sensitive than comparing independent means at this sample size.

## Figures

`analysis/generate_thesis_figures.py` reads `results/analysis_payload.json`
(a snapshot of `results/eval.db` computed during the analysis pass) and
writes to `results/figures/` (PNG 300dpi + SVG):

| File | Shows |
|---|---|
| `fig1_overall_by_regime` | mean overall quality per regime, with 95% CI |
| `fig2_group_regime_heatmap` | mean quality, question group × regime |
| `fig3_score_spread_by_regime` | full score distribution per regime (box plot) |
| `fig4_every_question_heatmap` | every rated question × regime, individually |
| `fig5_context_volume_vs_quality` | quality grouped by fragment-count band — deliberately a grouped comparison, not a fitted trend line, since fragment counts cluster into three discrete bands (12 fixed; ~18–60; ~230) rather than varying continuously, and a continuous fit across that gap would imply a relationship the data doesn't have samples to support |

## The published report

The full interactive write-up — headline numbers, the group × regime
heatmap, and the qualitative findings (a corrected premise on the
out-of-corpus question, a retrieval gap on the "intellectual spine"
question, a counter-example where more metadata produced a worse answer,
and a likely-lucky outlier) — is a self-contained HTML file in this repo:
[`docs/report/evaluation_report.html`](report/evaluation_report.html).
Open it directly in a browser; no server or network access needed.
