#!/usr/bin/env python3
"""boardview_parser.py — תומך ב-BVRAW_FORMAT_3 וב-BRD בינארי"""
import json, re, struct
from pathlib import Path

DB_ROOT = Path.home() / "repair_ai_db"

def parse_bvraw3(text: str) -> dict:
    result = {"format":"bvraw3","components":[],"nets":[],"pins":[],"outline":[]}
    net_set = set()
    lines = text.splitlines()
    i = 0
    current_comp = None
    current_pin  = None
    while i < len(lines):
        line = lines[i].strip(); i += 1

        # Board outline — list of x1 y1 x2 y2 ... segment pairs
        if line.startswith("OUTLINE_SEGMENTED "):
            try:
                vals = [float(v) for v in line.split()[1:]]
                # pairs of points = line segments
                segs = [[vals[i],vals[i+1],vals[i+2],vals[i+3]]
                        for i in range(0, len(vals)-3, 4)]
                result["outline"] = segs
                result["outline_type"] = "segments"
            except: pass
            continue

        if line.startswith("PART_NAME "):
            current_comp = {"ref":line[10:].strip(),"x":0,"y":0,"side":0,"rot":0,"nets":[],
                            "origin_x":0,"origin_y":0}
            current_pin = None
        elif current_comp and line.startswith("PART_SIDE "):
            current_comp["side"] = 0 if line[10:].strip().upper()=="T" else 1
        elif current_comp and line.startswith("PART_ORIGIN "):
            try:
                vals = line.split()[1:]
                current_comp["origin_x"] = float(vals[0])
                current_comp["origin_y"] = float(vals[1])
                current_comp["x"] = float(vals[0])
                current_comp["y"] = float(vals[1])
            except: pass
        elif line == "PART_END":
            if current_comp:
                comp_pins = [p for p in result["pins"] if p["ref"]==current_comp["ref"]]
                if comp_pins:
                    current_comp["x"] = sum(p["x"] for p in comp_pins)/len(comp_pins)
                    current_comp["y"] = sum(p["y"] for p in comp_pins)/len(comp_pins)
                    current_comp["nets"] = list({p["net"] for p in comp_pins if p["net"]})
                result["components"].append(current_comp)
            current_comp = None
        elif current_comp and line.startswith("PIN_ID "):
            current_pin = {"ref":current_comp["ref"],"pin":line[7:].strip(),"x":0,"y":0,"net":"","side":current_comp["side"]}
        elif current_pin:
            if line.startswith("PIN_ORIGIN "):
                try:
                    p = line.split()[1:]
                    px, py = float(p[0]), float(p[1])
                    cx = current_comp.get("origin_x", 0)
                    cy = current_comp.get("origin_y", 0)
                    # Detect relative coords: if pin is closer to 0 than to component center → relative
                    if cx != 0 or cy != 0:
                        dist_abs = (px - cx)**2 + (py - cy)**2
                        dist_rel = px**2 + py**2
                        if dist_rel < dist_abs:
                            px += cx
                            py += cy
                    current_pin["x"] = px
                    current_pin["y"] = py
                except: pass
            elif line.startswith("PIN_NET "):
                net=line[8:].strip(); current_pin["net"]=net
                if net and net not in net_set:
                    net_set.add(net); result["nets"].append({"id":len(result["nets"]),"name":net})
            elif line.startswith("PIN_SIDE "):
                current_pin["side"]=0 if line[9:].strip().upper()=="T" else 1
            elif line.startswith("PIN_RADIUS "):
                try: current_pin["radius"]=float(line[11:].strip())
                except: pass
            elif line.startswith("PIN_OUTLINE_RELATIVE "):
                try:
                    vals=[float(v) for v in line[21:].split()]
                    # pairs of x,y relative coords
                    pts=[[vals[i],vals[i+1]] for i in range(0,len(vals)-1,2)]
                    current_pin["outline"]=pts
                except: pass
            elif line=="PIN_END":
                result["pins"].append(current_pin); current_pin=None
    result["stats"]={"components":len(result["components"]),"nets":len(result["nets"]),"pins":len(result["pins"])}

    # If no outline — generate bounding box from pins
    if not result["outline"] and result["pins"]:
        xs = [p["x"] for p in result["pins"]]
        ys = [p["y"] for p in result["pins"]]
        pad = (max(xs)-min(xs)) * 0.03
        x0,x1 = min(xs)-pad, max(xs)+pad
        y0,y1 = min(ys)-pad, max(ys)+pad
        result["outline"] = [[x0,y0,x1,y0],[x1,y0,x1,y1],[x1,y1,x0,y1],[x0,y1,x0,y0]]
        result["outline_type"] = "bbox_generated"

    # Normalize: if pin space differs from outline space, remap pins+components
    if result["outline"] and result["pins"]:
        ox_vals, oy_vals = [], []
        for seg in result["outline"]:
            ox_vals += [seg[0], seg[2]]
            oy_vals += [seg[1], seg[3]]
        o_xspan = max(ox_vals) - min(ox_vals)
        o_yspan = max(oy_vals) - min(oy_vals)
        o_xmin  = min(ox_vals)
        o_ymin  = min(oy_vals)
        pin_xs = [p["x"] for p in result["pins"]]
        pin_ys = [p["y"] for p in result["pins"]]
        p_xspan = max(pin_xs) - min(pin_xs)
        p_yspan = max(pin_ys) - min(pin_ys)
        p_xmin  = min(pin_xs)
        p_ymin  = min(pin_ys)
        if p_xspan > 0 and p_yspan > 0:
            xr = o_xspan / p_xspan
            yr = o_yspan / p_yspan
            if xr > 5 or yr > 5 or xr < 0.2 or yr < 0.2:
                xscale = xr
                yscale = yr
                xoff   = o_xmin - p_xmin * xscale
                yoff   = o_ymin - p_ymin * yscale
                for p in result["pins"]:
                    p["x"] = p["x"] * xscale + xoff
                    p["y"] = p["y"] * yscale + yoff
                    if "outline" in p:
                        p["outline"] = [[v[0]*xscale, v[1]*yscale] for v in p["outline"]]
                for c in result["components"]:
                    c["x"] = c["x"] * xscale + xoff
                    c["y"] = c["y"] * yscale + yoff
    return result

def parse_brd_binary(data: bytes) -> dict:
    """OpenBoardView binary BRD format parser."""
    result = {"format":"brd_binary","components":[],"nets":[],"pins":[],"outline":[]}
    # Try to decode as text first (some .brd are actually text)
    try:
        text = data.decode("utf-8", errors="strict")
        if "BVRAW" in text[:50]:
            return parse_bvraw3(text)
    except: pass

    # Extract null-terminated strings from binary
    strings = []; cur = []
    for b in data:
        if 32 <= b <= 126: cur.append(chr(b))
        else:
            if len(cur) >= 2: strings.append("".join(cur))
            cur = []
    if cur and len(cur)>=2: strings.append("".join(cur))

    cr = re.compile(r'^[CRULQDFTYBIJZ]\d{1,4}[A-Z]?$')
    nr = re.compile(r'^PP[0-9A-Z_]{2,30}$|^GND$|^VBUS$|^PPVBUS')
    sc, sn = set(), set()
    for s in strings:
        if cr.match(s) and s not in sc:
            sc.add(s); result["components"].append({"ref":s,"x":0,"y":0,"side":0,"rot":0,"nets":[]})
        elif nr.match(s.upper()) and s.upper() not in sn:
            sn.add(s.upper()); result["nets"].append({"id":len(result["nets"]),"name":s.upper()})

    result["stats"]={"components":len(result["components"]),"nets":len(result["nets"]),"pins":len(result["pins"])}
    return result

def parse_file(path: Path) -> dict:
    data = path.read_bytes()

    # 1. Try plain text BVR format
    try:
        text = data.decode("utf-8", errors="strict")
        if "BVRAW_FORMAT" in text[:100]:
            return parse_bvraw3(text)
    except: pass

    # 2. Try latin-1 BVR (some files use this encoding)
    try:
        text = data.decode("latin-1")
        if "BVRAW_FORMAT" in text[:100] or "OUTLINE_SEGMENTED" in text[:500]:
            return parse_bvraw3(text)
    except: pass

    # 3. Try encrypted BRD (bit-rotation cipher) — many .bvr/.brd are disguised BRD files
    try:
        import brd_decoder
        decoded_text = brd_decoder.decode_brd(data)
        if any(k in decoded_text[:2000] for k in ("Format:", "Pins1:", "Parts:", "Pins:")):
            return brd_decoder.parse_brd(decoded_text)
    except Exception as e:
        pass

    # 4. Last resort: binary scraping
    return parse_brd_binary(data)

def main():
    index  = json.loads((DB_ROOT/"index.json").read_text())
    boards = {b["board_number"]:b for b in index["boards"] if b.get("board_number")}
    total  = 0
    for board_num in sorted(boards):
        board_dir = DB_ROOT/board_num
        if not board_dir.exists(): continue
        out_path  = board_dir/"boardview_parsed.json"
        bvr_files = list(board_dir.glob("*.bvr"))+list(board_dir.glob("*.BVR"))+list(board_dir.glob("*.brd"))+list(board_dir.glob("*.BRD"))+list(board_dir.glob("*.brd2"))
        if not bvr_files:
            print(f"  ✗ {board_num} — אין boardview"); continue
        bvr_path = bvr_files[0]
        print(f"  🔲 {board_num}  ←  {bvr_path.name}")
        try:
            parsed = parse_file(bvr_path)
            out_path.write_text(json.dumps(parsed,indent=2))
            s=parsed["stats"]
            print(f"     components:{s['components']}  nets:{s['nets']}  pins:{s['pins']}")
            total+=1
        except Exception as e:
            print(f"     ✗ {e}")
    print(f"\n✅ עובד {total} לוחות")

if __name__=="__main__":
    for f in DB_ROOT.rglob("boardview_parsed.json"):
        f.unlink(); print(f"  🗑  {f}")
    print(); main()