#!/usr/bin/env python3
"""
Embeddings-based semantic search for repair cases.
Uses OpenAI text-embedding-3-small to embed all cases,
stores vectors in SQLite, does cosine similarity at query time.

Usage:
    python3 embed_search.py --build          # embed all cases (run once)
    python3 embed_search.py --query "5v stuck" --board 820-02016
    python3 embed_search.py --stats
"""

import os, sys, json, sqlite3, struct, math, re, time, argparse
import urllib.request, urllib.error
from pathlib import Path

DB_PATH = Path.home() / "repair_ai_db" / "repair_cases.db"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM   = 1536

# ── Embedding API ──────────────────────────────────────────────────
def get_embedding(text, key):
    """Get embedding vector from OpenAI API."""
    text = text.replace("\n", " ")[:8000]
    payload = json.dumps({
        "model": EMBED_MODEL,
        "input": text,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["data"][0]["embedding"]

def vec_to_blob(vec):
    return struct.pack(f"{len(vec)}f", *vec)

def blob_to_vec(blob):
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))

def cosine(a, b):
    dot = sum(x*y for x,y in zip(a,b))
    na  = math.sqrt(sum(x*x for x in a))
    nb  = math.sqrt(sum(x*x for x in b))
    return dot / (na * nb) if na and nb else 0.0

# ── DB setup ───────────────────────────────────────────────────────
def init_embed_table():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS case_embeddings (
            case_id  INTEGER PRIMARY KEY REFERENCES repair_cases(id),
            vector   BLOB NOT NULL,
            text_key TEXT
        )
    """)
    con.commit()
    con.close()

def build_case_text(row):
    """Build searchable text for a repair case."""
    parts = []
    if row["board"]:    parts.append(f"Board: {row['board']}")
    if row["symptom"]:  parts.append(f"Symptom: {row['symptom']}")
    if row["rails_tested"]:
        try:
            rails = json.loads(row["rails_tested"])
            for r in rails:
                parts.append(f"Rail {r.get('rail','')}: {r.get('value','')}/{r.get('expected','')}")
        except: pass
    if row["root_cause"]: parts.append(f"Cause: {row['root_cause']}")
    if row["fix"]:        parts.append(f"Fix: {row['fix']}")
    if row.get("raw_text"):
        parts.append(row["raw_text"][:500])
    return " | ".join(parts)

# ── Build embeddings ───────────────────────────────────────────────
def build_embeddings(batch_size=50):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("⚠ OPENAI_API_KEY not set"); return

    init_embed_table()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Get cases without embeddings
    existing = {row[0] for row in con.execute("SELECT case_id FROM case_embeddings")}
    cases = con.execute("""
        SELECT id, board, symptom, rails_tested, root_cause, fix, raw_text
        FROM repair_cases
        WHERE symptom != 'extraction_failed'
        AND (solved=1 OR rails_tested != '[]' OR root_cause IS NOT NULL)
    """).fetchall()

    to_embed = [r for r in cases if r["id"] not in existing]
    print(f"📊 {len(existing)} already embedded, {len(to_embed)} to process")

    ok = err = 0
    for i, row in enumerate(to_embed):
        text = build_case_text(dict(row))
        try:
            vec  = get_embedding(text, key)
            blob = vec_to_blob(vec)
            con.execute(
                "INSERT OR REPLACE INTO case_embeddings (case_id, vector, text_key) VALUES (?,?,?)",
                (row["id"], blob, text[:200])
            )
            if (i+1) % 10 == 0:
                con.commit()
                print(f"  [{i+1}/{len(to_embed)}] embedded {ok+1} cases")
            ok += 1
            time.sleep(0.05)  # rate limit
        except Exception as e:
            err += 1
            if err < 5: print(f"  ❌ case {row['id']}: {e}")
        if (i+1) % batch_size == 0:
            con.commit()

    con.commit()
    con.close()
    print(f"\n✅ Done — {ok} embedded, {err} errors")
    print(f"Total embeddings: {ok + len(existing)}")

# ── Semantic search ────────────────────────────────────────────────
def search_semantic(query, board=None, limit=5, threshold=0.35):
    """Search repair cases using cosine similarity."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key or not DB_PATH.exists():
        return []

    try:
        q_vec = get_embedding(query, key)
    except Exception as e:
        print(f"Embedding error: {e}")
        return []

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Load candidates — filter by board if given
    if board:
        sql = """
            SELECT r.id, r.board, r.symptom, r.rails_tested, r.components,
                   r.root_cause, r.fix, r.solved, r.source_url, e.vector
            FROM repair_cases r
            JOIN case_embeddings e ON e.case_id = r.id
            WHERE r.board = ? AND r.symptom != 'extraction_failed'
        """
        rows = con.execute(sql, (board,)).fetchall()
    else:
        sql = """
            SELECT r.id, r.board, r.symptom, r.rails_tested, r.components,
                   r.root_cause, r.fix, r.solved, r.source_url, e.vector
            FROM repair_cases r
            JOIN case_embeddings e ON e.case_id = r.id
            WHERE r.symptom != 'extraction_failed'
        """
        rows = con.execute(sql).fetchall()

    con.close()

    # Score all candidates
    scored = []
    for row in rows:
        try:
            vec   = blob_to_vec(row["vector"])
            score = cosine(q_vec, vec)
            # Boost wiki guides and solved cases
            is_wiki = "repair.wiki" in (row["source_url"] or "")
            boost = (0.1 if is_wiki else 0) + (0.05 if row["solved"] else 0)
            scored.append((score + boost, dict(row)))
        except: pass

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for score, row in scored[:limit]:
        if score < threshold: break
        del row["vector"]
        row["rails_tested"] = json.loads(row.get("rails_tested") or "[]")
        row["components"]   = json.loads(row.get("components")   or "[]")
        row["_score"] = round(score, 3)
        results.append(row)

    return results

# ── Format for prompt ─────────────────────────────────────────────
def format_semantic_cases(cases):
    if not cases: return ""
    lines = []
    for c in cases:
        lines.append(f"\n[{c.get('board','')} | {'solved' if c['solved'] else 'open'} | score={c.get('_score',0):.2f}]")
        if c.get("symptom"):    lines.append(f"Symptom: {c['symptom']}")
        for r in (c.get("rails_tested") or [])[:4]:
            if r.get("rail"):
                lines.append(f"  {r['rail']}: {r.get('value','?')} (expected {r.get('expected','?')})")
        if c.get("root_cause"): lines.append(f"Cause: {c['root_cause']}")
        if c.get("fix"):        lines.append(f"Fix: {c['fix']}")
    return "\n".join(lines)

# ── Stats ─────────────────────────────────────────────────────────
def stats():
    if not DB_PATH.exists():
        print("No DB found"); return
    con = sqlite3.connect(DB_PATH)
    total  = con.execute("SELECT COUNT(*) FROM repair_cases").fetchone()[0]
    embedded = 0
    try:
        embedded = con.execute("SELECT COUNT(*) FROM case_embeddings").fetchone()[0]
    except: pass
    solved = con.execute("SELECT COUNT(*) FROM repair_cases WHERE solved=1").fetchone()[0]
    wiki   = con.execute("SELECT COUNT(*) FROM repair_cases WHERE is_wiki=1").fetchone()[0] if True else 0
    con.close()
    print(f"\n📊 Cases: {total} total | {solved} solved | {wiki} wiki")
    print(f"   Embedded: {embedded}/{total} ({100*embedded//total if total else 0}%)")
    remaining = total - embedded
    cost = remaining * 0.00002  # $0.02 per 1M tokens, avg ~1000 tokens/case
    print(f"   Remaining to embed: {remaining} (~${cost:.2f} at current pricing)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build",  action="store_true", help="Build embeddings for all cases")
    parser.add_argument("--query",  type=str)
    parser.add_argument("--board",  type=str)
    parser.add_argument("--stats",  action="store_true")
    parser.add_argument("--limit",  type=int, default=5)
    args = parser.parse_args()

    if args.stats or (not args.build and not args.query):
        stats()
    if args.build:
        build_embeddings()
    if args.query:
        results = search_semantic(args.query, board=args.board, limit=args.limit)
        print(f"\n🔍 Semantic search: '{args.query}'" + (f" | board={args.board}" if args.board else ""))
        print(format_semantic_cases(results) or "  No results")
