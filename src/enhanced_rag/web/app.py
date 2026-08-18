"""
The web application.

Two blueprints on one server:

  /            the archive explorer (browse and edit the .rnt)
  /rag         the retrieval bench (ask, compare regimes, rate answers)

They are independent — the explorer works without a built corpus, and the
bench works without the .rnt present. Mounting them together now means the
chat can move into the explorer's right-hand pane later without moving any
code.
"""

from __future__ import annotations

from flask import Flask, redirect, send_from_directory

from .. import settings


def create_app(archive_path=None, corpus_db=None, embedder=None,
               results_db=None) -> Flask:
    app = Flask(__name__, static_folder=str(settings.STATIC_DIR),
                static_url_path="/static")

    # Both blueprints load lazily and record why if they can't, so one
    # missing file never takes the whole server down.
    from .explorer import build_blueprint as build_explorer
    from .rag import build_blueprint as build_rag

    explorer_bp, explorer_error = build_explorer(archive_path or settings.ARCHIVE)
    rag_bp, rag_error = build_rag(corpus_db or settings.CORPUS_DB,
                                  embedder, results_db or settings.RESULTS_DB)
    app.register_blueprint(explorer_bp)
    app.register_blueprint(rag_bp, url_prefix="/rag")

    app.config["EXPLORER_ERROR"] = explorer_error
    app.config["RAG_ERROR"] = rag_error

    @app.route("/")
    def home():
        if explorer_error:
            return redirect("/rag/")
        return send_from_directory(app.static_folder, "explorer.html")

    @app.route("/debug")
    def debug_view():
        """Explorer variant with a toggleable section/block-break overlay,
        for inspecting how the parser splits a note — read-only, kept
        separate from the main explorer so it can't affect normal editing."""
        if explorer_error:
            return redirect("/rag/")
        return send_from_directory(app.static_folder, "explorer_debug.html")

    @app.route("/health")
    def health():
        return {"explorer": explorer_error or "ok", "rag": rag_error or "ok"}

    return app
