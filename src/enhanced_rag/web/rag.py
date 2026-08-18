"""
Retrieval bench — ask, compare regimes side by side, and rate answers.

Rating supports two modes:
  blind    regime labels hidden, answers shuffled (the default; this is
           what should back any claim in the thesis)
  open     labels shown, so you can inspect a specific regime's behaviour
           without the ceremony

Which mode a rating was given under is stored with it, so the write-up can
report the split honestly rather than implying everything was blind.
"""

from __future__ import annotations

import json
import random

from flask import Blueprint, Response, jsonify, request, send_from_directory

from .. import settings
from ..core import colours
from ..evaluation import questions as Q
from ..repositories.results_repository import ResultsRepository
from ..services.models import ModelError
from ..services.rag_service import NARRATIVE_NOTE
from ..services import export_service

bp = Blueprint("rag", __name__)

SERVICE = None      # RagService, or None if the corpus isn't built yet
RESULTS = None
INIT_ERROR = None


def build_blueprint(corpus_db, embedder_name, results_db):
    """Returns (blueprint, error_message_or_None)."""
    global SERVICE, RESULTS, INIT_ERROR
    RESULTS = ResultsRepository(results_db)
    try:
        from ..services.rag_service import RagService
        SERVICE = RagService(corpus_db, embedder_name)
    except Exception as e:
        INIT_ERROR = str(e)
        return bp, INIT_ERROR
    return bp, None


def _need_service():
    if SERVICE is None:
        return jsonify({"error": INIT_ERROR or "The corpus is not loaded."}), 503
    return None


# ── pages ──────────────────────────────────────────────────────────────

@bp.route("/")
def chat_page():
    return send_from_directory(settings.STATIC_DIR, "bench.html")


@bp.route("/export")
def export_page():
    return send_from_directory(settings.STATIC_DIR, "export.html")


@bp.route("/rate")
def rate_page():
    return send_from_directory(settings.STATIC_DIR, "rate.html")


# ── metadata ───────────────────────────────────────────────────────────

@bp.route("/api/config")
def api_config():
    return jsonify({
        "regimes": [{"key": k, **{f: v[f] for f in
                                  ("label", "blurb", "retriever", "top_k",
                                   "min_weight", "weight_alpha", "prompt_meta")}}
                    for k, v in settings.REGIMES.items()],
        "default_regimes": settings.DEFAULT_REGIMES,
        "generators": [{"key": k, "label": v["label"],
                        "ready": v["backend"] == "ollama" or bool(v["api_key"]),
                        "context_window": v.get("context_window", 8192)}
                       for k, v in settings.GENERATORS.items()],
        "default_generator": settings.DEFAULT_GENERATOR,
        "colours": colours.DISPLAY_HEX,
        "legend": colours.legend(markdown=True),
        "system_prompt": settings.SYSTEM_PROMPT,
        "narrative_note": NARRATIVE_NOTE,
        "error": INIT_ERROR,
    })


@bp.route("/api/stats")
def api_stats():
    if (bad := _need_service()):
        return bad
    return jsonify(SERVICE.stats())


# ── tiered bulk export ───────────────────────────────────────────────────

@bp.route("/api/export/stats")
def api_export_stats():
    if (bad := _need_service()):
        return bad
    page_id = settings.SCOPE_PAGE_IDS[0] if settings.SCOPE_PAGE_IDS else 28
    return jsonify({"colours": export_service.colour_stats(SERVICE.repo.con),
                    "page_id": page_id})


@bp.route("/api/export/build", methods=["POST"])
def api_export_build():
    if (bad := _need_service()):
        return bad
    body = request.get_json(force=True, silent=True) or {}
    colors = body.get("colors") or ["u", "p", "b"]
    budget = int(body.get("budget_chars") or 0)
    treenode_ids = body.get("treenode_ids") or None
    sort_recency = bool(body.get("sort_recency"))
    include_legend = bool(body.get("include_legend"))
    include_intro = bool(body.get("include_intro"))
    note_uids = None
    if treenode_ids:
        page_id = settings.SCOPE_PAGE_IDS[0] if settings.SCOPE_PAGE_IDS else 28
        note_uids = export_service.resolve_subtree_note_uids(
            SERVICE.repo.con, page_id, [int(t) for t in treenode_ids])
        if not note_uids:
            return jsonify({"error": "Selected tree nodes have no notes under them."}), 400
    try:
        result = export_service.export(SERVICE.repo.con, colors, budget_chars=budget,
                                        note_uids=note_uids, sort_recency=sort_recency,
                                        adaptive_fallback=bool(note_uids),
                                        include_legend=include_legend, include_intro=include_intro)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@bp.route("/api/export/preview", methods=["POST"])
def api_export_preview():
    """Read-only peek at a tree branch's content before checking it into
    the export selection — every colour, small char cap."""
    if (bad := _need_service()):
        return bad
    body = request.get_json(force=True, silent=True) or {}
    treenode_ids = body.get("treenode_ids") or []
    if not treenode_ids:
        return jsonify({"error": "treenode_ids required"}), 400
    page_id = settings.SCOPE_PAGE_IDS[0] if settings.SCOPE_PAGE_IDS else 28
    note_uids = export_service.resolve_subtree_note_uids(
        SERVICE.repo.con, page_id, [int(t) for t in treenode_ids])
    if not note_uids:
        return jsonify({"error": "No notes under this branch."}), 400
    return jsonify(export_service.preview_note_uids(SERVICE.repo.con, note_uids))


@bp.route("/api/export/curate", methods=["POST"])
def api_export_curate():
    """Question-driven curation: retrieve which notes are relevant to a
    topic (pooling keyword + vector regimes), then export their full
    tiered content — the "collect everything useful for X, hand it to a
    smarter model" tool."""
    if (bad := _need_service()):
        return bad
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Type a question or topic first."}), 400
    colors = body.get("colors") or None
    budget = int(body.get("budget_chars") or 150000)
    include_legend = bool(body.get("include_legend"))
    include_intro = bool(body.get("include_intro"))
    try:
        result = export_service.curate_for_question(
            SERVICE, query, colors=colors, budget_chars=budget,
            include_legend=include_legend, include_intro=include_intro)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@bp.route("/api/export/summarize", methods=["POST"])
def api_export_summarize():
    """LLM-written 'essence' summary of a colour+branch selection — themes,
    style, standout quotes — instead of the raw tiered dump. Defaults to
    the local uncensored generator so charged material in this archive
    (rhetoric-of-conquest passages etc.) doesn't routinely trip a refusal."""
    if (bad := _need_service()):
        return bad
    body = request.get_json(force=True, silent=True) or {}
    colors = body.get("colors") or ["u", "p", "b"]
    treenode_ids = body.get("treenode_ids") or None
    generator = body.get("generator") or "lmstudio"
    note_uids = None
    if treenode_ids:
        page_id = settings.SCOPE_PAGE_IDS[0] if settings.SCOPE_PAGE_IDS else 28
        note_uids = export_service.resolve_subtree_note_uids(
            SERVICE.repo.con, page_id, [int(t) for t in treenode_ids])
        if not note_uids:
            return jsonify({"error": "Selected tree nodes have no notes under them."}), 400
    try:
        result = export_service.summarize_branch(
            SERVICE.repo.con, colors, note_uids, generator_name=generator)
    except ModelError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@bp.route("/api/questions")
def api_questions():
    return jsonify({"groups": Q.GROUPS, "questions": Q.QUESTIONS})


# ── asking ─────────────────────────────────────────────────────────────

@bp.route("/api/ask", methods=["POST"])
def api_ask():
    if (bad := _need_service()):
        return bad
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Type a question first."}), 400

    regimes = body.get("regimes") or settings.DEFAULT_REGIMES
    unknown = [r for r in regimes if r not in settings.REGIMES]
    if unknown:
        return jsonify({"error": f"Unknown regime: {', '.join(unknown)}"}), 400

    generator = body.get("generator")
    overrides = body.get("overrides") or None
    results = []
    for key in regimes:
        try:
            results.append(SERVICE.answer(
                query, key, generator=generator, overrides=overrides,
                retrieval_only=bool(body.get("retrieval_only"))))
        except ModelError as e:
            results.append({"regime": key,
                            "regime_label": settings.REGIMES[key]["label"],
                            "error": str(e), "fragments": [], "metrics": {}})
        except Exception as e:
            results.append({"regime": key,
                            "regime_label": settings.REGIMES[key]["label"],
                            "error": f"Unexpected failure: {e}",
                            "fragments": [], "metrics": {}})
    return jsonify({"query": query, "results": results})


@bp.route("/api/ask_stream")
def api_ask_stream():
    """Server-Sent Events, one regime per call — the bench opens one of
    these per selected regime so columns fill in independently as tokens
    arrive, instead of the whole card blocking on the slowest model.
    GET (not POST) because EventSource can't send a request body."""
    if (bad := _need_service()):
        return bad
    query = (request.args.get("query") or "").strip()
    regime_key = request.args.get("regime")
    if not query or not regime_key or regime_key not in settings.REGIMES:
        return jsonify({"error": "query and a known regime are required"}), 400
    generator = request.args.get("generator") or None
    overrides_raw = request.args.get("overrides")
    overrides = json.loads(overrides_raw) if overrides_raw else None

    def sse():
        try:
            for kind, payload in SERVICE.answer_stream(
                    query, regime_key, generator=generator, overrides=overrides):
                yield f"event: {kind}\ndata: {json.dumps(payload)}\n\n"
        except ModelError as e:
            yield f"event: error\ndata: {json.dumps(str(e))}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps(f'Unexpected failure: {e}')}\n\n"

    return Response(sse(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                            "X-Accel-Buffering": "no"})


# ── rating ─────────────────────────────────────────────────────────────

@bp.route("/api/rate/next")
def api_rate_next():
    """One question's answers, ready to rate.

    ?blind=0 shows regime labels and keeps a stable order.
    ?question_id=B3 jumps to a specific question instead of the next unrated one.
    """
    blind = request.args.get("blind", "1") != "0"
    qid = request.args.get("question_id") or RESULTS.next_unrated_question()
    if not qid:
        no_runs = not RESULTS.question_ids()
        return jsonify({"done": True, "remaining": 0, "no_runs": no_runs})

    runs = RESULTS.runs_for_question(qid)
    if not runs:
        return jsonify({"error": f"No stored runs for {qid}."}), 404
    rated = RESULTS.rated_run_ids()

    items = []
    for r in runs:
        existing = RESULTS.rating_for(r["id"])
        item = {"run_id": r["id"], "answer": r["answer"],
                "rated": r["id"] in rated,
                "n_fragments": json.loads(r["metrics"] or "{}").get("n_fragments", 0),
                "scores": ({k: existing[k] for k in
                            ("overall", "correctness", "completeness", "grounding")}
                           if existing else None)}
        if not blind:
            item["regime"] = r["regime"]
            item["regime_label"] = settings.REGIMES.get(
                r["regime"], {}).get("label", r["regime"])
            item["generator"] = r["generator"]
        items.append(item)

    if blind:
        random.shuffle(items)
    else:
        items.sort(key=lambda x: x["regime"])

    meta = Q.BY_ID.get(qid, {})
    return jsonify({
        "done": False, "blind": blind, "question_id": qid,
        "question": runs[0]["question"], "group": meta.get("group", "?"),
        "group_name": Q.GROUPS.get(meta.get("group", ""), {}).get("name", ""),
        "hypothesis": Q.GROUPS.get(meta.get("group", ""), {}).get("hypothesis", ""),
        "note": meta.get("note", ""),
        "all_question_ids": RESULTS.question_ids(),
        "remaining": RESULTS.unrated_count(),
        "items": items,
    })


@bp.route("/api/rate", methods=["POST"])
def api_rate():
    b = request.get_json(force=True, silent=True) or {}
    if "run_id" not in b:
        return jsonify({"error": "run_id is required."}), 400
    RESULTS.save_rating(b["run_id"], b.get("overall"), b.get("correctness"),
                        b.get("completeness"), b.get("grounding"), b.get("notes", ""),
                        blind=bool(b.get("blind", True)))
    return jsonify({"ok": True, "remaining": RESULTS.unrated_count()})


@bp.route("/api/results/summary")
def api_summary():
    s = RESULTS.summary()
    s["regime_labels"] = {k: v["label"] for k, v in settings.REGIMES.items()}
    return jsonify(s)
