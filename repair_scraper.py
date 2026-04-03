#!/usr/bin/env python3
"""
MacBook Repair Case Scraper
============================
Scrapes public solved threads from Rossmann Forum,
extracts structured repair cases using GPT-5.4,
stores in SQLite DB at ~/repair_ai_db/repair_cases.db

Usage:
    export OPENAI_API_KEY=sk-...
    python3 repair_scraper.py                   # scrape + extract
    python3 repair_scraper.py --query "820-02016 no charge"  # test search
"""

import urllib.request, urllib.parse, json, sqlite3, time, re, os, sys, argparse
from pathlib import Path
from html.parser import HTMLParser

DB_PATH       = Path.home() / "repair_ai_db" / "repair_cases.db"
COOKIES_PATH  = Path(__file__).parent / "rossmann_cookies.json"
BASE_URL      = "https://boards.rossmanngroup.com"

def load_cookie_header():
    """Load cookies from rossmann_cookies.json (exported from Cookie-Editor)"""
    if not COOKIES_PATH.exists():
        print(f"⚠ No cookies file at {COOKIES_PATH} — scraping public threads only")
        return ""
    try:
        cookies = json.loads(COOKIES_PATH.read_text())
        # Cookie-Editor exports as list of {name, value, ...}
        if isinstance(cookies, list):
            parts = [f"{c['name']}={c['value']}" for c in cookies if 'name' in c and 'value' in c]
        elif isinstance(cookies, dict):
            parts = [f"{k}={v}" for k,v in cookies.items()]
        else:
            return ""
        print(f"✅ Loaded {len(parts)} cookies from {COOKIES_PATH.name}")
        return "; ".join(parts)
    except Exception as e:
        print(f"⚠ Cookie load error: {e}")
        return ""

COOKIE_STR = load_cookie_header()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    **({"Cookie": COOKIE_STR} if COOKIE_STR else {}),
}

# ─────────────────────────── DB ────────────────────────────────────
def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS repair_cases (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source_url  TEXT UNIQUE,
        board       TEXT,
        symptom     TEXT,
        rails_tested TEXT,
        components  TEXT,
        root_cause  TEXT,
        fix         TEXT,
        solved      INTEGER DEFAULT 0,
        raw_text    TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_board  ON repair_cases(board);
    CREATE INDEX IF NOT EXISTS idx_solved ON repair_cases(solved);

    -- FTS5 virtual table for fast full-text search
    CREATE VIRTUAL TABLE IF NOT EXISTS repair_fts USING fts5(
        board, symptom, rails_tested, components, root_cause, fix, raw_text,
        content='repair_cases', content_rowid='id',
        tokenize='porter unicode61'
    );

    -- Keep FTS in sync
    CREATE TRIGGER IF NOT EXISTS fts_insert AFTER INSERT ON repair_cases BEGIN
        INSERT INTO repair_fts(rowid,board,symptom,rails_tested,components,root_cause,fix,raw_text)
        VALUES(new.id,new.board,new.symptom,new.rails_tested,new.components,new.root_cause,new.fix,new.raw_text);
    END;
    """)
    con.commit()
    return con


def rebuild_fts():
    """Drop and recreate FTS table, then rebuild index from existing data"""
    con = sqlite3.connect(DB_PATH)
    # Always drop and recreate to handle corruption
    con.executescript("""
    DROP TABLE IF EXISTS repair_fts;
    CREATE VIRTUAL TABLE repair_fts USING fts5(
        board, symptom, rails_tested, components, root_cause, fix, raw_text,
        content='repair_cases', content_rowid='id',
        tokenize='porter unicode61'
    );
    """)
    con.execute("""
        INSERT INTO repair_fts(rowid,board,symptom,rails_tested,components,root_cause,fix,raw_text)
        SELECT id,board,symptom,rails_tested,components,root_cause,fix,raw_text FROM repair_cases
        WHERE symptom != 'extraction_failed'
    """)
    con.commit()
    count = con.execute("SELECT COUNT(*) FROM repair_fts").fetchone()[0]
    con.close()
    print(f"✅ FTS rebuilt: {count} documents indexed")

# ─────────────────────────── HTML helpers ──────────────────────────
class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.texts = []
        self._skip = False
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','nav','header','footer'): self._skip = True
    def handle_endtag(self, tag):
        if tag in ('script','style','nav','header','footer'): self._skip = False
    def handle_data(self, data):
        if not self._skip:
            s = data.strip()
            if s: self.texts.append(s)

def fetch(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read().decode('utf-8', errors='ignore')
        except Exception as e:
            if i == retries-1: raise
            time.sleep(2 ** i)

def html_to_text(html):
    p = TextExtractor()
    p.feed(html)
    return ' '.join(p.texts)

# ─────────────────────────── Thread discovery ──────────────────────
def get_thread_urls(max_pages=50, start_page=1):
    """Get thread URLs by browsing forum listing pages directly"""
    urls = []
    # XenForo forum listing pages
    # Only forum 15 — the main repair forum with 12,700 threads
    FORUM = "/forums/macbook-logic-board-repair-questions.15/"

    # Start from page 1, go up to max_pages
    # Each page has ~20 threads → 635 pages for full coverage
    consecutive_empty = 0
    for pg in range(start_page, start_page + max_pages):
        url = BASE_URL + FORUM + (f"page-{pg}" if pg > 1 else "")
        try:
            html = fetch(url)
            found = re.findall(r'href="(/threads/[^"]+\.\d+/)"', html)
            new_count = 0
            for u in found:
                # Normalize — strip /latest /unread /page-N suffixes
                clean = re.sub(r'/(latest|unread|page-\d+)/?$', '/', BASE_URL + u)
                if clean not in urls:
                    urls.append(clean)
                    new_count += 1
            print(f"  page {pg}: {new_count} new threads (total {len(urls)})")
            if new_count == 0:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    print("  → no more threads, stopping")
                    break
            else:
                consecutive_empty = 0
        except Exception as e:
            print(f"  ⚠ page {pg} failed: {e}")
            consecutive_empty += 1
            if consecutive_empty >= 5: break
        time.sleep(1.0)
    return urls

# ─────────────────────────── GPT extraction ────────────────────────
def extract_case(thread_text, url):
    """Use GPT-5.4 to extract structured repair case from thread text"""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("⚠ OPENAI_API_KEY not set")
        return None

    # Truncate long threads
    text = thread_text[:4000]

    prompt = f"""You are extracting structured data from a MacBook board repair forum thread.

Thread URL: {url}
Thread text:
{text}

Extract a JSON object with these fields (use null if not found):
{{
  "board": "board number like 820-02016 or null",
  "symptom": "main symptom in 1 sentence",
  "rails_tested": [{{"rail": "PP3V3_G3H", "value": "0V", "expected": "3.3V"}}],
  "components": ["U3100", "CD3217"],
  "root_cause": "what was wrong",
  "fix": "what was done to fix it",
  "solved": true/false
}}

Return ONLY valid JSON, no markdown, no explanation."""

    payload = json.dumps({
        "model": "gpt-4o-mini",  # cheaper for bulk extraction
        "max_completion_tokens": 400,
        "messages": [
            {"role": "system", "content": "Extract structured repair data. Return only JSON."},
            {"role": "user", "content": prompt}
        ]
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
            raw = resp["choices"][0]["message"]["content"].strip()
            # Strip markdown fences if present
            raw = re.sub(r'^```(?:json)?\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
            return json.loads(raw)
    except Exception as e:
        print(f"  ⚠ GPT error: {e}")
        return None

# ─────────────────────────── Main scraper ──────────────────────────
def scrape(max_threads=200, max_pages=50, start_page=1):
    con = init_db()
    existing = {row[0] for row in con.execute("SELECT source_url FROM repair_cases")}

    print(f"🔍 Discovering threads (pages {start_page}-{start_page+max_pages-1})...")
    urls = get_thread_urls(max_pages=max_pages, start_page=start_page)
    print(f"   Found {len(urls)} threads, {len(existing)} already in DB")

    new_urls = [u for u in urls if u not in existing][:max_threads]
    print(f"   Processing {len(new_urls)} new threads\n")

    saved = 0
    for i, url in enumerate(new_urls):
        print(f"[{i+1}/{len(new_urls)}] {url}")
        try:
            html  = fetch(url)
            text  = html_to_text(html)

            # Quick filter — only process MacBook board repair threads
            if not re.search(r'820-\d{3,5}|ppbus|pp3v3|pp5v|no charge|no power|backlight', text, re.I):
                print("   ⏭ skip — no board content")
                continue
            # Skip meta threads
            if re.search(r'forum.rules|sign.?up|troubleshooting.guide|tools.list', text[:200], re.I):
                print("   ⏭ skip — meta thread")
                continue

            case = extract_case(text, url)
            if not case or not isinstance(case, dict):
                print("   ⏭ skip — extraction failed")
                # Still mark URL as seen to avoid re-processing
                con.execute("INSERT OR IGNORE INTO repair_cases (source_url, board, symptom, solved) VALUES (?,?,?,?)",
                    (url, None, "extraction_failed", 0))
                con.commit()
                continue

            # Save to DB
            # Normalize board number: 820-01700-A → 820-01700
            raw_board = case.get("board") or ""
            board_norm = re.sub(r"(820-\d{5}).*", r"\1", raw_board) or None

            con.execute("""
                INSERT OR IGNORE INTO repair_cases
                (source_url, board, symptom, rails_tested, components,
                 root_cause, fix, solved, raw_text)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                url,
                board_norm,
                case.get("symptom"),
                json.dumps(case.get("rails_tested") or []),
                json.dumps(case.get("components") or []),
                case.get("root_cause"),
                case.get("fix"),
                1 if case.get("solved") else 0,
                text[:2000]
            ))
            con.commit()
            saved += 1
            print(f"   ✅ board={case.get('board')} solved={case.get('solved')} | {case.get('symptom','')[:60]}")

        except Exception as e:
            print(f"   ❌ {e}")

        time.sleep(1.5)  # be polite to server

    con.close()
    print(f"\n✅ Done — saved {saved} new cases to {DB_PATH}")

# ─────────────────────────── Search function ───────────────────────
def search_cases(query, board=None, limit=5):
    """Search repair cases using FTS5 — fast and semantic"""
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    if not board:
        m = re.search(r'820-\d{5}', query)
        if m: board = m.group()

    # Clean query for FTS5 — extract meaningful terms
    fts_terms = re.sub(r'820-\d+', '', query)
    fts_terms = re.sub(r'[^\w\s]', ' ', fts_terms)
    # Keep: net names, component refs, key repair words
    # Remove: common stopwords that hurt FTS precision
    stopwords = {'the','and','with','from','that','this','have','been','still',
                 'same','found','take','out','replace','already','not','but',
                 'for','its','was','also','check','measure','get','put','did'}
    tokens = [t for t in fts_terms.split() if len(t) > 2 and t.lower() not in stopwords]
    # Prioritize: net names (PP*), component refs (U/F/C + digits), voltages
    priority = [t for t in tokens if re.match(r'^(PP|U\d|F\d|C\d|R\d|Q\d)', t, re.I)]
    rest = [t for t in tokens if t not in priority]
    # FTS5 OR query — priority terms first
    ordered = priority + rest
    fts_terms = ' OR '.join(f'"{t}"' for t in ordered[:8]) if ordered else ''
    # Fallback: plain terms if OR query fails
    fts_plain = ' '.join(ordered[:6])

    rows = []
    try:
        if board and fts_terms:
            # FTS5 search within board — try OR query first, fallback to plain
            sql = """
                SELECT r.board, r.symptom, r.rails_tested, r.components,
                       r.root_cause, r.fix, r.solved, r.source_url,
                       (COALESCE(r.is_wiki,0) * 10 + r.solved * 3
                        + (CASE WHEN r.rails_tested != '[]' THEN 2 ELSE 0 END)
                        + (CASE WHEN r.root_cause IS NOT NULL THEN 1 ELSE 0 END)) AS score
                FROM repair_fts f
                JOIN repair_cases r ON r.id = f.rowid
                WHERE repair_fts MATCH ? AND r.board = ?
                  AND r.symptom != 'extraction_failed'
                ORDER BY score DESC, rank
                LIMIT ?
            """
            try:
                rows = con.execute(sql, (fts_terms, board, limit)).fetchall()
            except Exception:
                # Fallback to plain FTS if OR query syntax fails
                rows = con.execute(sql, (fts_plain, board, limit)).fetchall()

        # LIKE keyword fallback if FTS found nothing
        if not rows and board:
            keywords = [t for t in fts_plain.split() if len(t) > 3][:3]
            for kw in keywords:
                kw_like = f'%{kw}%'
                try:
                    like_sql = """
                        SELECT board, symptom, rails_tested, components,
                               root_cause, fix, solved, source_url,
                               (solved*3 + (CASE WHEN root_cause IS NOT NULL THEN 1 ELSE 0 END)) as score
                        FROM repair_cases
                        WHERE board = ? AND (symptom LIKE ? OR fix LIKE ? OR root_cause LIKE ?)
                          AND symptom != 'extraction_failed'
                        ORDER BY score DESC LIMIT ?
                    """
                    rows = con.execute(like_sql, (board, kw_like, kw_like, kw_like, limit)).fetchall()
                    if rows: break
                except: pass

        # Last resort: any board cases with measurements
        if not rows and board:
            sql = """
                SELECT board, symptom, rails_tested, components,
                       root_cause, fix, solved, source_url
                FROM repair_cases
                WHERE board = ? AND symptom != 'extraction_failed'
                  AND (rails_tested != '[]' OR root_cause IS NOT NULL)
                ORDER BY solved DESC, id DESC
                LIMIT ?
            """
            rows = con.execute(sql, (board, limit)).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            d.pop('score', None)
            d['rails_tested'] = json.loads(d.get('rails_tested') or '[]')
            d['components']   = json.loads(d.get('components')   or '[]')
            result.append(d)
        con.close()
        return result
    except Exception as e:
        con.close()
        print(f"Search error: {e}")
        return []

def format_cases_for_prompt(cases):
    """Format repair cases as actionable patterns for AI prompt."""
    if not cases: return ""
    lines = ["\n[Similar repair cases from forum database]:"]
    for case in cases:
        solved_tag = "SOLVED" if case.get('solved') else "unsolved"
        lines.append(f"\n  Case ({solved_tag}):")
        if case.get('symptom'):
            lines.append(f"    Symptom: {case['symptom']}")
        rails = case.get('rails_tested', [])
        if rails:
            for r in rails[:4]:
                rail = r.get('rail',''); val = r.get('value','')
                if rail and val:
                    lines.append(f"    Measured: {rail} = {val}")
        comps = case.get('components', [])
        if comps:
            lines.append(f"    Components involved: {', '.join(str(x) for x in comps[:5])}")
        if case.get('root_cause'):
            lines.append(f"    Root cause: {case['root_cause']}")
        if case.get('fix'):
            lines.append(f"    Fix: {case['fix']}")
    lines.append("\n  Use these cases to inform your next suggestion — do not cite them directly.")
    return "\n".join(lines)

# ─────────────────────────── CLI ───────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scrape",  action="store_true", help="Run scraper")
    parser.add_argument("--max",        type=int, default=200,  help="Max threads to scrape per run")
    parser.add_argument("--pages",      type=int, default=50,   help="Max forum pages to scan")
    parser.add_argument("--start-page", type=int, default=1,    help="Start from this forum page (for resuming)")
    parser.add_argument("--query",   type=str, help="Test search query")
    parser.add_argument("--board",   type=str, help="Filter by board number")
    parser.add_argument("--stats",   action="store_true", help="Show DB stats")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild FTS5 index")
    args = parser.parse_args()

    if args.stats or (not args.scrape and not args.query):
        if DB_PATH.exists():
            con = sqlite3.connect(DB_PATH)
            total  = con.execute("SELECT COUNT(*) FROM repair_cases").fetchone()[0]
            solved = con.execute("SELECT COUNT(*) FROM repair_cases WHERE solved=1").fetchone()[0]
            boards = con.execute("SELECT board, COUNT(*) FROM repair_cases GROUP BY board ORDER BY 2 DESC LIMIT 10").fetchall()
            con.close()
            print(f"\n📊 DB Stats: {total} cases, {solved} solved")
            print("Top boards:")
            for b,n in boards: print(f"  {b or '?':15} {n} cases")
        else:
            print("⚠ DB not found — run with --scrape first")

    if args.scrape:
        scrape(max_threads=args.max, max_pages=args.pages, start_page=args.start_page)

    if args.rebuild:
        rebuild_fts()

    if args.query:
        cases = search_cases(args.query, board=args.board)
        print(f"\n🔍 Results for '{args.query}':")
        print(format_cases_for_prompt(cases) or "  לא נמצא")
