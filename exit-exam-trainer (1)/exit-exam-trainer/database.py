"""
database.py
-----------
SQLite storage. One file, no server, safe to copy or delete.

Tables
  questions  the bank, with cached generated options
  progress   per-question history (attempts, result, bookmark, time spent)
  state      key/value blob for "resume where I left off"
  meta       import bookkeeping

WAL mode is on, so a hard power-off loses at most the transaction in flight.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager

import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    qhash         TEXT UNIQUE NOT NULL,
    question      TEXT NOT NULL,
    answer        TEXT NOT NULL,
    explanation   TEXT DEFAULT '',
    topic         TEXT DEFAULT 'General',
    options_json  TEXT,
    correct_index INTEGER,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_questions_topic ON questions(topic);

CREATE TABLE IF NOT EXISTS progress (
    question_id   INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    attempts      INTEGER DEFAULT 0,
    correct_count INTEGER DEFAULT 0,
    wrong_count   INTEGER DEFAULT 0,
    skipped       INTEGER DEFAULT 0,
    last_result   TEXT DEFAULT '',
    bookmarked    INTEGER DEFAULT 0,
    total_time_ms INTEGER DEFAULT 0,
    updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_progress_result ON progress(last_result);
CREATE INDEX IF NOT EXISTS idx_progress_bookmark ON progress(bookmarked);

CREATE TABLE IF NOT EXISTS state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    """One connection per thread; Flask's dev server is threaded."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)


def hash_question(text: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", text.lower())[:200]
    return hashlib.sha1(key.encode()).hexdigest()


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

def insert_questions(rows: list[dict]) -> tuple[int, int]:
    """Insert cleaned rows. Returns (inserted, duplicates_skipped)."""
    inserted = dup = 0
    with tx() as conn:
        for r in rows:
            h = hash_question(r["question"])
            cur = conn.execute(
                "INSERT OR IGNORE INTO questions "
                "(qhash, question, answer, explanation, topic) VALUES (?,?,?,?,?)",
                (h, r["question"], r["answer"],
                 r.get("explanation", ""), r.get("topic", "General")),
            )
            if cur.rowcount:
                inserted += 1
            else:
                dup += 1
    return inserted, dup


def questions_missing_options() -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT id, question, answer, topic FROM questions "
        "WHERE options_json IS NULL OR correct_index IS NULL"
    ).fetchall()


def all_questions_min() -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT id, question, answer, topic FROM questions ORDER BY id"
    ).fetchall()


def save_options(pairs: list[tuple[int, list[str], int]]) -> None:
    """pairs = [(question_id, options, correct_index), ...]"""
    with tx() as conn:
        conn.executemany(
            "UPDATE questions SET options_json=?, correct_index=? WHERE id=?",
            [(json.dumps(o, ensure_ascii=False), ci, qid) for qid, o, ci in pairs],
        )


def clear_options() -> None:
    with tx() as conn:
        conn.execute("UPDATE questions SET options_json=NULL, correct_index=NULL")


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

def count_questions() -> int:
    row = connect().execute("SELECT COUNT(*) c FROM questions").fetchone()
    return row["c"] if row else 0


def topics() -> list[dict]:
    rows = connect().execute(
        "SELECT topic, COUNT(*) n FROM questions GROUP BY topic ORDER BY n DESC"
    ).fetchall()
    return [{"topic": r["topic"], "count": r["n"]} for r in rows]


_FILTER_SQL = {
    "all": "",
    "bookmarked": "AND p.bookmarked = 1",
    "wrong": "AND p.last_result = 'wrong'",
    "unattempted": "AND (p.attempts IS NULL OR p.attempts = 0)",
    "attempted": "AND p.attempts > 0",
}


def fetch_questions(topic: str = "", filt: str = "all",
                    search: str = "", limit: int = 0, offset: int = 0) -> list[dict]:
    where = ["1=1"]
    params: list = []
    if topic and topic.lower() != "all":
        where.append("q.topic = ?")
        params.append(topic)
    if search:
        where.append("(q.question LIKE ? OR q.answer LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]

    sql = (
        "SELECT q.id, q.question, q.answer, q.explanation, q.topic, "
        "       q.options_json, q.correct_index, "
        "       COALESCE(p.attempts,0) attempts, COALESCE(p.bookmarked,0) bookmarked, "
        "       COALESCE(p.last_result,'') last_result "
        "FROM questions q LEFT JOIN progress p ON p.question_id = q.id "
        f"WHERE {' AND '.join(where)} {_FILTER_SQL.get(filt, '')} "
        "ORDER BY q.id"
    )
    if limit:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]

    out = []
    for r in connect().execute(sql, params):
        try:
            options = json.loads(r["options_json"]) if r["options_json"] else []
        except (TypeError, ValueError):
            options = []
        out.append({
            "id": r["id"],
            "question": r["question"],
            "answer": r["answer"],
            "explanation": r["explanation"] or "",
            "topic": r["topic"],
            "options": options,
            "correct_index": r["correct_index"],
            "attempts": r["attempts"],
            "bookmarked": bool(r["bookmarked"]),
            "last_result": r["last_result"],
        })
    return out


def fetch_ids(topic: str = "", filt: str = "all", search: str = "") -> list[int]:
    """Just the ids that match a filter - cheap enough to shuffle in memory."""
    where = ["1=1"]
    params: list = []
    if topic and topic.lower() != "all":
        where.append("q.topic = ?")
        params.append(topic)
    if search:
        where.append("(q.question LIKE ? OR q.answer LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    sql = (
        "SELECT q.id FROM questions q "
        "LEFT JOIN progress p ON p.question_id = q.id "
        f"WHERE {' AND '.join(where)} {_FILTER_SQL.get(filt, '')} ORDER BY q.id"
    )
    return [r["id"] for r in connect().execute(sql, params)]


def fetch_by_ids(ids: list[int]) -> list[dict]:
    """Fetch full rows for the given ids, preserving the order given."""
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = connect().execute(
        "SELECT q.id, q.question, q.answer, q.explanation, q.topic, "
        "       q.options_json, q.correct_index, "
        "       COALESCE(p.bookmarked,0) bookmarked, "
        "       COALESCE(p.attempts,0) attempts, "
        "       COALESCE(p.last_result,'') last_result "
        "FROM questions q LEFT JOIN progress p ON p.question_id = q.id "
        f"WHERE q.id IN ({marks})", ids).fetchall()

    by_id = {}
    for r in rows:
        try:
            options = json.loads(r["options_json"]) if r["options_json"] else []
        except (TypeError, ValueError):
            options = []
        if not options:
            options, ci = [r["answer"]], 0
        else:
            ci = r["correct_index"] if r["correct_index"] is not None else 0
        by_id[r["id"]] = {
            "id": r["id"],
            "question": r["question"],
            "answer": r["answer"],
            "explanation": r["explanation"] or "",
            "topic": r["topic"],
            "options": options,
            "correct": ci,
            "bookmarked": bool(r["bookmarked"]),
            "attempts": r["attempts"],
            "last_result": r["last_result"],
        }
    return [by_id[i] for i in ids if i in by_id]


def count_matching(topic: str = "", filt: str = "all", search: str = "") -> int:
    where = ["1=1"]
    params: list = []
    if topic and topic.lower() != "all":
        where.append("q.topic = ?")
        params.append(topic)
    if search:
        where.append("(q.question LIKE ? OR q.answer LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    sql = (
        "SELECT COUNT(*) c FROM questions q "
        "LEFT JOIN progress p ON p.question_id = q.id "
        f"WHERE {' AND '.join(where)} {_FILTER_SQL.get(filt, '')}"
    )
    row = connect().execute(sql, params).fetchone()
    return row["c"] if row else 0


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------

def record_attempt(question_id: int, result: str, time_ms: int = 0) -> None:
    """result is 'correct', 'wrong' or 'skipped'."""
    if result not in ("correct", "wrong", "skipped"):
        return
    with tx() as conn:
        conn.execute(
            "INSERT INTO progress (question_id) VALUES (?) "
            "ON CONFLICT(question_id) DO NOTHING", (question_id,))
        conn.execute(
            f"""UPDATE progress SET
                    attempts      = attempts + {0 if result == 'skipped' else 1},
                    correct_count = correct_count + {1 if result == 'correct' else 0},
                    wrong_count   = wrong_count   + {1 if result == 'wrong' else 0},
                    skipped       = skipped       + {1 if result == 'skipped' else 0},
                    last_result   = ?,
                    total_time_ms = total_time_ms + ?,
                    updated_at    = CURRENT_TIMESTAMP
                WHERE question_id = ?""",
            (result, max(0, int(time_ms)), question_id))


def toggle_bookmark(question_id: int) -> bool:
    with tx() as conn:
        conn.execute("INSERT INTO progress (question_id) VALUES (?) "
                     "ON CONFLICT(question_id) DO NOTHING", (question_id,))
        conn.execute("UPDATE progress SET bookmarked = 1 - bookmarked, "
                     "updated_at = CURRENT_TIMESTAMP WHERE question_id = ?",
                     (question_id,))
        row = conn.execute("SELECT bookmarked FROM progress WHERE question_id = ?",
                           (question_id,)).fetchone()
    return bool(row["bookmarked"]) if row else False


def reset_progress() -> None:
    with tx() as conn:
        conn.execute("DELETE FROM progress")
        conn.execute("DELETE FROM state")


def stats() -> dict:
    conn = connect()
    total = count_questions()
    row = conn.execute(
        "SELECT COUNT(*) seen, "
        "       COALESCE(SUM(attempts),0) attempts, "
        "       COALESCE(SUM(correct_count),0) correct, "
        "       COALESCE(SUM(wrong_count),0) wrong, "
        "       COALESCE(SUM(skipped),0) skipped, "
        "       COALESCE(SUM(total_time_ms),0) time_ms, "
        "       COALESCE(SUM(bookmarked),0) bookmarks "
        "FROM progress"
    ).fetchone()

    attempts = row["attempts"] or 0
    correct = row["correct"] or 0
    per_topic = conn.execute(
        "SELECT q.topic, "
        "       COUNT(*) total, "
        "       COALESCE(SUM(p.attempts),0) attempts, "
        "       COALESCE(SUM(p.correct_count),0) correct, "
        "       COALESCE(SUM(p.wrong_count),0) wrong "
        "FROM questions q LEFT JOIN progress p ON p.question_id = q.id "
        "GROUP BY q.topic ORDER BY q.topic"
    ).fetchall()

    topics_out = []
    for t in per_topic:
        a = t["attempts"] or 0
        c = t["correct"] or 0
        topics_out.append({
            "topic": t["topic"],
            "total": t["total"],
            "attempts": a,
            "correct": c,
            "wrong": t["wrong"] or 0,
            "accuracy": round(100 * c / a, 1) if a else None,
        })

    answered = conn.execute(
        "SELECT COUNT(*) c FROM progress WHERE attempts > 0").fetchone()["c"]

    return {
        "total_questions": total,
        "answered": answered,
        "attempts": attempts,
        "correct": correct,
        "wrong": row["wrong"] or 0,
        "skipped": row["skipped"] or 0,
        "bookmarks": row["bookmarks"] or 0,
        "accuracy": round(100 * correct / attempts, 1) if attempts else 0.0,
        "avg_seconds": round((row["time_ms"] or 0) / 1000 / attempts, 1) if attempts else 0.0,
        "progress_pct": round(100 * answered / total, 1) if total else 0.0,
        "topics": topics_out,
    }


# --------------------------------------------------------------------------
# Session state (resume)
# --------------------------------------------------------------------------

def set_state(key: str, value) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO state (key, value, updated_at) VALUES (?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=CURRENT_TIMESTAMP",
            (key, json.dumps(value, ensure_ascii=False)))


def get_state(key: str, default=None):
    row = connect().execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return default


def set_meta(key: str, value: str) -> None:
    with tx() as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, str(value)))


def get_meta(key: str, default=None):
    row = connect().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
