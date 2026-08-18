#!/usr/bin/env python3
"""Start the web server (archive explorer + retrieval bench).

    python scripts/serve.py
    python scripts/serve.py --port 5000 --archive data/PersonalArchive.rnt
"""
import argparse
import _bootstrap  # noqa: F401
from enhanced_rag import settings
from enhanced_rag.web.app import create_app

ap = argparse.ArgumentParser()
ap.add_argument("--archive", default=str(settings.ARCHIVE))
ap.add_argument("--corpus", default=str(settings.CORPUS_DB))
ap.add_argument("--results", default=str(settings.RESULTS_DB))
ap.add_argument("--embedder", default=None)
ap.add_argument("--host", default=settings.HOST)
ap.add_argument("--port", type=int, default=settings.PORT)
a = ap.parse_args()

app = create_app(a.archive, a.corpus, a.embedder, a.results)
for name, err in (("explorer", app.config["EXPLORER_ERROR"]),
                  ("retrieval bench", app.config["RAG_ERROR"])):
    print(f"  {name}: {'ready' if not err else 'UNAVAILABLE — ' + err}")
print(f"\n  explorer  http://{a.host}:{a.port}/")
print(f"  bench     http://{a.host}:{a.port}/rag/")
print(f"  rating    http://{a.host}:{a.port}/rag/rate\n")
# threaded=True: the bench opens one SSE stream per selected regime
# (/api/ask_stream) — single-threaded dev server would serialize them
# instead of letting them stream concurrently.
app.run(host=a.host, port=a.port, debug=False, threaded=True)
