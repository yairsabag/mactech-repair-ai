#!/usr/bin/env python3
"""
repair.wiki scraper
====================
Scrapes structured repair guides from repair.wiki and adds them to the DB.
Wiki guides are higher quality than forum threads — step-by-step, with
exact test points and reference values.

Usage:
    python3 wiki_scraper.py --scrape
    python3 wiki_scraper.py --query "5v stuck" --board 820-02016
"""

import urllib.request, urllib.error, json, sqlite3, re, time, sys, argparse
from html.parser import HTMLParser
from pathlib import Path

DB_PATH  = Path.home() / "repair_ai_db" / "repair_cases.db"
BASE_URL = "https://repair.wiki"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}

# Known MacBook repair guide URLs on repair.wiki
# Known confirmed pages (already scraped)
WIKI_PAGES_CONFIRMED = [
    "/w/MacBook_Air_A2337_Not_turning_on,_0.00-0.05a_current_draw_at_5V_repair",
    "/w/MacBook_Air_A2337_Not_turning_on,_0.20-0.25a_current_draw_at_5V_with_power_cycling_of_the_USB-C_amp_meter_repair",
    "/w/MacBook_Air_A2337_No_backlight_on_display_repair",
    "/w/MacBook_Air_A2337",
    "/w/MacBook_Pro_A2338_Not_turning_on,_0.00_-_0.05A_current_draw_at_5V_repair",
    "/w/MacBook_Pro_A2338_Not_turning_on,_current_draw_cycling_between_0.00_and_0.45a_at_5V_repair",
    "/w/MacBook_Pro_A2338_No_backlight_on_display_repair",
    "/w/MacBook_Pro_A2338_camera_not_detected_after_history_of_liquid_damage_repair",
    "/w/MacBook_Pro_A2338",
    "/w/Macbook_Air_820-02536_Does_not_run_from_battery",
    "/w/MacBook_Pro_2021_A2442_Water_Damage_No_Keyboard_Repair",
    "/w/CD3217_compatibility",
    "/w/Macbook_Board_and_Model_numbers",
]

# Model pages to discover repair guides from
MODEL_PAGES = [
    "/w/MacBook_Air_A2337",
    "/w/MacBook_Pro_A2338",
    "/w/MacBook_Air_A2681",
    "/w/Macbook_Air_A2681",
    "/w/MacBook_Pro_A2442",
    "/w/MacBook_Pro_A2485",
    "/w/MacBook_Pro_A2779",
    "/w/MacBook_Pro_A2780",
    "/w/MacBook_Air_A2941",
    "/w/MacBook_Air_A3113",
    "/w/Macbook_Air_A3113",
    "/w/MacBook_Pro_A2992",
]

def discover_wiki_pages():
    """Discover repair guide URLs by scraping model pages and category pages."""
    import re
    urls = list(WIKI_PAGES_CONFIRMED)
    
    # Scrape model pages for linked repair guides
    for path in MODEL_PAGES:
        url = BASE_URL + path
        try:
            html = fetch(url)
            # Find links to repair guides
            found = re.findall(r'href="(/w/[^"]+(?:repair|Repair)[^"]*)"', html)
            for u in found:
                # Only MacBook repair pages
                if (u not in urls 
                    and 'action=' not in u 
                    and 'Special:' not in u
                    and 'Category:' not in u
                    and 'Property:' not in u
                    and any(m in u for m in ['MacBook','Macbook','A2337','A2338','A2442',
                                              'A2485','A2681','A2779','A2780','A2941',
                                              'A3113','A3114','A2992','820-0'])):
                    urls.append(u)
            print(f"  {path.split('/')[-1][:40]}: found {len(found)} repair links")
        except Exception as e:
            print(f"  ⚠ {path}: {e}")
        time.sleep(1)
    
    # Also scrape the repair guide category pages
    cat_pages = [
        "/index.php?title=Category:Repair_guides_for_MacBook_Air_A2337",
        "/index.php?title=Category:Repair_guides_for_MacBook_Pro_A2338",
        "/index.php?title=Category:Repair_guides_for_MacBook_Air_A2681",
        "/index.php?title=Category:Repair_guides_for_MacBook_Pro_A2442",
    ]
    for cat in cat_pages:
        url = BASE_URL + cat
        try:
            html = fetch(url)
            found = re.findall(r'href="(/w/MacBook[^"]+)"', html)
            for u in found:
                if u not in urls and 'action=' not in u:
                    urls.append(u)
        except: pass
        time.sleep(1)
    
    return list(dict.fromkeys(urls))  # deduplicate preserving order

WIKI_PAGES = WIKI_PAGES_CONFIRMED  # fallback if discovery fails

# Board mappings
MODEL_TO_BOARD = {
    "A2337": "820-02016",
    "A2338": "820-02020",
    "A2681": "820-02536",
    "A2442": "820-02098",
    "A2485": "820-02100",
    "A2779": "820-02841",
    "A2780": "820-02652",
    "A2941": "820-03160",
    "A2918": "820-02757",
    "A2991": "820-02935",
    "A3113": "820-03285",
    "A3114": "820-03286",
}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.texts = []
        self._skip = False
        self._in_content = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag in ('script', 'style', 'nav', 'header', 'footer'):
            self._skip = True
        # Look for main content div
        if tag == 'div' and attrs_dict.get('id') in ('mw-content-text', 'bodyContent'):
            self._in_content = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'header', 'footer'):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            s = data.strip()
            if s:
                self.texts.append(s)


def fetch(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read().decode('utf-8', errors='ignore')
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(2 ** i)


def extract_board_from_url(url):
    """Extract board number from URL or page content"""
    # Check URL for model number
    for model, board in MODEL_TO_BOARD.items():
        if model in url:
            return board
    # Check for direct board number
    m = re.search(r'820-0\d{4}', url)
    if m:
        return m.group()
    return None


def extract_board_from_text(text):
    """Extract board number from page text"""
    m = re.search(r'820-0\d{4}', text)
    if m:
        return m.group()
    for model, board in MODEL_TO_BOARD.items():
        if model in text:
            return board
    return None


def parse_wiki_page(html, url):
    """Parse a repair.wiki page into structured case data"""
    p = TextExtractor()
    p.feed(html)
    text = ' '.join(p.texts)

    board = extract_board_from_url(url) or extract_board_from_text(text)

    # Extract symptom from title/URL
    slug = url.split('/w/')[-1].replace('_', ' ').replace('%22', '"')
    slug = urllib.parse.unquote(slug)
    symptom = re.sub(r'\s+repair$', '', slug, flags=re.I)

    # Extract rails and measurements
    rails = []
    # Pattern: "PPXXX_YYY ... normal voltage ... Xv" or "voltage ≈ Xv"
    rail_patterns = [
        r'(PP[A-Z0-9_]{3,30})[^\n]*?(\d+\.?\d*)\s*[Vv]',
        r'(PP[A-Z0-9_]{3,30})[^\n]*?normal[^\n]*?(\d+\.?\d*)\s*[Vv]',
    ]
    seen_rails = set()
    for pattern in rail_patterns:
        for m in re.finditer(pattern, text):
            rail = m.group(1)
            val  = m.group(2)
            if rail not in seen_rails and len(rail) < 35:
                rails.append({"rail": rail, "value": None, "expected": f"{val}V"})
                seen_rails.add(rail)
            if len(rails) >= 10:
                break

    # Extract components mentioned
    components = list(set(re.findall(r'\b([UFCRQL]\w{2,6})\b', text)))[:15]

    # Root cause and fix from text
    root_cause = None
    fix = None
    rc_m = re.search(r'(?:cause|caused by|root cause)[:\s]+([^.]{10,100})', text, re.I)
    if rc_m:
        root_cause = rc_m.group(1).strip()
    fix_m = re.search(r'(?:replace|resolv|fix)[ed\s]+([^.]{10,100})', text, re.I)
    if fix_m:
        fix = fix_m.group(1).strip()

    return {
        "board": board,
        "symptom": symptom[:200] if symptom else None,
        "rails_tested": rails,
        "components": components,
        "root_cause": root_cause,
        "fix": fix,
        "solved": 1,  # wiki guides are always solved/verified
        "raw_text": text[:3000],
        "source_url": url,
        "is_wiki": True,
    }


def init_db_wiki():
    """Add wiki source column if not present"""
    import sqlite3
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("ALTER TABLE repair_cases ADD COLUMN is_wiki INTEGER DEFAULT 0")
        con.commit()
        print("✅ Added is_wiki column")
    except Exception:
        pass  # Column already exists
    con.close()


def scrape_wiki():
    """Fetch and store all wiki pages"""
    init_db_wiki()
    con = sqlite3.connect(DB_PATH)
    existing = {row[0] for row in con.execute("SELECT source_url FROM repair_cases")}

    # Discover pages dynamically
    print("🔍 Discovering repair.wiki pages...")
    pages = discover_wiki_pages()
    print(f"   Found {len(pages)} pages total")
    
    saved = 0
    for path in pages:
        url = BASE_URL + path
        if url in existing:
            print(f"  ⏭ already in DB: {path.split('/')[-1][:50]}")
            continue

        print(f"  📥 {path.split('/')[-1][:60]}")
        try:
            html  = fetch(url)
            case  = parse_wiki_page(html, url)
            if not case.get("board"):
                print(f"     ⚠ no board detected, skipping")
                continue

            con.execute("""
                INSERT OR IGNORE INTO repair_cases
                (source_url, board, symptom, rails_tested, components,
                 root_cause, fix, solved, raw_text, is_wiki)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                url,
                case["board"],
                case["symptom"],
                json.dumps(case["rails_tested"]),
                json.dumps(case["components"]),
                case["root_cause"],
                case["fix"],
                case["solved"],
                case["raw_text"],
                1,
            ))
            con.commit()
            saved += 1
            print(f"     ✅ board={case['board']} | {case['symptom'][:60]}")
        except Exception as e:
            print(f"     ❌ {e}")
        time.sleep(1)

    # Rebuild FTS to include new wiki pages
    print("\n🔄 Rebuilding FTS index...")
    try:
        con.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS repair_fts USING fts5(
            board, symptom, rails_tested, components, root_cause, fix, raw_text,
            content='repair_cases', content_rowid='id',
            tokenize='porter unicode61'
        );
        """)
        con.execute("DELETE FROM repair_fts")
        con.execute("""
            INSERT INTO repair_fts(rowid,board,symptom,rails_tested,components,root_cause,fix,raw_text)
            SELECT id,board,symptom,rails_tested,components,root_cause,fix,raw_text
            FROM repair_cases WHERE symptom != 'extraction_failed'
        """)
        con.commit()
        count = con.execute("SELECT COUNT(*) FROM repair_fts").fetchone()[0]
        print(f"✅ FTS rebuilt: {count} documents")
    except Exception as e:
        print(f"⚠ FTS rebuild: {e}")

    con.close()
    print(f"\n✅ Done — saved {saved} wiki guides")


import urllib.parse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scrape", action="store_true")
    args = parser.parse_args()

    if args.scrape:
        scrape_wiki()
    else:
        parser.print_help()
