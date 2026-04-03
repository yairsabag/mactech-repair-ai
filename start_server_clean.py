#!/usr/bin/env python3
import os, json, re, sys, urllib.parse, urllib.request, urllib.error
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── .env loader ───────────────────────────────────────────────────────────────
_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

sys.path.insert(0, str(Path(__file__).parent))

# ── Optional modules ──────────────────────────────────────────────────────────
try:
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
    from embed_search import search_semantic, format_semantic_cases
    HAS_EMBEDDINGS = True
    print("✅ Semantic search (embeddings) loaded")
except ImportError:
    HAS_EMBEDDINGS = False
    def search_semantic(*a, **kw): return []
    def format_semantic_cases(c): return ""

try:
    from obd_importer import get_net_refs, format_refs_for_prompt
    HAS_OBD = True
    print("✅ OpenBoardData reference loaded")
except ImportError:
    pass  # already stubbed above

try:
    from case_intake import parse_case_intake, infer_next_action, intake_to_state_summary
    HAS_INTAKE = True
except ImportError:
    HAS_INTAKE = False
    def parse_case_intake(*a, **kw): return None
    def infer_next_action(*a, **kw): return None
    def intake_to_state_summary(*a, **kw): return ""

# ── Auth / rate / feedback ────────────────────────────────────────────────────
try:
    from auth import (init_db, auth_from_request, require_auth,
                      create_user, authenticate, get_user_by_id, issue_token,
                      revoke_token, create_invite, log_activity,
                      list_users, get_user_activity, check_rate)
    from rate_limiter import check_rate, purge_old_logs
    from feedback import save_feedback, get_feedback_summary, CATEGORIES
    HAS_AUTH = True
    init_db()
    print("✅ Auth + rate limiter loaded")
except ImportError as _e:
    HAS_AUTH = False
    print(f"⚠  Auth not loaded: {_e}")
    def require_auth(h, **kw): return {"id": 0, "role": "beta", "email": "anon"}, ""
    def check_rate(*a, **kw): return True, 0
    def save_feedback(*a, **kw): return True, "ok"
    def get_feedback_summary(): return {}
    def create_invite(*a, **kw): return "no-auth"
    def create_user(*a, **kw): return False, "auth not loaded"
    def get_user(*a, **kw): return None
    def issue_token(*a, **kw): return ""

# ── Decision Engine v3 ────────────────────────────────────────────────────────
try:
    from decision_engine_v3 import (
        RepairState, resolve_board_family,
        process_repair_turn, validate_llm_output,
        apply_current_shortcuts,
    )
    HAS_DECISION = True
    print("✅ Decision Engine v3 loaded")
except ImportError as _e:
    HAS_DECISION = False
    print(f"⚠  Decision Engine v3 not loaded: {_e}")
    class RepairState: pass

# In-memory repair states — keyed by (board, hash of first user message)
_REPAIR_STATES: dict = {}

# ── Model → board number map ──────────────────────────────────────────────────
MODEL_TO_BOARD = {
    'macbook air 13" m1': "820-02443", "macbook air m1": "820-02443",
    "a2337": "820-02443",
    'macbook air 13" m2': "820-02863", "macbook air m2": "820-02863",
    "a2681": "820-02863",
    'macbook air 15" m2': "820-03250", "a2941": "820-03250",
    'macbook pro 13" m1': "820-02773", "macbook pro m1": "820-02773",
    "a2338": "820-02773",
    'macbook pro 14" m1': "820-02840", "a2442": "820-02840",
    'macbook pro 16" m1': "820-02841", "a2485": "820-02841",
    'macbook pro 13" 2020': "820-02016", "a2251": "820-02016",
    'macbook pro 13" 2019': "820-01990", "a2159": "820-01990",
    'macbook pro 16" 2019': "820-01739", "a2141": "820-01739",
    "macbook air 2020": "820-02020", "macbook air intel": "820-02020",
    "a2179": "820-02020",
    "macbook air 2018": "820-00165", "macbook air 2019": "820-00165",
    "a1932": "820-00165",
}

def extract_board(text: str) -> str | None:
    """Extract board number from any text string."""
    m = re.search(r'820-\d{5}', text)
    if m:
        return m.group()
    sl = text.lower()
    for label, bnum in MODEL_TO_BOARD.items():
        if label in sl:
            return bnum
    return None

# ── DB / search index ─────────────────────────────────────────────────────────
DB_ROOT = Path.home() / "repair_ai_db"
INDEX   = json.loads((DB_ROOT / "index.json").read_text())
BOARDS  = {b["board_number"]: b for b in INDEX["boards"] if b.get("board_number")}
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

HTML         = open(Path(__file__).parent / "app.html").read()    if (Path(__file__).parent / "app.html").exists()    else "<h1>app.html missing</h1>"
LANDING_HTML = open(Path(__file__).parent / "landing.html").read() if (Path(__file__).parent / "landing.html").exists() else HTML
ADMIN_HTML = open(Path(__file__).parent / "admin.html").read() if (Path(__file__).parent / "admin.html").exists() else "<h1>admin.html missing</h1>"

# ── Boardview index ───────────────────────────────────────────────────────────
def _load_boardview_index(board_match):
    if not board_match: return set(), set()
    bvp = DB_ROOT / board_match / "boardview_parsed.json"
    if not bvp.exists(): return set(), set()
    try:
        bvd   = json.loads(bvp.read_text())
        nets  = {n["name"] for n in bvd.get("nets",  []) if n.get("name")}
        comps = {c["ref"]  for c in bvd.get("components", []) if c.get("ref")}
        return nets, comps
    except: return set(), set()

# ── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers(); self.wfile.write(body)

    def send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers(); self.wfile.write(body)

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        p = urllib.parse.urlparse(self.path); route = p.path

        if route in ("/", "/index.html"):
            return self.send_html(HTML)

        if route in ("/landing", "/join", "/beta"):
            return self.send_html(LANDING_HTML)

        if route in ("/admin", "/admin.html"):
            return self.send_html(ADMIN_HTML)

        if route == "/feedback-widget.js":
            fp = Path(__file__).parent / "feedback-widget.js"
            if fp.exists():
                body = fp.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Content-Length", len(body))
                self.end_headers(); self.wfile.write(body); return
            self.send_response(404); self.end_headers(); return

        if route == "/api/boards":
            return self.send_json({n: {"model": b.get("model","?"), "chip": b.get("chip","?")}
                                   for n, b in BOARDS.items() if n})

        if route.startswith("/api/board/"):
            num = route.split("/api/board/")[1]
            return self.send_json(BOARDS.get(num, {"error": "not found"}))

        if route.startswith("/api/boardview/"):
            num = route.split("/api/boardview/")[1]
            bvp = DB_ROOT / num / "boardview_parsed.json"
            return self.send_json(json.loads(bvp.read_text()) if bvp.exists()
                                  else {"error": "run boardview_parser.py"})

        if route.startswith("/api/search/"):
            parts = route.split("/api/search/")[1].split("/", 1); num = parts[0]
            term  = urllib.parse.unquote(parts[1]).upper() if len(parts) > 1 else ""
            idx2  = SEARCH_INDEX.get(num, {})
            pages = list(idx2.get(term, []))
            if not pages:
                for k, v in idx2.items():
                    if term in k:
                        for pg in v:
                            if pg not in pages: pages.append(pg)
            pages.sort()
            bvp = DB_ROOT / num / "boardview_parsed.json"
            in_bv = False; bv_nets = []; bv_comps = []
            if bvp.exists():
                bvd   = json.loads(bvp.read_text())
                in_bv = any(c2.get("ref","") == term for c2 in bvd.get("components",[]))
                bv_nets  = [n["name"] for n in bvd.get("nets",[])       if term in n["name"]][:8]
                bv_comps = [c2["ref"] for c2 in bvd.get("components",[]) if term in c2.get("ref","")][:6]
            return self.send_json({"pages": pages, "in_boardview": in_bv,
                                   "bv_nets": bv_nets, "bv_comps": bv_comps})

        if route == "/img":
            params = dict(urllib.parse.parse_qsl(p.query)); fp = Path(params.get("path",""))
            if fp.exists() and fp.suffix.lower() in (".png",".jpg",".jpeg"):
                data = fp.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", len(data))
                self.end_headers(); self.wfile.write(data); return
            self.send_response(404); self.end_headers(); return

        self.send_response(404); self.end_headers()

    # ── OPTIONS (CORS) ────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        n    = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        client_ip = self.client_address[0]

        # Register — username + password
        if self.path == "/api/register":
            ok, msg = create_user(
                username=body.get("username",""),
                password=body.get("password",""),
                email=body.get("email",""),
                invite_code=body.get("invite_code",""),
            )
            if not ok: return self.send_json({"error": msg}, 400)
            user = authenticate(body.get("username",""), body.get("password",""))
            if not user: return self.send_json({"error": "Auth error after register."}, 500)
            token = issue_token(user["id"], client_ip)
            return self.send_json({"token": token, "username": user["username"],
                                   "email": user.get("email",""), "role": user["role"]})

        # Login — username or email + password
        if self.path == "/api/login":
            user = authenticate(body.get("username","") or body.get("email",""),
                                body.get("password",""))
            if not user: return self.send_json({"error": "Wrong username or password."}, 401)
            if user["role"] == "blocked": return self.send_json({"error": "Account suspended."}, 403)
            token = issue_token(user["id"], client_ip)
            return self.send_json({"token": token, "username": user["username"],
                                   "email": user.get("email",""), "role": user["role"]})

        # Feedback
        if self.path == "/api/feedback":
            user, err = require_auth(dict(self.headers))
            if err and HAS_AUTH: return self.send_json({"error": err}, 401)
            uid = user.get("id", 0) if user else 0
            ok, msg = save_feedback(
                user_id=uid,
                category=body.get("category",""),
                board=body.get("board",""),
                symptom=body.get("symptom",""),
                note=str(body.get("note",""))[:500],
                de_step=body.get("de_step"),
                conversation=body.get("conversation"),
            )
            if not ok: return self.send_json({"error": msg}, 400)
            return self.send_json({"ok": True})

        # Admin — set user role (block/unblock)
        if self.path == "/api/admin/set_role":
            user, err = require_auth(dict(self.headers), min_role="admin")
            if err: return self.send_json({"error": err}, 403)
            from auth import set_role
            set_role(body.get("username",""), body.get("role","beta"))
            return self.send_json({"ok": True})

        # Admin — create invite
        if self.path == "/api/admin/invite":
            user, err = require_auth(dict(self.headers), min_role="admin")
            if err: return self.send_json({"error": err}, 403)
            code = create_invite(note=body.get("note",""),
                                 max_uses=int(body.get("max_uses",1)),
                                 created_by=user["email"])
            return self.send_json({"code": code})

        # Admin — user list
        if self.path == "/api/admin/users":
            user, err = require_auth(dict(self.headers), min_role="admin")
            if err: return self.send_json({"error": err}, 403)
            return self.send_json(list_users())

        # Admin — user activity detail
        if self.path == "/api/admin/user_activity":
            user, err = require_auth(dict(self.headers), min_role="admin")
            if err: return self.send_json({"error": err}, 403)
            uid = int(body.get("user_id", 0))
            return self.send_json(get_user_activity(uid))

        # Admin — feedback summary
        if self.path == "/api/admin/feedback":
            user, err = require_auth(dict(self.headers), min_role="admin")
            if err: return self.send_json({"error": err}, 403)
            return self.send_json(get_feedback_summary())

        # Chat
        if self.path == "/api/chat":
            user, _ = require_auth(dict(self.headers))
            if user and user.get("role") == "blocked":
                return self.send_json({"error": "Account suspended."}, 403)
            if user and user.get("id", 0) > 0:
                allowed, retry = check_rate(user["id"], user.get("role","beta"), "chat")
                if not allowed:
                    return self.send_json({"error": f"Rate limit — try again in {retry}s."}, 429)

            # Log activity for tracking
            if user and user.get("id", 0) > 0:
                _bm_log = extract_board(body.get("board_context","") + " " + body.get("message",""))
                _sym_log = next((m["content"] for m in (body.get("history") or [])
                                 if m.get("role")=="user"), body.get("message",""))
                _sym = ("no_backlight" if "backlight" in _sym_log.lower() else
                        "no_charge"   if "charg" in _sym_log.lower() else
                        "no_power"    if "power" in _sym_log.lower() else "")
                log_activity(user["id"], _bm_log or "", _sym)
            reply = ai_chat(body.get("message",""), body.get("board_context",""), body.get("history",[]))
            resp  = {"reply": reply}

            # Include last DE step for feedback context
            _bm = extract_board(body.get("board_context","") + " " + body.get("message",""))
            if HAS_DECISION and _bm:
                _fmk = next((m["content"] for m in (body.get("history") or [])
                             if m.get("role") == "user"), body.get("message",""))
                _rs  = _REPAIR_STATES.get((_bm, hash(_fmk[:120])))
                if _rs and _rs.last_action:
                    resp["de_step"] = {k: v for k, v in _rs.last_action.items()
                                       if k in ("action","target","step_number","template")}
            return self.send_json(resp)


# ── ai_chat ───────────────────────────────────────────────────────────────────
def ai_chat(msg, ctx, history=[]):
    key = os.environ.get("OPENAI_API_KEY")
    if not key: return "Set OPENAI_API_KEY first."

    # Board detection
    board_match = extract_board(ctx + " " + msg)
    if not board_match:
        for bnum, bdata in BOARDS.items():
            model = bdata.get("model","").lower()
            if model and model in (ctx + " " + msg).lower():
                board_match = bnum; break

    # Boardview ground truth
    real_nets, real_comps = _load_boardview_index(board_match)
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
        if HAS_OBD and board_match:
            obd_ctx = format_refs_for_prompt(board_match, key_nets[:40])
            if obd_ctx: nets_ctx += obd_ctx
        if HAS_COMP_DB and board_match:
            bvp = DB_ROOT / board_match / "boardview_parsed.json"
            if bvp.exists():
                try:
                    bvd_data = json.loads(bvp.read_text())
                    comp_ctx = enrich_boardview_context(board_match, key_nets[:20], bvd_data)
                    if comp_ctx: nets_ctx += comp_ctx
                except: pass

    # Case search
    cases_ctx = ""
    first_msg = next((m["content"] for m in (history or []) if m.get("role")=="user"), msg)
    if board_match:
        search_query = f"{first_msg} {msg}".strip()
        if HAS_EMBEDDINGS:
            cases = search_semantic(search_query, board=board_match, limit=4)
            cases_ctx = format_semantic_cases(cases) if cases else ""
        if not cases_ctx and HAS_CASES_DB:
            cases = search_cases(search_query + " " + ctx, board=board_match, limit=4)
            cases_ctx = format_cases_for_prompt(cases) if cases else ""

    # Protocol
    proto_ctx = ""
    if HAS_PROTOCOLS and board_match:
        proto_ctx = format_protocol_for_prompt(board_match, first_msg, history)
        if not proto_ctx:
            proto_ctx = format_protocol_for_prompt(board_match, msg, history)

    # Intake
    intake = None
    if HAS_INTAKE and board_match:
        intake_src   = msg if len(msg) > len(first_msg) else first_msg
        recent_user  = [m["content"] for m in (history or []) if m.get("role")=="user"][-3:]
        intake       = parse_case_intake(intake_src, board_match, recent_user_msgs=recent_user)

    # ── Decision Engine v3 ────────────────────────────────────────────────────
    de_step      = {}
    llm_injection = ""
    if HAS_DECISION and board_match:
        session_key = (board_match, hash(first_msg[:120]))
        if session_key not in _REPAIR_STATES:
            rs = RepairState(board=board_match)
            rs.board_family = resolve_board_family(board_match) or ""
            lowmsg = first_msg.lower()
            if any(w in lowmsg for w in ["backlight","no light","dark screen","bklt"]):
                rs.symptom = "no_backlight"
            elif any(w in lowmsg for w in ["no charge","not charging","no 20v"]):
                rs.symptom = "no_charge"
            elif any(w in lowmsg for w in ["no image","blank","black screen","no display"]):
                rs.symptom = "no_image"
            else:
                rs.symptom = "no_power"
            _REPAIR_STATES[session_key] = rs

        repair_state = _REPAIR_STATES[session_key]

        # Auto-detect current draw from message
        ma_m = re.search(r'(\d+)\s*ma\b', msg.lower())
        a_m  = re.search(r'(\d+\.?\d*)\s*a\b', msg.lower())
        if ma_m and repair_state.current_draw_amps is None:
            repair_state.current_draw_amps = int(ma_m.group(1)) / 1000
        elif a_m and repair_state.current_draw_amps is None:
            val = float(a_m.group(1))
            if val < 20:
                repair_state.current_draw_amps = val

        de_step, llm_injection = process_repair_turn(repair_state, msg)

    # Power tree fallback
    tree_ctx = ""
    if not proto_ctx and HAS_POWER_TREE and board_match:
        tree_ctx = format_tree_for_prompt(board_match, msg)

    # ── State summary ─────────────────────────────────────────────────────────
    state_lines = []
    if HAS_DECISION and board_match:
        session_key = (board_match, hash(first_msg[:120]))
        rs = _REPAIR_STATES.get(session_key)
        if rs and rs.confirmed_rails:
            measured = {k: v["value"] for k, v in rs.confirmed_rails.items()
                        if v.get("source") == "measured"}
            if measured:
                state_lines.append("Already measured: " +
                    ", ".join(f"{k}={v}" for k, v in measured.items()))
    if HAS_INTAKE and intake and hasattr(intake, "confidence") and intake.confidence > 0.4:
        intake_sum = intake_to_state_summary(intake)
        if intake_sum:
            state_lines.insert(0, intake_sum)
    state_summary = "\n".join(state_lines)

    # ── System prompt ─────────────────────────────────────────────────────────
    base_rules = (
        "You are an expert MacBook board-level repair technician assistant. "
        "You guide technicians step by step through board-level diagnosis."
        "\n\nCRITICAL RULES:"
        "\n1) ONE action per reply — one measurement or one physical check only."
        "\n2) ONLY mention nets and components from the [Actual rails] list."
        "\n3) If 0V + short (<0.1Ω): inject 1V/3A to locate the shorted component."
        "\n4) If 0V + no short: check the fuse on that rail."
        "\n5) If low voltage: partial short — check diode mode to ground."
        "\n6) Be conversational but precise. 1-2 sentences max."
        "\n7) When the user confirms something (yes/done/ok), move to the NEXT step immediately."
        "\n8) Never repeat a question the user already answered."
    )

    if de_step and not de_step.get("resolved"):
        system_prompt = (
            base_rules
            + (f"\n\n[What we know so far]:\n{state_summary}" if state_summary else "")
            + f"\n\n[DECISION ENGINE — STEP {de_step.get('step_number','')}]"
            + f"\nAction  : {de_step.get('action','')}"
            + f"\nTarget  : {de_step.get('target','')}"
            + f"\nTemplate: {de_step.get('template','')}"
            + f"\nFollow-up: {de_step.get('follow_up','')}"
            + f"\n\nSTRICT: rephrase Template into 1-2 natural sentences."
            + f" You MUST mention '{de_step.get('target','')}' exactly."
            + " End with the Follow-up question. Do NOT suggest any other component or rail."
        )
    elif de_step and de_step.get("resolved"):
        system_prompt = (
            base_rules
            + f"\n\n[DIAGNOSIS COMPLETE]: {de_step.get('conclusion','')}"
            + "\n\nTell the technician to perform this repair. One sentence. Be definitive."
        )
    elif proto_ctx:
        system_prompt = (
            base_rules + "\n\n" + proto_ctx.strip()
            + (f"\n\n[What we know so far]:\n{state_summary}" if state_summary else "")
            + "\n\nAsk for the ONE measurement in the protocol. Never loop. Never mention other nets."
        )
    else:
        system_prompt = base_rules
        if state_summary:
            system_prompt += f"\n\n[What we know]:\n{state_summary}"

    # ── Build request ─────────────────────────────────────────────────────────
    user_content = f"{ctx}\n{msg}"
    if not proto_ctx and tree_ctx:
        user_content += tree_ctx
    if cases_ctx:
        system_prompt += f"\n\n[Similar forum cases — use for pattern matching only]:\n{cases_ctx}"
    if nets_ctx:
        user_content += nets_ctx
    if llm_injection:
        user_content += llm_injection

    messages = [{"role": "system", "content": system_prompt}]
    for h in (history or [])[-10:]:
        if h.get("role") in ("user","assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_content})

    payload = json.dumps({
        "model": "gpt-4o",
        "max_tokens": 400,
        "messages": messages
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            reply = json.loads(r.read())["choices"][0]["message"]["content"]
        # Guardrail: if LLM deviated from locked target, fall back to template
        if HAS_DECISION and de_step and not de_step.get("resolved"):
            is_valid, reason = validate_llm_output(reply, de_step)
            if not is_valid:
                print(f"⚠  LLM guardrail triggered: {reason}")
                reply = de_step["template"] + "\n\n" + de_step.get("follow_up","")
        return reply
    except urllib.error.HTTPError as e:
        return f"Error {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    PORT = 8765
    build_search_index()
    print(f"✅ http://localhost:{PORT}  |  {len(BOARDS)} boards loaded")
    HTTPServer(("", PORT), Handler).serve_forever()
