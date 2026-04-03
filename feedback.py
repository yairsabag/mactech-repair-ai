#!/usr/bin/env python3
"""
feedback.py — Feedback storage and basic analytics
====================================================
4 categories + free text field.
Linked to session turn so we can trace what DE step caused the issue.
"""

import sqlite3, time, json
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "mactech.db"

CATEGORIES = {"helpful", "wrong_diagnosis", "missing_schematic", "need_better_guidance"}

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            board        TEXT,
            symptom      TEXT,
            category     TEXT NOT NULL,
            note         TEXT,                -- free text from technician
            de_step      TEXT,               -- JSON: last DE step at feedback time
            conversation TEXT,               -- JSON: last 4 turns for context
            ts           INTEGER DEFAULT (strftime('%s','now'))
        )
    """)
    conn.commit()
    return conn

def save_feedback(
    user_id: int,
    category: str,
    board: str = "",
    symptom: str = "",
    note: str = "",
    de_step: Optional[dict] = None,
    conversation: Optional[list] = None,
) -> tuple[bool, str]:

    if category not in CATEGORIES:
        return False, f"Unknown category. Use one of: {CATEGORIES}"

    note = note.strip()[:500]   # sanitize length

    with _db() as db:
        db.execute(
            "INSERT INTO feedback (user_id, board, symptom, category, note, de_step, conversation) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                user_id, board, symptom, category, note,
                json.dumps(de_step) if de_step else None,
                json.dumps(conversation[-4:]) if conversation else None,
            )
        )
    return True, "ok"

def get_feedback_summary() -> dict:
    """Returns counts per category + recent notes — for admin panel."""
    with _db() as db:
        rows = db.execute("""
            SELECT category, COUNT(*) as cnt
            FROM feedback GROUP BY category
        """).fetchall()

        recent = db.execute("""
            SELECT f.*, u.email
            FROM feedback f
            LEFT JOIN users u ON f.user_id = u.id
            ORDER BY f.ts DESC LIMIT 50
        """).fetchall()

    counts = {r["category"]: r["cnt"] for r in rows}
    total  = sum(counts.values())

    return {
        "total": total,
        "counts": counts,
        "recent": [dict(r) for r in recent],
    }

def get_feedback_by_board(board: str) -> list:
    with _db() as db:
        rows = db.execute(
            "SELECT * FROM feedback WHERE board=? ORDER BY ts DESC",
            (board,)
        ).fetchall()
    return [dict(r) for r in rows]
