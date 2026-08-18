# Architecture

How the project is put together, why it's laid out this way, and the
specific technical decisions that shaped the retrieval pipeline.

## Contents
- [Layering](#layering)
- [Data flow: archive to answer](#data-flow-archive-to-answer)
- [Chunking](#chunking)
- [The colour system — the semantic layer](#the-colour-system--the-semantic-layer)
- [The regime matrix — the experiment design](#the-regime-matrix--the-experiment-design)
- [Salience-tiered context budgeting](#salience-tiered-context-budgeting)
- [Adapting the colour system](#adapting-the-colour-system)
- [RightNote's file format, reverse-engineered](#rightnotes-file-format-reverse-engineered)
- [The databases this project creates](#the-databases-this-project-creates)
- [Two embedding variants — measured effect](#two-embedding-variants--measured-effect)

## Layering

```
web/            Flask routes only — no SQL, no retrieval logic
services/       orchestration and business logic — no Flask, no raw SQL
repositories/   all SQL lives here
core/           domain logic with no I/O beyond the archive file itself
```

Repositories own SQL, services own logic, the web layer owns HTTP. This
means the retriever can be tested without a server, the bench can run
without the archive present, and swapping the storage layer later touches
one directory rather than the whole codebase.

```
src/enhanced_rag/
├── settings.py            paths, model presets, the regime matrix, scope
├── mcp_server.py          MCP tools (read-only)
├── core/
│   ├── colours.py           THE colour/weight system — single source of truth
│   ├── rtf_parser.py        RTF -> spans with colour and bold
│   └── rnt_crud.py          the only sanctioned writer to the .rnt
├── repositories/
│   ├── corpus_repository.py   knowledge units + vectors, scope-filtered
│   └── results_repository.py  eval runs + ratings
├── services/
│   ├── models.py             embedder + generator clients, swappable
│   ├── indexer.py            builds FTS and embeddings
│   ├── retrieval.py          the regime-driven retriever
│   ├── rag_service.py        orchestration — the thing everything calls
│   └── export_service.py     tiered bulk export, curation, summarization
├── evaluation/
│   ├── questions.py          the question set, grouped with hypotheses
│   ├── runner.py             runs regimes x questions
│   └── statistics.py         paired tests, per-group tables
├── pipeline/                 one-off build stages (archive -> corpus)
└── web/                      Flask app, explorer + bench + export routes
```

## Data flow: archive to answer

```
PersonalArchive.rnt (RightNote's own SQLite file)
        │  pipeline/build_shadow_db.py
        ▼
   shadow db (flattened notes + treenodes + colour/date metadata)
        │  pipeline/build_knowledge_units.py
        ▼
   corpus.db: knowledge_units table (one row per retrievable line)
        │  services/indexer.py  (FTS5 + two embedding variants)
        ▼
   corpus.db: FTS5 index + vector tables ("plain" and "context" embeddings)
        │  services/retrieval.py  (regime-driven retriever)
        ▼
   ranked fragments  ──►  services/rag_service.py  ──►  generator  ──►  answer
```

Every retrievable line carries: its text, its highlight colour, whether it
was bold, its position in the tree (`tree_path`), its parent note's
caption, and an `effective_weight` derived from colour + bold + treenode
marking (`core/colours.py`).

## Chunking

Chunking is line-level, not paragraph- or note-level: each highlighted or
structurally distinct line becomes one `knowledge_unit`. This was decided
empirically, not assumed — several days of exploring the parsed archive
through the treemap/chunk-size dashboard (`analysis/build_dashboard.py`)
showed that note-level chunks were far too coarse (a single note can run to
thousands of characters spanning many unrelated points) and that the
RTF parser's own block/section break tokens (`[BR2]`, `[BR3]`) already mark
natural boundaries reliably enough to chunk on directly. The debug explorer
view (`/debug`) exists specifically to let those boundaries be inspected
against the live editor while this was being tuned.

## The colour system — the semantic layer

`core/colours.py` is the single source of truth for the weight mapping used
everywhere else (parser, retriever, prompt builder, export tools, MCP
tools):

| Colour | Weight | Meaning |
|---|---|---|
| Purple | 5.0 | standout / rare peak |
| Pink | 4.0 | exceptional |
| Blue | 3.0 | excellent / high-salience |
| Green | 2.0 | good / validated |
| Yellow | 1.0 | noteworthy / provisional |
| Orange | 0.5 | corrective / needs revision |

Bold adds +0.5; an explicitly coloured treenode (the author marking a whole
branch, not just a line) adds a further +0.5. This mapping is informal by
construction — it encodes years of ad hoc human judgement, not a fixed
rulebook, and both the prompt given to generators and the export tools say
so explicitly rather than presenting it as ground truth.

**Known limitation, confirmed empirically (see the evaluation report's F3
finding):** the author's own highlighting intensity was not stationary over
the archive's history — colour usage escalated over time, so older
foundational material can carry systematically lower weight than newer
material saying less. Weight-driven retrieval inherits this bias. This is a
property of the archive's own annotation history, not of the retrieval
method itself.

## The regime matrix — the experiment design

`settings.REGIMES` defines nine retrieval configurations, each changing
essentially one variable relative to the previous, so a quality difference
is attributable to that one change rather than several compounding at once:

| Key | Retriever | What it isolates |
|---|---|---|
| R0 | keyword (BM25/FTS) | lexical baseline, no embeddings, no metadata |
| R1 | vector, plain-text embedding | dense baseline, the RAG control |
| R2 | R1 + colour-weight in ranking | does weighting alone help |
| R3 | R1 + tree-path in the embedding | does structural context alone help |
| R4 | R2 restricted to highlighted-only pool | does filtering to marked content alone help |
| R5 | R2 + colour tags/paths shown to the generator | does prompt-visible metadata alone help |
| R6 | everything above + note-level expansion | full metadata fusion |
| R7 | none — top-weighted fragments stuffed in | raw long-context control from the literature |
| R8 | pooled keyword + vector, then expansion | the manual "curate for a question" workflow, automated |

R6's "note-level expansion" pulls high-weight neighbouring lines from each
hit's parent note — line-level retrieval is precise but sometimes too small
a unit to answer from on its own; expansion restores local context without
falling back to whole-note retrieval, which would reintroduce the noise the
line-level index exists to avoid.

## Salience-tiered context budgeting

The mechanism behind both the bulk-export tool and the `R8` regime — fill a
character/token budget in strict priority order by an author-assigned
salience tier (purple+pink always first, then blue, then green, and so on,
each tier only spending what the previous left) — is a specific,
identifiable technique, distinct from "retrieval with weights" in general.
It differs from standard top-k retrieval, which ranks by a single
continuous score, and from undifferentiated long-context stuffing (R7,
which has no priority structure at all): it is a **graded, cascading
budget allocation** driven by a human-assigned categorical importance
signal rather than a learned or continuous one.

This is named here as **salience-tiered context budgeting**: closest to
what the retrieval literature calls a cascading or graded retrieval
budget, applied specifically to human-authored categorical salience
(highlight colour) rather than a model-computed relevance score — the
combination of a strict categorical priority order with a hard character
budget, rather than either alone, is the specific mechanism.

## Adapting the colour system

Everything downstream depends on `core/colours.py`'s colour → weight
mapping and the `[TAG]` strings it produces — changing to a different
personal colour system (different hues, a different number of tiers, a
different weighting) means editing exactly two places:

1. **`core/colours.py`** — the `COLOURS` list (hex, weight, tag, meaning
   per colour) and the bonus constants (`BOLD_BONUS`, `TREENODE_BONUS`).
   Every other module imports from here rather than hardcoding a mapping,
   so this is the single edit point for the weighting scheme itself.
2. **`core/rtf_parser.py`**'s `HIGHLIGHT_COLOR_MAP` / `HEX_COLOR_MAP` — the
   RGB/hex values the parser matches against the *source* format's actual
   highlight colours, which is a separate concern from what `colours.py`
   calls those colours semantically. If the source archive uses different
   highlight hex values than this project's RightNote archive did, this
   is the mapping that needs new entries.

Everything that describes the colour system to a generator or in an
export (`colours.legend()`, the tag legend in export tools, the clipboard
tool's preamble) reads from `colours.py` at call time, so a change there
propagates everywhere without hunting down separate copies of the legend
text.

## RightNote's file format, reverse-engineered

`.rnt` files are SQLite databases. Three tables matter for this project:
`notes` (content + metadata), `treenodes` (the tree structure, one row per
node, linking to a `note_uid`), and `contents` (the packed RTF payload).
RightNote compresses `contents.data` with zlib by default; this project
disables that compression in RightNote's own settings before working with
an archive, trading disk space for not needing to zlib-decompress on every
single read during parsing and re-parsing — a meaningful difference at
archive sizes in the hundreds of thousands of fragments (see
`docs/REPLICATION.md`).

RightNote's own full-text index (`notes_fts`, FTS3) is fragile: writing to
it with plain `INSERT`/`DELETE` corrupts the search segments. `rnt_crud.py`
is the only sanctioned writer to the `.rnt` file specifically because it
routes every content change through `notes_fts_content` (a shadow content
table) and calls FTS3's `'rebuild'` special command afterward, which is the
only reliable way to keep the index consistent with the content.

## The databases this project creates

| File | Built by | Contains |
|---|---|---|
| `data/PersonalArchive.rnt` | (external — RightNote / the source archive) | the raw archive; read and written directly by `rnt_crud.py` |
| `data/corpus.db` | `pipeline/` scripts | `knowledge_units` (chunked, scope-filtered, colour/weight-tagged retrievable lines), FTS5 index, two embedding-vector tables |
| `results/eval.db` | `evaluation/runner.py` via `run_eval.py` | `runs` (one row per question × regime × generator answer, with retrieval metrics as JSON) and `ratings` (human scores, keyed to a run, with a `blind` flag) |

`corpus.db` and `eval.db` are both git-ignored (`data/`, `results/`) since
they're derived from the private archive and from the (currently
unblinded-to-nobody-but-the-rater) evaluation runs.

## Two embedding variants — measured effect

Every fragment is embedded twice: **plain** (text only) and **context**
(text plus its breadcrumb tree-path prepended). R1 (plain) and R3 (context)
are otherwise identical regimes, so the difference between them isolates
this one choice: in the current evaluation run, R1 averaged 40.0 overall
quality and R3 averaged 47.9 — a +7.9 point (≈20% relative) improvement
from embedding the tree-path alongside the text, with no other change. See
`results/table.csv` for the full paired-test numbers.
