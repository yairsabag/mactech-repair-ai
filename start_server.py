#!/usr/bin/env python3
import os, json, re, sys, urllib.parse, urllib.request, urllib.error
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# Load repair cases DB if available
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from repair_scraper import search_cases, format_cases_for_prompt
    HAS_CASES_DB = True
    print("✅ Repair cases DB loaded")
except ImportError:
    HAS_CASES_DB = False
    def search_cases(*a, **kw): return []
    def format_cases_for_prompt(c): return ""

try:
    from power_tree import format_tree_for_prompt, detect_subsystem
    HAS_POWER_TREE = True
except ImportError:
    HAS_POWER_TREE = False
    def format_tree_for_prompt(*a, **kw): return ""

try:
    from obd_loader import get_net_refs, format_refs_for_prompt, format_single_net
    HAS_OBD = True
    print("✅ OpenBoardData loaded")
except ImportError:
    HAS_OBD = False
    def get_net_refs(*a, **kw): return []
    def format_refs_for_prompt(*a, **kw): return ""
    def format_single_net(*a, **kw): return ""

try:
    from diagnostic_protocols import format_protocol_for_prompt
    HAS_PROTOCOLS = True
except ImportError:
    HAS_PROTOCOLS = False
    def format_protocol_for_prompt(*a, **kw): return ""

try:
    from component_db import enrich_boardview_context
    HAS_COMP_DB = True
except ImportError:
    HAS_COMP_DB = False
    def enrich_boardview_context(*a, **kw): return ""

try:
    from decision_engine import get_next_decision
    HAS_DECISION = True
except ImportError:
    HAS_DECISION = False
    def get_next_decision(*a, **kw): return {"decision_type":"unknown","ask":""}

try:
    from case_intake import parse_case_intake, infer_next_action, intake_to_state_summary
    HAS_INTAKE = True
except ImportError:
    HAS_INTAKE = False
    def parse_case_intake(*a,**kw): return None
    def infer_next_action(*a,**kw): return None
    def intake_to_state_summary(*a,**kw): return ""


try:
    from embed_search import search_semantic, format_semantic_cases
    HAS_EMBEDDINGS = True
    print("✅ Semantic search (embeddings) loaded")
except ImportError:
    HAS_EMBEDDINGS = False
    def search_semantic(*a, **kw): return []
    def format_semantic_cases(c): return ""

# Load OBD reference values if available
try:
    from obd_importer import get_net_refs, format_refs_for_prompt
    HAS_OBD = True
    print("✅ OpenBoardData reference loaded")
except ImportError:
    HAS_OBD = False
    def get_net_refs(*a, **kw): return {}
    def format_refs_for_prompt(r): return ""


DB_ROOT = Path(os.environ.get("REPAIR_AI_DB", Path.home() / "repair_ai_db"))
try:
    INDEX = json.loads((DB_ROOT / "index.json").read_text())
    BOARDS = {b["board_number"]: b for b in INDEX["boards"] if b.get("board_number")}
except FileNotFoundError:
    INDEX = {"boards": []}
    BOARDS = {}
    print(f"⚠ No index.json at {DB_ROOT} — running without board data")
SEARCH_INDEX = {}

def build_search_index():
    try:
        import fitz
    except ImportError:
        print("⚠  pip install pymupdf  — schematic search disabled"); return
    print("Building search index...")
    for board_num, board in BOARDS.items():
        idx = {}
        for sch in board.get("files", {}).get("schematics", []):
            pdf_path = Path(sch.get("path", ""))
            if not pdf_path.exists(): continue
            try:
                doc = fitz.open(str(pdf_path))
                for i, page in enumerate(doc):
                    text = page.get_text().upper()
                    tokens = set(re.findall(r'\b[CRULQDFTYBIJZ]\d{1,4}[A-Z]?\b', text))
                    tokens |= set(re.findall(r'\bPP[0-9A-Z_]{2,25}\b', text))
                    tokens |= set(re.findall(r'\b[A-Z]{2,6}\d{3,5}\b', text))
                    for tok in tokens:
                        idx.setdefault(tok, [])
                        if (i+1) not in idx[tok]: idx[tok].append(i+1)
                doc.close()
            except: pass
        SEARCH_INDEX[board_num] = idx
        if idx: print(f"  ✓ {board_num}: {len(idx)} terms")

HTML = open(Path(__file__).parent / "app.html").read() if (Path(__file__).parent / "app.html").exists() else "<h1>app.html missing</h1>"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length", len(body)); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path); route = p.path
        if route in ("/","index.html"):
            body=HTML.encode(); self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",len(body)); self.end_headers(); self.wfile.write(body)
        elif route=="/api/boards":
            self.send_json({n:{"model":b.get("model","?"),"chip":b.get("chip","?")} for n,b in BOARDS.items() if n})
        elif route.startswith("/api/board/"):
            num=route.split("/api/board/")[1]; self.send_json(BOARDS.get(num,{"error":"not found"}))
        elif route.startswith("/api/boardview/"):
            num=route.split("/api/boardview/")[1]; bvp=DB_ROOT/num/"boardview_parsed.json"
            self.send_json(json.loads(bvp.read_text()) if bvp.exists() else {"error":"run boardview_parser.py"})
        elif route.startswith("/api/search/"):
            parts=route.split("/api/search/")[1].split("/",1); num=parts[0]
            term=urllib.parse.unquote(parts[1]).upper() if len(parts)>1 else ""
            idx2=SEARCH_INDEX.get(num,{})
            pages=list(idx2.get(term,[]))
            if not pages:
                for k,v in idx2.items():
                    if term in k:
                        for p in v:
                            if p not in pages: pages.append(p)
            pages.sort()
            bvp=DB_ROOT/num/"boardview_parsed.json"
            in_bv=False; bv_nets=[]; bv_comps=[]
            if bvp.exists():
                bvd=json.loads(bvp.read_text())
                in_bv=any(c2.get("ref","")==term for c2 in bvd.get("components",[]))
                bv_nets=[n["name"] for n in bvd.get("nets",[]) if term.lower() in n["name"].lower()][:20]
                bv_comps=[c2["ref"] for c2 in bvd.get("components",[]) if term.lower() in c2.get("ref","").lower()][:10]
            self.send_json({"pages":pages,"in_boardview":in_bv,"bv_nets":bv_nets,"bv_comps":bv_comps})
        elif route=="/img":
            params=dict(urllib.parse.parse_qsl(p.query)); fp=Path(params.get("path",""))
            if fp.exists() and fp.suffix.lower() in (".png",".jpg",".jpeg"):
                data=fp.read_bytes(); self.send_response(200)
                self.send_header("Content-Type","image/png"); self.send_header("Content-Length",len(data)); self.end_headers(); self.wfile.write(data)
            else: self.send_response(404); self.end_headers()
        else: self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path=="/api/chat":
            n=int(self.headers.get("Content-Length",0)); body=json.loads(self.rfile.read(n))
            self.send_json({"reply":ai_chat(body.get("message",""),body.get("board_context",""),body.get("history",[]))})

def _load_boardview_index(board_match):
    """Return (nets_set, comps_set) from boardview — ground truth."""
    if not board_match:
        return set(), set()
    bvp = DB_ROOT / board_match / "boardview_parsed.json"
    if not bvp.exists():
        return set(), set()
    try:
        bvd = json.loads(bvp.read_text())
        nets  = {n["name"] for n in bvd.get("nets",  []) if n.get("name")}
        comps = {c["ref"]  for c in bvd.get("components", []) if c.get("ref")}
        return nets, comps
    except:
        return set(), set()


def ai_chat(msg, ctx, history=[]):
    key = os.environ.get("OPENAI_API_KEY")
    if not key: return "Set OPENAI_API_KEY first."
    import urllib.request

    # Extract board number
    board_match = None
    m = re.search(r'820-\d{5}', ctx + msg)
    if m: board_match = m.group()

    # Load REAL boardview — only these nets/components exist on this board
    real_nets, real_comps = _load_boardview_index(board_match)

    # Build nets context — only real nets
    nets_ctx = ""
    if real_nets:
        key_nets = sorted([n for n in real_nets if any(k in n for k in
            ["PPBUS","PP3V","PP1V","PP5V","PP2V","PPDCIN","PPVBUS","PPVIN",
             "PPVOUT","LUXE","BKLT","AON","_S2","_S1","_S0","AWAKE"])])[:80]
        bklt_comps = sorted([c for c in real_comps if any(k in c for k in
            ["FP","QP","UP","RP8","CP8","KP","DP"])])[:25]
        if key_nets:
            nets_ctx  = "\n\nActual rails on THIS board (ONLY use these — no others exist):\n"
            nets_ctx += ", ".join(key_nets)
        if bklt_comps:
            nets_ctx += "\nBacklight-area components on this board: " + ", ".join(bklt_comps)
        # Add OBD reference values for key nets
        if HAS_OBD and board_match:
            obd_ctx = format_refs_for_prompt(board_match, key_nets[:40])
            if obd_ctx:
                nets_ctx += obd_ctx

        # Add component descriptions for components on relevant nets
        if HAS_COMP_DB and board_match:
            bvp = DB_ROOT / board_match / "boardview_parsed.json"
            if bvp.exists():
                try:
                    bvd_data = json.loads(bvp.read_text())
                    comp_ctx = enrich_boardview_context(board_match, key_nets[:20], bvd_data)
                    if comp_ctx:
                        nets_ctx += comp_ctx
                except: pass

    # Semantic case search
    cases = []
    cases_ctx = ""
    if board_match:
        # Build rich search query: combine first user msg + current msg for better case matching
        first_msg = next((m["content"] for m in (history or []) if m.get("role")=="user"), msg)
        search_query = f"{first_msg} {msg}".strip()
        if HAS_EMBEDDINGS:
            cases = search_semantic(search_query, board=board_match, limit=4)
            cases_ctx = format_semantic_cases(cases) if cases else ""
        if not cases_ctx and HAS_CASES_DB:
            cases = search_cases(search_query + " " + ctx, board=board_match, limit=4)
            cases_ctx = format_cases_for_prompt(cases) if cases else ""

    # Diagnostic protocol (step-by-step)
    proto_ctx = ""
    first_user_msg = next((m["content"] for m in (history or []) if m.get("role")=="user"), msg)
    if HAS_PROTOCOLS and board_match:
        proto_ctx = format_protocol_for_prompt(board_match, first_user_msg, history)
        if not proto_ctx:
            proto_ctx = format_protocol_for_prompt(board_match, msg, history)

    # ── Pipeline: intake → state → decision → (cases if needed) ─────
    intake = None
    intake_action = None
    if HAS_INTAKE and board_match:
        # Parse the FIRST user message (richest with context)
        # Also try current message if it's long (>50 chars = likely info-rich)
        intake_src = first_user_msg if len(first_user_msg) > 30 else msg
        if len(msg) > len(first_user_msg): intake_src = msg
        recent_user = [m["content"] for m in (history or []) if m.get("role")=="user"][-3:]
        intake = parse_case_intake(intake_src, board_match, recent_user_msgs=recent_user)
        if intake and intake.confidence > 0.5:
            intake_action = infer_next_action(intake)

    de = {"decision_type": "unknown", "ask": ""}
    if intake_action:
        de = intake_action  # intake overrides decision engine
    elif HAS_DECISION and board_match:
        de = get_next_decision(board_match, first_user_msg, history or [], msg)

        # If no hard rule fired, retrieve similar cases
        if de.get("decision_type") not in ("final_fix",) and board_match:
            search_query = f"{first_user_msg} {msg}".strip()
            retrieved_cases = []
            if HAS_EMBEDDINGS:
                retrieved_cases = search_semantic(search_query, board=board_match, limit=4)
            if not retrieved_cases and HAS_CASES_DB:
                retrieved_cases = search_cases(search_query, board=board_match, limit=4)
            if retrieved_cases:
                cases_ctx = format_cases_for_prompt(retrieved_cases) if HAS_CASES_DB else ""

    # Power tree fallback
    tree_ctx = ""
    if not proto_ctx and HAS_POWER_TREE and board_match:
        tree_ctx = format_tree_for_prompt(board_match, msg)

    # System prompt
    # Build system prompt — inject protocol directly so AI cannot ignore it
    base_rules = (
        "You are an expert MacBook board-level repair technician assistant. "
        "You guide technicians step by step through board-level diagnosis. "
        "\n\nCRITICAL RULES:"
        "\n1) ONE action per reply — one measurement or one physical check only."
        "\n2) ONLY mention nets and components that appear in the [Actual rails] list. Never invent net names."
        "\n3) If 0V + short (<0.1Ω): say inject 1V/3A to locate the shorted component."
        "\n4) If 0V + no short: say check the fuse on that rail."
        "\n5) If low voltage: partial short — check diode mode to ground."
        "\n6) Be conversational but precise. 1-2 sentences max."
        "\n7) When the user confirms something (yes/done/ok), move to the NEXT step immediately.""\n8) When a component heats during injection AND you have isolation evidence (short gone after lift) → tell them to replace it.""\n9) Never repeat a question the user already answered."
        "\n8) Never repeat a question the user already answered."
    )

    # Build system prompt from decision engine output
    de_type = de.get("decision_type", "unknown")
    de_ask  = de.get("ask", "")
    de_state = de.get("state", {})
    state_lines = []
    if de_state.get("measurements"):
        state_lines.append("Already measured: " + ", ".join(f"{k}={v}" for k,v in de_state["measurements"].items()))
    if de_state.get("heated"):
        state_lines.append("Heated during injection: " + ", ".join(de_state["heated"]))
    state_summary = "\n".join(state_lines)

    # Add intake summary if available
    intake_summary = ""
    if intake and intake.confidence > 0.4:
        intake_summary = intake_to_state_summary(intake)
        if state_summary:
            state_summary = intake_summary + "\n" + state_summary
        else:
            state_summary = intake_summary

    if de_type == "final_fix":
        comp = de.get("component","")
        reason = de.get("reason","")
        system_prompt = (
            base_rules
            + f"\n\n[DECISION ENGINE — FINAL FIX]:"
            + f"\nComponent: {comp}"
            + f"\nReason: {reason}"
            + "\n\nTell the technician to replace this component. One sentence. Be definitive."
        )
    elif de_type in ("ask_measurement","check_short","inject","ask_confirm","check_fuse") and de_ask:
        de_reason = de.get("reason", "")
        system_prompt = (
            "You are a MacBook board-level repair assistant. "
            "The diagnostic engine has determined the exact next step. "
            "\nYou MUST follow it precisely."
            "\nDo NOT add other measurements, rails, or components not listed below."
            "\nDo NOT use your own knowledge to suggest additional checks."
            + (f"\n\n[Known facts]:\n{state_summary}" if state_summary else "")
            + (f"\n[Reason]: {de_reason}" if de_reason else "")
            + f"\n\n[EXACT NEXT ACTION]:\n{de_ask}"
            + "\n\nRephrase this naturally in 1-2 sentences. Say ONLY what is above. Nothing extra."
        )
    elif proto_ctx:
        system_prompt = (
            base_rules
            + "\n\n" + proto_ctx.strip()
            + (f"\n\n[What we know so far]:\n{state_summary}" if state_summary else "")
            + "\n\nAsk for the ONE measurement in the protocol. Never loop. Never mention other nets."
        )
    else:
        system_prompt = base_rules
        if state_summary:
            system_prompt += f"\n\n[What we know]:\n{state_summary}"

    # Build user content
    user_content = f"{ctx}\n{msg}"
    if not proto_ctx and tree_ctx:
        user_content += tree_ctx
    # Cases go into system prompt so AI uses them for reasoning, not quoting
    if cases_ctx:
        system_prompt += f"\n\n[Similar forum cases — use for pattern matching only, do not quote]:\n{cases_ctx}"
    if nets_ctx:
        user_content += nets_ctx

    # Build messages with history
    messages = [{"role": "system", "content": system_prompt}]
    for h in (history or [])[-10:]:
        if h.get("role") in ("user","assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_content})

    payload = json.dumps({
        "model": "gpt-5.4",
        "max_completion_tokens": 400,
        "messages": messages
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return f"Error {e.code}: {body[:200]}"
    except Exception as e:
        return f"Error: {e}"


if __name__=="__main__":
    PORT=int(os.environ.get("PORT", 8765)); build_search_index()
    print(f"✅ http://localhost:{PORT}  |  {len(BOARDS)} boards loaded")
    HTTPServer(("",PORT),Handler).serve_forever()
