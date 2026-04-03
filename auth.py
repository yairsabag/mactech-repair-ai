#!/usr/bin/env python3
"""
auth.py v2 — Username + password auth with activity tracking
"""
import hashlib, hmac, json, os, re, secrets, sqlite3, time
from pathlib import Path
from typing import Optional

DB_PATH  = Path(__file__).parent / "mactech.db"
SECRET   = os.environ.get("MACTECH_SECRET", secrets.token_hex(32))
TOKEN_TTL = 60 * 60 * 24 * 30

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with _db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT UNIQUE NOT NULL,
            email        TEXT,
            pw_hash      TEXT NOT NULL,
            role         TEXT DEFAULT 'beta',
            created_at   INTEGER DEFAULT (strftime('%s','now')),
            last_seen    INTEGER,
            invite_code  TEXT
        );
        CREATE TABLE IF NOT EXISTS invite_codes (
            code       TEXT PRIMARY KEY,
            created_by TEXT,
            max_uses   INTEGER DEFAULT 1,
            uses       INTEGER DEFAULT 0,
            expires_at INTEGER,
            note       TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            expires_at INTEGER,
            ip         TEXT
        );
        CREATE TABLE IF NOT EXISTS activity (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            board   TEXT,
            symptom TEXT,
            ts      INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS rate_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action  TEXT NOT NULL,
            ts      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rl ON rate_log(user_id, action, ts);
        CREATE TABLE IF NOT EXISTS feedback (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            board        TEXT,
            symptom      TEXT,
            category     TEXT NOT NULL,
            note         TEXT,
            de_step      TEXT,
            conversation TEXT,
            ts           INTEGER DEFAULT (strftime('%s','now'))
        );
        """)
    print("✅ DB initialised")

# ── Password ──────────────────────────────────────────────────────────────────
def _hash_pw(password, salt=""):
    if not salt: salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"{salt}:{dk.hex()}"

def _verify_pw(password, stored):
    try:
        salt, _ = stored.split(":", 1)
        return hmac.compare_digest(_hash_pw(password, salt), stored)
    except: return False

# ── Invites ───────────────────────────────────────────────────────────────────
def create_invite(note="", max_uses=1, expires_in_days=None, created_by="admin"):
    code = secrets.token_urlsafe(12)
    exp  = int(time.time()) + expires_in_days * 86400 if expires_in_days else None
    with _db() as db:
        db.execute("INSERT INTO invite_codes (code,created_by,max_uses,note,expires_at) VALUES (?,?,?,?,?)",
                   (code, created_by, max_uses, note, exp))
    return code

def validate_invite(code):
    with _db() as db:
        row = db.execute("SELECT * FROM invite_codes WHERE code=?", (code,)).fetchone()
    if not row:                         return False, "Invalid invite code."
    if row["uses"] >= row["max_uses"]:  return False, "Invite code already used."
    if row["expires_at"] and time.time() > row["expires_at"]: return False, "Invite code expired."
    return True, "ok"

def consume_invite(code):
    with _db() as db:
        db.execute("UPDATE invite_codes SET uses=uses+1 WHERE code=?", (code,))

def list_invites():
    with _db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM invite_codes ORDER BY rowid DESC").fetchall()]

# ── Users ─────────────────────────────────────────────────────────────────────
def create_user(username, password, email="", invite_code="", role="beta"):
    username = username.strip().lower()
    if not re.match(r"^[a-z0-9_\-]{3,32}$", username):
        return False, "Username: 3-32 chars, a-z 0-9 _ only."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    if role == "beta":
        valid, reason = validate_invite(invite_code)
        if not valid: return False, reason
    try:
        with _db() as db:
            db.execute("INSERT INTO users (username,email,pw_hash,role,invite_code) VALUES (?,?,?,?,?)",
                       (username, email.lower().strip() or None, _hash_pw(password), role, invite_code))
        if role == "beta": consume_invite(invite_code)
        return True, "ok"
    except sqlite3.IntegrityError:
        return False, "Username already taken."

def authenticate(username, password):
    with _db() as db:
        row = db.execute("SELECT * FROM users WHERE username=? OR email=?",
                         (username.lower().strip(), username.lower().strip())).fetchone()
    if not row or not _verify_pw(password, row["pw_hash"]): return None
    return dict(row)

def get_user_by_id(user_id):
    with _db() as db:
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None

def set_role(username, role):
    with _db() as db:
        db.execute("UPDATE users SET role=? WHERE username=?", (role, username.lower()))

def list_users():
    with _db() as db:
        rows = db.execute("""
            SELECT u.*, COUNT(DISTINCT a.id) as session_count,
                   COUNT(DISTINCT a.board) as board_count,
                   MAX(a.ts) as last_activity
            FROM users u LEFT JOIN activity a ON a.user_id=u.id
            GROUP BY u.id ORDER BY u.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]

def get_user_activity(user_id):
    with _db() as db:
        boards   = [dict(r) for r in db.execute(
            "SELECT board, symptom, COUNT(*) as cnt, MAX(ts) as last "
            "FROM activity WHERE user_id=? GROUP BY board,symptom ORDER BY last DESC", (user_id,)).fetchall()]
        feedback = {r["category"]: r["cnt"] for r in db.execute(
            "SELECT category, COUNT(*) as cnt FROM feedback WHERE user_id=? GROUP BY category", (user_id,)).fetchall()}
    return {"boards": boards, "feedback": feedback}

# ── Activity ──────────────────────────────────────────────────────────────────
def log_activity(user_id, board="", symptom=""):
    if not user_id or user_id == 0: return
    with _db() as db:
        db.execute("INSERT INTO activity (user_id,board,symptom) VALUES (?,?,?)", (user_id, board, symptom))

# ── Tokens ────────────────────────────────────────────────────────────────────
def _sign(payload):
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def issue_token(user_id, ip=""):
    raw   = f"{user_id}.{int(time.time())}.{secrets.token_hex(8)}"
    token = f"{raw}.{_sign(raw)}"
    exp   = int(time.time()) + TOKEN_TTL
    with _db() as db:
        db.execute("INSERT INTO sessions (token,user_id,expires_at,ip) VALUES (?,?,?,?)",
                   (token, user_id, exp, ip))
    return token

def validate_token(token):
    if not token or token.count(".") < 3: return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload), sig): return None
    with _db() as db:
        row = db.execute(
            "SELECT s.*,u.username,u.email,u.role FROM sessions s "
            "JOIN users u ON s.user_id=u.id "
            "WHERE s.token=? AND s.expires_at>strftime('%s','now')", (token,)).fetchone()
    if not row: return None
    with _db() as db:
        db.execute("UPDATE users SET last_seen=strftime('%s','now') WHERE id=?", (row["user_id"],))
    return dict(row)

def revoke_token(token):
    with _db() as db: db.execute("DELETE FROM sessions WHERE token=?", (token,))

# ── Request helpers ───────────────────────────────────────────────────────────
def auth_from_request(headers):
    auth = headers.get("Authorization","")
    if auth.startswith("Bearer "): return validate_token(auth[7:].strip())
    for part in headers.get("Cookie","").split(";"):
        if "mactech_token=" in part:
            return validate_token(part.split("mactech_token=",1)[1].strip())
    return None

def require_auth(headers, min_role="beta"):
    user = auth_from_request(headers)
    if not user:                                       return None, "Unauthorized."
    if user["role"] == "blocked":                      return None, "Account suspended."
    if min_role == "admin" and user["role"] != "admin": return None, "Admin access required."
    return user, ""

# ── Rate limiting ─────────────────────────────────────────────────────────────
LIMITS = {"beta":{"chat":60,"search":200,"window":3600}, "admin":{"chat":999,"search":999,"window":3600}}

def check_rate(user_id, role, action="chat"):
    lim = LIMITS.get(role, LIMITS["beta"])
    now = int(time.time()); cutoff = now - lim["window"]
    with _db() as db:
        count = db.execute("SELECT COUNT(*) FROM rate_log WHERE user_id=? AND action=? AND ts>?",
                           (user_id, action, cutoff)).fetchone()[0]
        if count >= lim[action]:
            oldest = db.execute("SELECT MIN(ts) FROM rate_log WHERE user_id=? AND action=? AND ts>?",
                                (user_id, action, cutoff)).fetchone()[0] or now
            return False, max(1, (oldest + lim["window"]) - now)
        db.execute("INSERT INTO rate_log (user_id,action,ts) VALUES (?,?,?)", (user_id, action, now))
    return True, 0

# ── Feedback ──────────────────────────────────────────────────────────────────
CATEGORIES = {"helpful","wrong_diagnosis","missing_schematic","need_better_guidance"}

def save_feedback(user_id, category, board="", symptom="", note="", de_step=None, conversation=None):
    if category not in CATEGORIES: return False, "Unknown category."
    with _db() as db:
        db.execute("INSERT INTO feedback (user_id,board,symptom,category,note,de_step,conversation) VALUES (?,?,?,?,?,?,?)",
                   (user_id, board, symptom, category, note[:500],
                    json.dumps(de_step) if de_step else None,
                    json.dumps(conversation[-4:]) if conversation else None))
    return True, "ok"

def get_feedback_summary():
    with _db() as db:
        counts = {r["category"]: r["cnt"] for r in db.execute(
            "SELECT category, COUNT(*) as cnt FROM feedback GROUP BY category").fetchall()}
        recent = [dict(r) for r in db.execute(
            "SELECT f.*,u.username FROM feedback f LEFT JOIN users u ON f.user_id=u.id "
            "ORDER BY f.ts DESC LIMIT 100").fetchall()]
    return {"total": sum(counts.values()), "counts": counts, "recent": recent}

# ── Bootstrap ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    init_db()
    if "--create-admin" in sys.argv:
        uname = input("Admin username: ").strip()
        pw    = input("Admin password: ").strip()
        with _db() as db:
            try:
                db.execute("INSERT INTO users (username,pw_hash,role) VALUES (?,?,?)",
                           (uname.lower(), _hash_pw(pw), "admin"))
                print(f"✅ Admin '{uname}' created")
            except sqlite3.IntegrityError:
                db.execute("UPDATE users SET pw_hash=?,role='admin' WHERE username=?",
                           (_hash_pw(pw), uname.lower()))
                print(f"✅ Admin '{uname}' updated")
    else:
        code = create_invite(note="beta invite", max_uses=50)
        print(f"\n🔑 Invite: {code}")
        print(f"   Link: http://localhost:8765/landing?invite={code}")
        print(f"\nCreate admin: python3 auth.py --create-admin")
