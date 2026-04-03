#!/usr/bin/env python3
"""
rate_limiter.py — Per-user rate limiting
=========================================
- Chat: N requests per hour per user
- Search: separate limit
- All tracked in SQLite (same DB as auth)
- Returns (allowed: bool, retry_after_seconds: int)
"""

import sqlite3, time
from pathlib import Path

DB_PATH = Path(__file__).parent / "mactech.db"

# Limits per role
LIMITS = {
    "beta":  {"chat": 40,  "search": 100, "window": 3600},   # 40 chat/hr
    "admin": {"chat": 999, "search": 999, "window": 3600},
}

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            action     TEXT NOT NULL,        -- 'chat' | 'search'
            ts         INTEGER NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rl ON rate_log(user_id, action, ts)")
    conn.commit()
    return conn

def check_rate(user_id: int, role: str, action: str = "chat") -> tuple[bool, int]:
    """
    Returns (allowed, retry_after_seconds).
    retry_after_seconds = 0 if allowed.
    """
    limits = LIMITS.get(role, LIMITS["beta"])
    max_req = limits[action]
    window  = limits["window"]
    now     = int(time.time())
    cutoff  = now - window

    with _db() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM rate_log WHERE user_id=? AND action=? AND ts>?",
            (user_id, action, cutoff)
        ).fetchone()[0]

        if count >= max_req:
            # Find oldest request in window → that's when a slot opens
            oldest = db.execute(
                "SELECT MIN(ts) FROM rate_log WHERE user_id=? AND action=? AND ts>?",
                (user_id, action, cutoff)
            ).fetchone()[0] or now
            retry_after = (oldest + window) - now
            return False, max(1, retry_after)

        # Log this request
        db.execute("INSERT INTO rate_log (user_id, action, ts) VALUES (?,?,?)",
                   (user_id, action, now))

    return True, 0

def purge_old_logs(older_than_hours: int = 25):
    """Clean up old rate log entries — call periodically."""
    cutoff = int(time.time()) - older_than_hours * 3600
    with _db() as db:
        db.execute("DELETE FROM rate_log WHERE ts<?", (cutoff,))
