# User Guide

Every tool in this project: what it's for, the exact command or URL to
reach it, and its features. Screenshot filenames referenced below match
what's requested in `docs/SCREENSHOTS.md`; none are committed yet.

## Contents
1. [Archive Explorer](#1-archive-explorer) (+ Debug view)
2. [Retrieval Bench](#2-retrieval-bench)
3. [Bulk Export](#3-bulk-export)
4. [Clipboard tool](#4-clipboard-tool)
5. [Rating UI](#5-rating-ui)
6. [MCP server](#6-mcp-server)
7. [Analysis tools](#7-analysis-tools)

All web tools run from one server:
```bash
python scripts/serve.py
```

---

## 1. Archive Explorer

**URL:** `http://localhost:5000/`

**What it's for:** browsing and editing the `.rnt` archive itself without
RightNote installed, and — during this project's development — the only
way notes were created, retagged with highlight colours, or reorganized
without risking the archive's own full-text index. Useful any time the
archive needs to change: fixing a note, checking what's actually stored
under a given branch, or re-tagging highlight colours to test how that
changes retrieval.

**Features:**
- Lazy-loaded tree navigation (`/api/tree/<page>[/<treenode>]`), expand-state
  preserved across edits so the tree doesn't collapse every save
- Note editing with the archive's highlight-colour system as inline
  formatting, saved back to RTF
- Full-text search over the archive (FTS3, the format's native index)
- Colour tagging per note or per treenode background
- Create, rename, move, delete notes and tree branches
- All writes go through `core/rnt_crud.py`, which keeps the FTS3 index in
  sync — see `docs/ARCHITECTURE.md` for why this matters

![explorer](screenshots/explorer.png)

### 1.1 Debug view

**URL:** `http://localhost:5000/debug`

**What it's for:** the same explorer, read-only, with a "Sections" toggle
that overlays the RTF parser's block/section break tokens (`[BR2]`,
`[BR3]`) directly on the note text — built to check whether a given note is
actually being chunked the way the parser claims, without a separate
debugger or log. Used to design and validate the chunking method (see
`docs/ARCHITECTURE.md`).

![debug view](screenshots/explorer_debug.png)

---

## 2. Retrieval Bench

**URL:** `http://localhost:5000/rag/`

**What it's for:** asking a question and comparing how different retrieval
regimes answer it, side by side, with the exact prompt each one built
visible for inspection. This is the tool the regime comparisons in the
evaluation report were explored through before the formal evaluation run.

**Before using it — the generator must actually be reachable.** `python
scripts/serve.py` starts the web server only; it does not start or load a
model. Whichever generator is selected needs its own backend running
first:
- **LM Studio** (default, `lmstudio`/`lmstudio-2`): the LM Studio *app*
  being open is not enough — its API server is a separate toggle. Also
  check for duplicate loaded instances first — a stale extra instance of
  the same model at the wrong context length can silently answer instead
  of the one just (re)loaded, truncating longer answers with no error.
  ```bash
  lms ps                                                       # check what's loaded first
  lms unload --all                                             # clear any stale/duplicate instances
  lms server start
  lms load gemma4-26b-a4b-uncensored-hauhaucs-balanced-q4_k_p -c 16384 -y
  lms ps                                                        # confirm exactly one instance, ctx 16384
  ```
- **Ollama** (`local-qwen`): `ollama serve` (and the model must be pulled:
  `ollama pull qwen2.5:7b-instruct`).

If a generator is unreachable, the bench now shows the exact fix command
in the error message itself rather than a bare connection trace — but
running the commands above before a demo avoids seeing that message at
all.

**Features:**
- Multi-regime comparison in one request, streamed independently per
  column so a slow regime doesn't block the others
- Generator picker (any entry in `settings.GENERATORS`)
- Context-size controls: raw fragment count, or "fill N% of context window"
- Organic-prose vs. cited-fragment answer style toggle
- "Show system prompt" panel — the exact prompt sent
- "Copy context" — the built prompt, for pasting into an external chat
- Retrieval-only mode (inspect fragments and scores without generating)

![retrieval bench](screenshots/bench.png)

---

## 3. Bulk Export

**URL:** `http://localhost:5000/rag/export`

**What it's for:** the practical, everyday-use half of this project —
pulling a large, thematically coherent slice of the archive out for
pasting into an external big-context chat (Claude, ChatGPT, Gemini)
instead of running everything through the local pipeline. This is how a
stronger, non-local model gets used on the archive without giving it
programmatic access.

**Colour + budget export:**
- Per-colour checkboxes with live unit/character counts, plus two extra
  tiers — bold-but-uncoloured text, and any uncoloured text — for branches
  never highlighted
- Character-budget slider with context-window presets (~8K/32K/128K/1M/none)
- Tiered fill order: purple+pink always included first, then blue, green,
  yellow, orange, then the two uncoloured tiers, each filling whatever
  budget the previous tier left (see `docs/ARCHITECTURE.md` for the
  general mechanism this is one instance of)
- Adaptive fallback: a branch with zero hits under the checked colours
  falls back to bold-then-any uncoloured text from that branch, instead of
  returning nothing
- Optional front-matter: a framing paragraph and/or the colour-tag legend,
  prepended to the export for a chat with no other context on the archive

**Tree / branch selector:**
- The explorer's own lazy-loaded accordion, checkbox per branch, colour
  swatch per node
- Colour-filter dropdown dims branches not tagged the selected colour
- A 👁 preview icon shows a branch's content before checking it in
- "Sort by recency" — flat, newest-first instead of tree-outline order

**Curate for a question:**
- Given a topic, pools keyword (FTS) and vector retrieval to find relevant
  *notes*, then exports each match's full tiered content — not just the
  matched line — so the surrounding context comes along. The manual
  version of the `R8_pooled_curated` evaluation regime.

**Summarize selection:**
- Sends the tiered content to a generator (default: the local uncensored
  model) for a short "essence" summary — themes, recurring style, standout
  quotes.

![bulk export](screenshots/export.png)

---

## 4. Clipboard tool

**Command:**
```bash
pip install pywin32 keyboard pystray Pillow
python tools/clipboard_tool.py
```

**What it's for:** a Windows system-tray shortcut for the same
colour-tagging conversion as the Bulk Export tool, without opening a
browser — copy from RightNote, hit a hotkey, paste the tagged plain text
somewhere else.

**Features:**
- `Ctrl+Shift+V` — convert the clipboard (copied from RightNote) into
  plain text tagged `[BLU]...[/BLU]` etc.
- `Ctrl+Shift+M` — settings menu: which colours to include, whether to
  keep unmarked text, an editable preamble explaining the tag system
- `Ctrl+Shift+Q` — quit
- Settings persist to `tools/clipboard_tool_config.json`

**On Word documents:** confirmed working — the conversion operates on RTF
highlight-colour spans read off the Windows clipboard, and RTF
highlighting is the same underlying mechanism Word uses. Word's own
highlight palette doesn't line up hex-for-hex with RightNote's, so not
every colour maps cleanly out of the box: **green and yellow convert
reliably** (Word's standard green/yellow highlights match this project's
`HIGHLIGHT_COLOR_MAP` closely enough), which is enough for a simple
"good / needs work" two-colour scheme on Word text. The rest of Word's
highlight colours are not guaranteed to map to the intended tag. Extending
the colour map to Word's exact palette is a small, contained fix — see
`docs/ARCHITECTURE.md`'s "Adapting the colour system" for where — just not
done here, since it was out of scope for this project's own archive.

![clipboard tool](screenshots/clipboard_tool.png)

---

## 5. Rating UI

**URL:** `http://localhost:5000/rag/rate`

**What it's for:** scoring every regime's answer to a question, blind or
open, one question at a time — the tool the published evaluation was
actually rated through.

**Features:**
- One card per regime per question, in a grid
- Blind mode (default): answers shuffled, labels hidden — the mode any
  thesis claim should be backed by
- Open mode: labels visible, stable order, for inspecting one regime's
  behaviour; stored and reported separately from blind ratings
- A single "Overall" score (0–100) is the primary metric; a "breakdown"
  panel (correctness / completeness / grounding) is optional and only
  recorded if opened
- Free-text notes field per card
- "Jump to question" — revisit and re-score any question; saving again
  overwrites the previous score
- `python scripts/run_eval.py --only <id> --regimes <key> --redo`
  regenerates one specific answer without rerunning the whole set

![rating ui](screenshots/rate.png)

---

## 6. MCP server

**Command:**
```bash
python scripts/mcp_server.py
```

**What it's for:** letting a connected AI client (Claude Desktop, or any
MCP-capable agent) query the archive live and on demand, instead of the
archive being pasted or uploaded in bulk.

**Tools exposed:**
| Tool | Purpose |
|---|---|
| `archive_stats` | corpus size, fragment counts, embedding coverage |
| `colour_system` | the colour/weight legend |
| `list_regimes` | available retrieval regimes and what each adds |
| `search` | raw retrieval for a query under one regime, no generation |
| `ask` | retrieve + generate an answer under one regime |
| `compare_regimes` | the same query answered under several regimes at once |
| `branch_summary` | the "essence" summary of a branch/colour selection |
| `bulk_export` | the full tiered text of a branch or selection |
| `evaluation_questions` | the thesis question set, optionally filtered by group |

Nothing exposed writes to the archive or the corpus.

---

## 7. Analysis tools

**Interactive dashboard:**
```bash
python analysis/export_for_viz.py     # corpus.db -> analysis/viz_data_page28.json
python analysis/build_dashboard.py    # -> analysis/dashboard.html
python -m http.server                 # from analysis/, then open the page
```
An interactive treemap of the note tree plus a chunk-size / colour
distribution breakdown. Used to design and sanity-check the chunking
method against the real archive — see `docs/ARCHITECTURE.md`.

![analysis dashboard](screenshots/analysis_dashboard1.png)
![analysis dashboard](screenshots/analysis_dashboard2.png)
**Print-ready evaluation figures:**
```bash
python analysis/generate_thesis_figures.py
```
Reads `results/analysis_payload.json` and writes PNG (300dpi) + SVG figures
to `results/figures/` — see `docs/ANALYSIS.md` for what each figure shows
and how to regenerate the underlying data.

**Statistics:**
```bash
python scripts/analyse.py
python scripts/analyse.py --csv results/table.csv
```
Paired significance tests and per-group tables from `results/eval.db` —
see `docs/ANALYSIS.md`.
