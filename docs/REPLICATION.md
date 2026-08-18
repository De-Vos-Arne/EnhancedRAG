# Replication notes

Context for anyone reproducing this project against their own archive —
what it actually took, in wall-clock time and hardware, and what changes if
the source material differs from the one used here.

## Contents
- [The source archive](#the-source-archive)
- [Replicating against your own material](#replicating-against-your-own-material)
- [What the initial build actually took](#what-the-initial-build-actually-took)
- [Hardware](#hardware)
- [Models used](#models-used)
- [Sharing a redacted subset](#sharing-a-redacted-subset)
- [The evaluation report](#the-evaluation-report)

## The source archive

- Format: RightNote `.rnt` (see `docs/ARCHITECTURE.md` for the file
  format itself).
- Original file size: ~671MB uncompressed; ~500MB excluding embedded
  images. The current working copy measured 689.9MB.
- Scope: the archive contains many pages; **only one page, "Belief-System"
  (page_id 28), is in scope for this project.** This is enforced in code
  (`settings.SCOPE_PAGE_IDS`, checked in `repositories/corpus_repository.py`)
  — out-of-scope pages are never embedded, indexed, or retrievable, not
  merely excluded by convention.
- Within the in-scope page: 170,109 retrievable knowledge units across
  5,996 notes. 31.8% of units carry a highlight colour (54,042 of 170,109);
  the breakdown by colour:

  | Colour | Units | % of all units | % of highlighted |
  |---|--:|--:|--:|
  | Green | 21,313 | 12.53% | 39.4% |
  | Blue | 13,086 | 7.69% | 24.2% |
  | Yellow | 11,591 | 6.81% | 21.4% |
  | Orange | 7,415 | 4.36% | 13.7% |
  | Pink | 361 | 0.21% | 0.7% |
  | Purple | 149 | 0.09% | 0.3% |
  | (unmarked) | 116,067 | 68.2% | — |

## Replicating against your own material

The archive does not need to be RightNote-authored — RightNote is this
project's specific source format, not a requirement of the method. What the
pipeline actually needs, conceptually, is:
- a body of notes organized as a tree (or convertible to one),
- an optional per-line or per-note importance signal (here: highlight
  colour + bold), applied by the author over time, even informally and
  inconsistently,
- enough volume that naive retrieval alone struggles, which is the
  precondition for metadata-enhanced retrieval to have anything to prove.

Adapting the pipeline to a different source format means replacing
`pipeline/build_shadow_db.py` (the `.rnt`-specific extraction step) with
an equivalent for that format; everything downstream of the shadow DB
(`build_knowledge_units.py` onward) is format-agnostic.

**The archive does not need to be clean.** A meaningful part of this
project's premise is that messy, inconsistent, occasionally
self-contradictory personal notes are still usable, provided the retrieval
and generation layer is built to work with exactly that kind of material —
a sufficiently capable model, given the whole picture, is largely able to
understand and reason about a personal archive's own inconsistencies rather
than requiring them resolved first. Some amount of structural
cleanup (a database-integrity pass over the raw archive) was still needed
before the parsing pipeline could run reliably against it — see
`scripts/doctor.py` and `rnt_crud.py`'s own integrity-check methods for
what that pass checks.

## What the initial build actually took

Rough effort breakdown, for anyone estimating their own timeline:
- **The browser-based archive explorer** (tree navigation, note editing,
  CRUD against the live `.rnt` file without corrupting RightNote's own FTS3
  index) was the largest single piece of infrastructure work — reverse-
  engineering the file format and building a safe writer
  (`core/rnt_crud.py`) came before any retrieval work could start.
- **Data exploration** — building the treemap/chunk-size dashboard
  (`analysis/build_dashboard.py`) and inspecting real chunking output via
  the debug view (`/debug`) — took several days, and directly informed both
  the chunking method and an initial data-cleaning pass; not wasted time,
  effectively a required step before the chunking method could be trusted.
- **Encoding (embedding) the corpus**: roughly 6–7 hours per embedding
  variant, ×2 variants (plain and context-embedded, see
  `docs/ARCHITECTURE.md`), so roughly a full day end-to-end, for ~170,000
  fragments each — on the machine spec below.
- **Generating answers for the evaluation set** took a long time even on
  the local hardware below — generation time scales with regime (a 12-
  fragment regime answers in ~10–30s; R7's 229-fragment long-context
  control took 40–85s per answer) across 27–34 questions × 9 regimes.
- **Blind rating** took roughly a day and a half of active work across
  ~250 answers. Budget for this explicitly if reproducing the evaluation —
  it is the single largest manual-effort step in the whole pipeline, by
  design (an LLM judge was deliberately not used — see
  `evaluation/statistics.py`'s own note on this).

## Hardware

- GPU: 12GB VRAM (RTX 5070 Ti Laptop in this build)
- RAM: 32GB
- The 26B-parameter Q4 generator model does not fully fit in 12GB VRAM at
  any context length tested (model weights alone need ~17GB); some layers
  run from system RAM. This is the actual throughput ceiling observed
  (~20 tokens/sec), not a software limitation — see the generator's own
  settings comment in `settings.py` for the tradeoffs considered.

## Models used

- **Ollama**, embeddings only: `nomic-embed-text` throughout.
- **Generation, first choice**: `qwen2.5:7b-instruct` via Ollama — dropped
  during the project because its answer quality was not sufficient for the
  evaluation once the model comparison began; kept in `settings.py` as
  `local-qwen` for reference/fallback but is no longer the default.
- **Generation, used throughout the evaluation**: [Gemma4-26B-A4B-Uncensored-HauhauCS-Balanced](https://huggingface.co/HauhauCS/Gemma4-26B-A4B-Uncensored-HauhauCS-Balanced)
  (`Q4_K_P.gguf`, ~16GB), served locally via LM Studio, ~20 tokens/sec on
  the hardware above.
- **Why an uncensored model specifically**: the archive's own material
  ranges into rhetoric (authority, hierarchy, conquest framing as doctrinal
  content, not as the author's literal politics) that a safety-tuned model
  can refuse or hedge on out of context, which would otherwise require
  building separate refusal-detection and retry/fallback logic just to get
  a usable answer for evaluation. Using a model whose fine-tuning doesn't
  apply that filtering in the first place avoids that engineering
  entirely, and this specific model was additionally chosen for running at
  usable speed on a 12GB card. See `docs/ARCHITECTURE.md`'s note on the
  archive's F3 finding for another example of how the archive's own
  character shapes tooling decisions.
- Adding a further/alternate model: one `GENERATORS` entry — see
  `docs/INSTALL.md`.

## Sharing a redacted subset

Because the source archive contains personal material (credentials, private
identifying information, and page content well outside "Belief-System"),
only a filtered subset is intended to be shared publicly.

```bash
# whole in-scope page (still large — every note on Belief-System)
python scripts/make_subset_archive.py --out data/PersonalArchiveSubset.rnt --pages 28

# a specific small selection instead — one or more branches only, everything
# else on the page excluded. Find treenode_ids in the explorer.
python scripts/make_subset_archive.py --out data/PersonalArchiveSubset.rnt \
    --pages 28 --treenode-ids 11164 20531
```
This copies the original file (never modifies it in place), then deletes
everything outside the given page(s)/branches from the copy and reclaims
the freed space (`VACUUM`). Whole-page filtering alone took this project's
own archive from 689.9MB to 304MB — still all 6,924 notes on the in-scope
page, which is too much to publish as-is. Restricting to a handful of
specific branches with `--treenode-ids` instead produces a file sized to
whatever was actually selected — a single small branch tested at well
under 1MB — which is the intended route for actually publishing a small,
verified-clean sample.

**Whichever selection is used, read every note it contains before
publishing** — page-level or branch-level filtering only controls *scope*,
not content; it does not verify that what remains is free of sensitive
material. Before publishing, rename the file to `PersonalArchive.rnt` (the
default path this project's own scripts and docs expect), so the public
repository works with the default `RAG_ARCHIVE` setting with no
configuration.

**If the subset is edited further by hand in RightNote after generating
it** (deleting more notes manually, beyond what `--pages`/`--treenode-ids`
already filtered), run the FTS rebuild step again before sharing:
```bash
python scripts/make_subset_archive.py --rebuild-only --out data/PersonalArchiveSubset.rnt
```
This matters more than it looks: RightNote's FTS3 index does not
physically purge a deleted note's text on a normal delete, only marks it
gone — the old text can still sit in the index's own segment data,
recoverable, even though the note itself is gone from the visible tree.
This is exactly what happened once during this project's own subset
preparation: a file trimmed down to ~13 notes was still carrying ~570KB of
leftover searchable index data from the original several thousand,
invisible in the explorer/RightNote UI but still physically present in the
file. `--rebuild-only` forces a full FTS3 rebuild from the file's current
content plus a `VACUUM`, which is the only way to actually purge it — the
file dropped from over 300MB to under 100KB once run. Whichever tool was
used to delete notes, treat this as a required step before publishing, not
an optional cleanup.

**Why this particular subset exists at all**: it is deliberately reduced
to a small number of notes, not a representative sample of the archive —
enough to show the highlight-colour system, the tree structure, and how a
note looks in RightNote itself, without exposing the private, often messy
and sometimes internally inconsistent material the full archive actually
contains (some of it older, reflecting earlier and since-revised
thinking). It exists to make the file format and the pipeline concretely
inspectable, not to reproduce this project's results — see the next
paragraph.

Note explicitly, wherever the subset is published, that answers reproduced
against it should be expected to differ substantially from — and likely be
far worse than — the full private archive's results, since nearly all
supporting context is deliberately absent. The actual evaluation was run
against the complete, private archive; the answers and ratings from that
run are preserved in this repository regardless (`results/eval.db`, the
statistical tables, and the published report — see below), so the
evaluation's evidentiary record does not depend on the archive itself
being shared.

## The evaluation report

The full blind-rated comparison (9 regimes × 27 questions, statistical
tables, per-group breakdown, and the qualitative findings this project
surfaced) ships in this repo as a self-contained HTML file:
[`docs/report/evaluation_report.html`](report/evaluation_report.html).

Print-ready static figures from the same data are generated by
`analysis/generate_thesis_figures.py` into `results/figures/` — see
`docs/USER_GUIDE.md`.
