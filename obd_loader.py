#!/usr/bin/env python3
import urllib.request, json, re, sys, argparse, time
from pathlib import Path

DB_ROOT  = Path.home() / "repair_ai_db"
OBD_DIR  = DB_ROOT / "openboarddata"
BASE_URL = "https://openboarddata.org/?a=generate&bpath=laptops/apple"

BOARDS = {
    "820-02016": "820-02016", "820-02020": "820-02020",
    "820-02098": "820-02098", "820-02100": "820-02100",
    "820-02382": "820-02382", "820-02443": "820-02443",
    "820-02536": "820-02536", "820-02652": "820-02652",
    "820-02841": "820-02841", "820-02935": "820-02935",
    "820-03160": "820-03160", "820-03285": "820-03285",
    "820-03286": "820-03286",
}

def download_all():
    OBD_DIR.mkdir(parents=True, exist_ok=True)
    ok = err = 0
    for board, fname in BOARDS.items():
        out = OBD_DIR / (board + ".txt")
        if out.exists():
            print("  cached: " + board); ok += 1; continue
        url = BASE_URL + "/" + fname
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MacTech/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read().decode("utf-8", errors="ignore")
            out.write_text(data, encoding="utf-8")
            n = len([l for l in data.splitlines() if l.strip() and not l.startswith("#")])
            print("  " + board + ": " + str(n) + " entries"); ok += 1
        except urllib.error.HTTPError as e:
            print("  not found: " + board + " (" + str(e.code) + ")"); err += 1
        except Exception as e:
            print("  error: " + board + " " + str(e)); err += 1
        time.sleep(0.3)
    print("Done: " + str(ok) + " ok, " + str(err) + " missing")

def parse_obd(board):
    path = OBD_DIR / (board + ".txt")
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line[0] in "#": continue
        skip = ("HEADER","DIAG","SECT","NOTE","COMP","OBDATA")
        if any(line.startswith(s) for s in skip): continue
        if "/" in line:
            parts = line.split(None, 3)
            if len(parts) < 3: continue
            net = parts[0].split("/")[0].upper().strip()
            mtype = parts[1].lower()
            val_str = parts[2]
            comment = parts[3].strip("'\"") if len(parts) > 3 else ""
            if net not in result: result[net] = {}
            if mtype == "d" and val_str.upper() != "OL":
                try: result[net]["diode"] = float(val_str)
                except: pass
            elif mtype == "v":
                try: result[net]["voltage"] = float(val_str)
                except: pass
            elif mtype == "r" and val_str.upper() != "OL":
                result[net]["resistance"] = val_str
            if comment and comment not in ("''", '""', ""):
                result[net]["comment"] = comment[:80]
        else:
            parts = line.split(None, 4)
            if len(parts) < 2: continue
            net = parts[0].upper()
            entry = {}
            try: entry["diode"] = float(parts[1])
            except: pass
            if len(parts) > 2:
                try: entry["voltage"] = float(parts[2])
                except: pass
            if len(parts) > 3: entry["resistance"] = parts[3]
            if entry: result[net] = entry
    return {k: v for k, v in result.items() if v}

_cache = {}
def get_obd(board):
    if board not in _cache: _cache[board] = parse_obd(board)
    return _cache[board]

def lookup_net(board, net_name):
    return get_obd(board).get(net_name.upper())

def get_net_refs(board, nets):
    obd = get_obd(board)
    return [(n, obd[n.upper()]) for n in nets if n.upper() in obd]

def format_refs_for_prompt(board, nets, max_nets=30):
    refs = get_net_refs(board, nets)
    if not refs: return ""
    lines = ["", "[Reference values - " + board + "]",
             "NET -> diode | voltage | resistance"]
    for net, data in refs[:max_nets]:
        parts = []
        if "diode"      in data: parts.append("diode=" + str(round(data["diode"],3)))
        if "voltage"    in data: parts.append(str(data["voltage"]) + "V")
        if "resistance" in data: parts.append("res=" + str(data["resistance"]))
        if "comment"    in data: parts.append(data["comment"][:40])
        if parts: lines.append("  " + net + ": " + " | ".join(parts))
    lines.append("diode<0.1=short, OL=open")
    return "\n".join(lines)

def format_single_net(board, net):
    data = lookup_net(board, net)
    if not data: return ""
    parts = []
    if "diode"   in data: parts.append("diode=" + str(round(data["diode"],3)))
    if "voltage" in data: parts.append(str(data["voltage"]) + "V")
    if "resistance" in data: parts.append(data["resistance"])
    return "[" + net + ": " + " | ".join(parts) + "]" if parts else ""

def stats():
    print("\nOpenBoardData:")
    total = 0
    for board in BOARDS:
        obd = get_obd(board)
        total += len(obd)
        print("  " + board + ": " + (str(len(obd)) + " nets" if obd else "not downloaded"))
    print("  Total: " + str(total) + " net values")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--query", type=str)
    parser.add_argument("--board", type=str, default="820-02016")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()
    if args.stats or (not args.download and not args.query): stats()
    if args.download: download_all()
    if args.query:
        data = lookup_net(args.board, args.query)
        if data:
            print(args.board + "/" + args.query + ":")
            for k,v in data.items(): print("  " + k + ": " + str(v))
        else:
            print("No data for " + args.query + " on " + args.board)
