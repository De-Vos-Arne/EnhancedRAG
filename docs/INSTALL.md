# Installation

## Prerequisites

| Tool | Why | Link |
|---|---|---|
| Python 3.11+ | runs the whole project | (system package manager) |
| Ollama | embeddings (always), and optionally a local generator | https://ollama.com/download |
| LM Studio | recommended local generator backend | https://lmstudio.ai/ |
| RightNote | only needed to *author or browse* the source `.rnt` archive outside this project; the project reads and writes `.rnt` files directly and does not require RightNote to be installed to run | https://www.bauerapps.com/rightnote/ |

RightNote is a hierarchical note-taking application — conceptually closer to a
personal wiki or a tree-structured library than to a single Word document: it
stores an effectively unlimited number of notes in one file, organized as a
tree, and is built for finding your way through a much larger body of
material than a single document would hold. This project's explorer
(`/`) reimplements enough of RightNote's editing surface (tree navigation,
note editing, highlight colours) to work directly against the `.rnt` file, so
day-to-day use of this project does not require the RightNote application —
only the file format.

## Install the Python project

Pick one:

```bash
# uv (fastest)
uv sync

# pip
python -m venv .venv && .venv\Scripts\activate    # or: source .venv/bin/activate
pip install -r requirements.txt

# Docker
docker compose up --build
```

## Set up the model backends

**Ollama** (required — embeddings always go through it):
```bash
ollama serve
ollama pull nomic-embed-text
```

**LM Studio** (recommended generator — a local, uncensored model that avoids
refusal/false-positive handling on charged material; see `docs/REPLICATION.md`
for why uncensored specifically):
1. Install from https://lmstudio.ai/
2. Download a GGUF model. The model used throughout this project is
   [Gemma4-26B-A4B-Uncensored-HauhauCS-Balanced](https://huggingface.co/HauhauCS/Gemma4-26B-A4B-Uncensored-HauhauCS-Balanced)
   (`Q4_K_P.gguf`, ~16GB on disk). Any OpenAI-compatible local model works —
   this one was chosen for running acceptably fast (~20 tokens/sec observed)
   on a 12GB-VRAM / 32GB-RAM laptop.
3. Start the local server and load the model — **the app running is not
   enough**, the local server is a separate toggle. Check for stale/
   duplicate loaded instances first — the base model identifier routes to
   whichever instance loaded it first, so a leftover instance at the wrong
   context length can silently answer instead of the one just reloaded,
   with truncated output and no error:
   ```bash
   lms ps
   lms unload --all
   lms server start
   lms load gemma4-26b-a4b-uncensored-hauhaucs-balanced-q4_k_p -c 16384 -y
   lms ps    # confirm exactly one instance, context 16384
   ```
   (`lms` ships in LM Studio's install; on Windows it's typically at
   `%USERPROFILE%\.lmstudio\bin\lms.exe`.)

**Adding your own model** — one dict entry in `settings.py`'s `GENERATORS`:
```python
"my-model": dict(
    label="My Model", backend="openai",  # "openai" = any OpenAI-compatible
                                          # chat-completions endpoint (LM
                                          # Studio, OpenRouter, a hosted
                                          # API); "ollama" for an Ollama model
    model="the-exact-model-id",
    base_url="http://localhost:PORT/v1",
    api_key="...",                       # any non-empty string for local servers
    context_window=16384,
    gen_params=dict(),                   # optional sampling overrides
),
```
Then `--generator my-model` on any script, or pick it from the bench's
generator dropdown. `RAG_GENERATOR=my-model` in `.env` makes it the default.

## Get an archive

Put your `.rnt` file at `data/PersonalArchive.rnt` (or set `RAG_ARCHIVE` in
`.env` to point elsewhere). See `docs/REPLICATION.md` for what "convert your
own archive" means if your source material isn't already a RightNote file —
in short, anything that can be expressed as a tree of notes with an optional
highlight-colour-as-importance signal per line can be encoded the same way;
RightNote itself is only this project's specific source format.

Set which page(s) are in scope — **critically important if your archive
contains anything you don't want touched**:
```bash
# .env
RAG_SCOPE_PAGE_IDS=28        # comma-separated page_ids; this is enforced in
                              # code (repositories/corpus_repository.py), not
                              # just a convention — out-of-scope pages are
                              # never embedded, indexed, or retrievable.
```

## Build the corpus and index

```bash
python scripts/build_corpus.py     # .rnt -> data/corpus.db  (slow, once)
python scripts/build_index.py      # keyword index + embeddings  (slow, once)
```
Both are one-off and cached — see `docs/REPLICATION.md` for how long this
actually takes on real archive sizes.

## Run

```bash
python scripts/serve.py
```
- `http://localhost:5000/` — archive explorer
- `http://localhost:5000/debug` — explorer + parser section-break overlay
- `http://localhost:5000/rag/` — retrieval bench
- `http://localhost:5000/rag/export` — bulk export / curation tool
- `http://localhost:5000/rag/rate` — blind rating UI

`python scripts/doctor.py` diagnoses the most common setup problems (missing
archive, missing index, wrong embedder dimensions, Ollama unreachable) before
you go looking through logs.

See `docs/USER_GUIDE.md` for what each of these pages actually does, and the
project root `README.md` for the full repo map.
