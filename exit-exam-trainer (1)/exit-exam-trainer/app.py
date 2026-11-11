#!/usr/bin/env python3
"""
app.py
------
Flask server for the Merchant Navy MCQ trainer.

Run:  python app.py       then open http://127.0.0.1:5000

Everything is local: no internet, no API calls, no external services.
"""

from __future__ import annotations

import csv
import io
import math
import random
import time
import traceback
from pathlib import Path

from flask import (Flask, jsonify, render_template, request,
                   send_file, make_response)
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

import builder
import config
import database as db
import parser

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024
app.json.sort_keys = False


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

def bootstrap() -> None:
    """Create the database and load the bundled bank on first run."""
    db.init_db()
    if db.count_questions() == 0 and config.SEED_BANK.exists():
        print(f"[startup] First run - importing {config.SEED_BANK.name} ...")
        rows = parser.load_bank(config.SEED_BANK)
        inserted, dupes = db.insert_questions(rows)
        print(f"[startup] {inserted} questions added ({dupes} duplicates skipped)")
    pending = len(db.questions_missing_options())
    if pending:
        print(f"[startup] Preparing options for {pending} questions "
              "(one time only) ...")
        t0 = time.time()
        builder.build()
        print(f"[startup] Done in {time.time() - t0:.1f}s")
    print(f"[startup] Ready - {db.count_questions()} questions available")


# --------------------------------------------------------------------------
# Error handling: the API always answers with JSON, never an HTML stack trace
# --------------------------------------------------------------------------

@app.errorhandler(Exception)
def handle_error(exc):
    if not request.path.startswith("/api/"):
        raise exc

    # A 404 or 405 is a real answer, not a crash - keep its status code.
    if isinstance(exc, HTTPException):
        return jsonify({"ok": False, "error": exc.description,
                        "status": exc.code}), exc.code

    app.logger.error("%s\n%s", exc, traceback.format_exc())
    return jsonify({"ok": False,
                    "error": f"{type(exc).__name__}: {exc}"}), 500


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html",
                           set_size=config.SET_SIZE,
                           exam_seconds=config.EXAM_SECONDS_PER_Q)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@app.get("/api/meta")
def api_meta():
    total = db.count_questions()
    return jsonify({
        "ok": True,
        "total": total,
        "set_size": config.SET_SIZE,
        "sets": max(1, math.ceil(total / config.SET_SIZE)),
        "topics": db.topics(),
        "resume": db.get_state("session"),
    })


@app.get("/api/count")
def api_count():
    """How many questions match the current filters - used while typing."""
    n = db.count_matching(request.args.get("topic", ""),
                          request.args.get("filter", "all"),
                          request.args.get("search", "").strip())
    return jsonify({"ok": True, "matching": n,
                    "sets": max(0, math.ceil(n / config.SET_SIZE))})


@app.get("/api/set")
def api_set():
    """Return one set of questions for the chosen filters."""
    try:
        index = max(0, int(request.args.get("set", 0)))
    except ValueError:
        index = 0
    topic = request.args.get("topic", "")
    filt = request.args.get("filter", "all")
    search = request.args.get("search", "").strip()
    shuffle_q = request.args.get("shuffle_questions") == "1"
    shuffle_o = request.args.get("shuffle_options") == "1"
    try:
        seed = int(request.args.get("seed", 0))
    except ValueError:
        seed = 0

    ids = db.fetch_ids(topic, filt, search)
    if not ids:
        return jsonify({"ok": True, "questions": [], "sets": 0, "matching": 0,
                        "set": 0})

    if shuffle_q:
        random.Random(seed or 20260722).shuffle(ids)

    sets = max(1, math.ceil(len(ids) / config.SET_SIZE))
    index = min(index, sets - 1)
    start = index * config.SET_SIZE
    chunk = ids[start:start + config.SET_SIZE]

    questions = db.fetch_by_ids(chunk)

    if shuffle_o:
        rng = random.Random((seed or 20260722) + index)
        for q in questions:
            correct_text = q["options"][q["correct"]]
            rng.shuffle(q["options"])
            q["correct"] = q["options"].index(correct_text)

    return jsonify({"ok": True, "set": index, "sets": sets,
                    "matching": len(ids), "questions": questions})


@app.post("/api/answer")
def api_answer():
    data = request.get_json(silent=True) or {}
    try:
        qid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Missing question id"}), 400
    result = str(data.get("result", ""))
    time_ms = int(data.get("time_ms") or 0)
    db.record_attempt(qid, result, time_ms)
    return jsonify({"ok": True})


@app.post("/api/answers")
def api_answers():
    """Bulk write - used when an exam is submitted."""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    written = 0
    for it in items:
        try:
            db.record_attempt(int(it["id"]), str(it.get("result", "")),
                              int(it.get("time_ms") or 0))
            written += 1
        except Exception:
            continue
    return jsonify({"ok": True, "written": written})


@app.post("/api/bookmark")
def api_bookmark():
    data = request.get_json(silent=True) or {}
    try:
        qid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Missing question id"}), 400
    return jsonify({"ok": True, "bookmarked": db.toggle_bookmark(qid)})


@app.get("/api/stats")
def api_stats():
    return jsonify({"ok": True, **db.stats()})


@app.post("/api/state")
def api_state_save():
    db.set_state("session", request.get_json(silent=True) or {})
    return jsonify({"ok": True})


@app.get("/api/state")
def api_state_load():
    return jsonify({"ok": True, "session": db.get_state("session")})


@app.post("/api/reset")
def api_reset():
    db.reset_progress()
    return jsonify({"ok": True})


@app.post("/api/import")
def api_import():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file selected"}), 400

    name = secure_filename(f.filename)
    ext = Path(name).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        return jsonify({"ok": False,
                        "error": f"{ext or 'This file type'} is not supported. "
                                 "Use CSV, XLSX, JSON or TXT."}), 400

    dest = config.UPLOAD_DIR / name
    f.save(dest)

    rows = parser.load_bank(dest)
    if not rows:
        return jsonify({"ok": False,
                        "error": "No questions found. Check that the file has a "
                                 "question column and an answer column."}), 400

    inserted, dupes = db.insert_questions(rows)
    built = builder.build(verbose=False)
    return jsonify({"ok": True, "parsed": len(rows), "added": inserted,
                    "duplicates": dupes, "prepared": built,
                    "total": db.count_questions()})


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def _csv_response(header: list[str], rows: list[list], filename: str):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    resp = make_response("\ufeff" + buf.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@app.get("/api/export/<kind>.csv")
def api_export_csv(kind: str):
    if kind == "bookmarks":
        rows = db.fetch_questions(filt="bookmarked")
        return _csv_response(
            ["Question", "Answer", "Topic"],
            [[r["question"], r["answer"], r["topic"]] for r in rows],
            "bookmarks.csv")

    if kind == "wrong":
        rows = db.fetch_questions(filt="wrong")
        return _csv_response(
            ["Question", "Answer", "Topic"],
            [[r["question"], r["answer"], r["topic"]] for r in rows],
            "wrong-questions.csv")

    if kind == "report":
        s = db.stats()
        rows = [["Total questions", s["total_questions"]],
                ["Answered", s["answered"]],
                ["Attempts", s["attempts"]],
                ["Correct", s["correct"]],
                ["Wrong", s["wrong"]],
                ["Skipped", s["skipped"]],
                ["Accuracy %", s["accuracy"]],
                ["Average seconds per question", s["avg_seconds"]],
                ["Bookmarks", s["bookmarks"]],
                ["Progress %", s["progress_pct"]],
                [], ["Topic", "Questions", "Attempts", "Correct", "Wrong", "Accuracy %"]]
        for t in s["topics"]:
            rows.append([t["topic"], t["total"], t["attempts"], t["correct"],
                         t["wrong"], t["accuracy"] if t["accuracy"] is not None else ""])
        return _csv_response(["Metric", "Value"], rows, "performance-report.csv")

    return jsonify({"ok": False, "error": "Unknown export"}), 404


@app.get("/api/export/<kind>.xlsx")
def api_export_xlsx(kind: str):
    try:
        from openpyxl import Workbook
    except ImportError:
        return jsonify({"ok": False,
                        "error": "openpyxl is not installed - use CSV export."}), 400

    wb = Workbook()
    ws = wb.active

    if kind in ("bookmarks", "wrong"):
        ws.title = "Bookmarks" if kind == "bookmarks" else "Wrong questions"
        ws.append(["Question", "Answer", "Topic"])
        for r in db.fetch_questions(filt="bookmarked" if kind == "bookmarks" else "wrong"):
            ws.append([r["question"], r["answer"], r["topic"]])
    elif kind == "report":
        s = db.stats()
        ws.title = "Summary"
        ws.append(["Metric", "Value"])
        for k in ("total_questions", "answered", "attempts", "correct", "wrong",
                  "skipped", "accuracy", "avg_seconds", "bookmarks", "progress_pct"):
            ws.append([k.replace("_", " ").title(), s[k]])
        ws2 = wb.create_sheet("By topic")
        ws2.append(["Topic", "Questions", "Attempts", "Correct", "Wrong", "Accuracy %"])
        for t in s["topics"]:
            ws2.append([t["topic"], t["total"], t["attempts"], t["correct"],
                        t["wrong"], t["accuracy"]])
    else:
        return jsonify({"ok": False, "error": "Unknown export"}), 404

    for sheet in wb.worksheets:
        sheet.column_dimensions["A"].width = 80
        sheet.column_dimensions["B"].width = 40

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{kind}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet")


if __name__ == "__main__":
    bootstrap()
    print(f"\n  Open http://{config.HOST}:{config.PORT} in your browser\n")
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG, threaded=True)
