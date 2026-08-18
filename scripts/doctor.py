#!/usr/bin/env python3
"""
Check everything and say what's wrong.

    python scripts/doctor.py

Run this first when something breaks. It checks each prerequisite in the
order the pipeline needs them and stops guessing on your behalf — every
failure comes with the exact command that fixes it.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

import _bootstrap  # noqa: F401

OK, WARN, BAD = "[ ok ]", "[warn]", "[FAIL]"
problems = []


def check(label, fn):
    try:
        status, detail = fn()
    except Exception as e:
        status, detail = BAD, f"{type(e).__name__}: {e}"
    print(f"{status}  {label:<28} {detail}")
    if status == BAD:
        problems.append(label)


def py_version():
    v = sys.version_info
    if v < (3, 10):
        return BAD, f"{v.major}.{v.minor} — needs 3.10+"
    return OK, f"{v.major}.{v.minor}.{v.micro}"


def packages():
    missing = [p for p in ("flask", "numpy", "requests")
               if not importlib.util.find_spec(p)]
    if missing:
        return BAD, f"missing: {', '.join(missing)} — pip install -r requirements.txt"
    return OK, "flask, numpy, requests"


def settings_ok():
    from enhanced_rag import settings
    return OK, f"root {settings.ROOT}"


def archive():
    from enhanced_rag import settings
    p = Path(settings.ARCHIVE)
    if not p.exists():
        return WARN, (f"no .rnt at {p} — the explorer won't work, "
                      f"the bench still will")
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        n = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        con.close()
        return OK, f"{p.name}, {n} notes"
    except Exception as e:
        return BAD, f"{p.name} unreadable: {e}"


def corpus():
    from enhanced_rag import settings
    p = Path(settings.CORPUS_DB)
    if not p.exists():
        return BAD, f"not built — run: python scripts/build_corpus.py"
    con = sqlite3.connect(p)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "knowledge_units" not in tables:
        con.close()
        return BAD, "no knowledge_units table — run: python scripts/build_corpus.py"
    n = con.execute("SELECT COUNT(*) FROM knowledge_units").fetchone()[0]
    con.close()
    return (OK if n else BAD), f"{n} fragments"


def keyword_index():
    from enhanced_rag import settings
    p = Path(settings.CORPUS_DB)
    if not p.exists():
        return WARN, "no corpus yet"
    con = sqlite3.connect(p)
    has = con.execute("SELECT COUNT(*) FROM sqlite_master "
                      "WHERE name='ku_fts'").fetchone()[0]
    con.close()
    if not has:
        return BAD, "missing — run: python scripts/build_index.py --fts-only"
    return OK, "ku_fts present"


def embeddings():
    from enhanced_rag import settings
    p = Path(settings.CORPUS_DB)
    if not p.exists():
        return WARN, "no corpus yet"
    con = sqlite3.connect(p)
    cols = {r[1] for r in con.execute("PRAGMA table_info(knowledge_units)")}
    if not {"embedding", "embedding_plain"} <= cols:
        con.close()
        return BAD, "columns missing — run: python scripts/build_index.py"
    a = con.execute("SELECT COUNT(*) FROM knowledge_units "
                    "WHERE embedding_plain IS NOT NULL").fetchone()[0]
    b = con.execute("SELECT COUNT(*) FROM knowledge_units "
                    "WHERE embedding IS NOT NULL").fetchone()[0]
    con.close()
    if not (a and b):
        return BAD, f"plain={a} context={b} — run: python scripts/build_index.py"
    return OK, f"plain={a} context={b}"


def ollama():
    import requests
    from enhanced_rag import settings
    try:
        r = requests.get(f"{settings.OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
    except Exception:
        return BAD, (f"unreachable at {settings.OLLAMA_URL} — run: ollama serve")
    names = [m["name"] for m in r.json().get("models", [])]
    emb = settings.embedder()["model"]
    gen = settings.generator()["model"]
    missing = [m for m in (emb, gen)
               if not any(n.startswith(m) for n in names)]
    if missing:
        return WARN, "not pulled: " + ", ".join(f"ollama pull {m}" for m in missing)
    return OK, f"{len(names)} models, {emb} + {gen} present"


def corpus_loads():
    from enhanced_rag.services.rag_service import RagService
    rag = RagService()
    s = rag.stats()
    return OK, (f"{s['fragments']} fragments, {s['highlighted']} highlighted, "
                f"{s['notes']} notes")


def retrieval_works():
    from enhanced_rag.services.rag_service import RagService
    rag = RagService()
    out = rag.answer("what is this archive about", "R0_baseline_fts",
                     retrieval_only=True)
    return (OK if out["fragments"] else WARN), \
        f"keyword baseline returned {len(out['fragments'])} fragments"


def results_db():
    from enhanced_rag import settings
    from enhanced_rag.repositories.results_repository import ResultsRepository
    r = ResultsRepository(settings.RESULTS_DB)
    s = r.summary()
    return OK, f"{s['total_runs']} runs, {s['total_rated']} rated"


print("\nEnhancedRAG — diagnostics\n" + "=" * 60)
check("python", py_version)
check("packages", packages)
check("settings", settings_ok)
print("-" * 60)
check("archive (.rnt)", archive)
check("corpus.db", corpus)
check("keyword index", keyword_index)
check("embeddings", embeddings)
print("-" * 60)
check("ollama", ollama)
check("corpus loads", corpus_loads)
check("retrieval", retrieval_works)
check("results db", results_db)
print("=" * 60)

if problems:
    print(f"\n{len(problems)} problem(s): {', '.join(problems)}")
    print("Fix them top-down — later checks depend on earlier ones.")
    sys.exit(1)
print("\nAll good. Start the server:  python scripts/serve.py")
