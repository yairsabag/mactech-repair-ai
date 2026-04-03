#!/usr/bin/env python3
import json, sys
from pathlib import Path
from collections import defaultdict

SEP = '\xcb\xd7'

def decode_brd(data: bytes) -> str:
    r = bytearray()
    for b in data:
        r.append((~(((b >> 6) & 3) | ((b << 2) & 0xFF))) & 0xFF)
    return r.decode("latin-1", errors="replace")

def parse_brd(text: str) -> dict:
    lines = [l.strip() for l in text.split(SEP) if l.strip()]
    sec = {}
    for i, l in enumerate(lines):
        for name in ("str_length","var_data","Format","Parts","Pins1","Pins2","Pins","Nails"):
            if l == name or l.startswith(name + ":"):
                sec[name] = i
                break

    def get_section(name, stops):
        if name not in sec: return []
        s = sec[name] + 1
        candidates = [sec[n] for n in stops if n in sec and sec[n] > sec[name]]
        e = min(candidates) if candidates else len(lines)
        return lines[s:e]

    result = {"format":"brd_decoded","components":[],"nets":[],"pins":[],"outline":[],"outline_type":"segments"}

    # Format: outline polyline
    outline_pts = []
    for l in get_section("Format", ["Parts","Pins1","Pins","Nails"]):
        p = l.split()
        if len(p) >= 2:
            try: outline_pts.append((float(p[0]), float(p[1])))
            except: pass

    if len(outline_pts) >= 2:
        steps = sorted([max(abs(outline_pts[i][0]-outline_pts[i-1][0]),
                           abs(outline_pts[i][1]-outline_pts[i-1][1]))
                       for i in range(1, len(outline_pts)) if outline_pts[i] != outline_pts[i-1]])
        med = steps[len(steps)//2] if steps else 1
        jump = max(200, med * 30)
        for i in range(len(outline_pts)-1):
            x1,y1 = outline_pts[i]; x2,y2 = outline_pts[i+1]
            if x1==x2 and y1==y2: continue
            if max(abs(x2-x1),abs(y2-y1)) > jump: continue
            result["outline"].append([x1,y1,x2,y2])

    net_set = {}
    def add_net(name):
        if name and name not in net_set:
            net_set[name] = len(result["nets"])
            result["nets"].append({"id": net_set[name], "name": name})

    is_variant_b = "Pins1" in sec

    if is_variant_b:
        # Pins1: string table REF STR_LEN CUMULATIVE_END
        comps = []
        for l in get_section("Pins1", ["Pins2","Nails"]):
            p = l.split()
            if len(p) >= 3:
                try: comps.append((p[0], int(p[2])))
                except: pass
        comps.sort(key=lambda x: x[1])

        def ref_for_pin_idx(idx):
            prev = 0
            for ref, end in comps:
                if prev < idx <= end: return ref
                prev = end
            return None

        # CRITICAL FIX: use absolute row counter, NOT p[3]
        abs_pin_idx = 1
        for l in get_section("Pins2", ["Nails"]):
            p = l.split()
            if len(p) >= 4:
                try:
                    x, y = float(p[0]), float(p[1])
                    pin_name = p[3] if len(p) >= 4 else str(abs_pin_idx)
                    net_name = p[4] if len(p) >= 5 else ""
                    ref = ref_for_pin_idx(abs_pin_idx) or f"UNK{abs_pin_idx}"
                    if net_name: add_net(net_name)
                    result["pins"].append({"ref":ref,"pin":str(pin_name),"x":x,"y":y,"net":net_name,"side":0,"radius":10.0})
                except: pass
            abs_pin_idx += 1

        for l in get_section("Nails", []):
            p = l.split()
            if len(p) >= 5:
                try:
                    probe=int(p[0]); x,y=float(p[1]),float(p[2]); side=int(p[3]); net_name=p[4]
                    if net_name: add_net(net_name)
                    result["pins"].append({"ref":f"TP{probe}","pin":"1","x":x,"y":y,"net":net_name,"side":side,"radius":8.0})
                except: pass

        comp_pins = defaultdict(list)
        for pin in result["pins"]: comp_pins[pin["ref"]].append(pin)
        for ref, ps in comps:
            pins = comp_pins.get(ref, [])
            x = sum(p["x"] for p in pins)/len(pins) if pins else 0
            y = sum(p["y"] for p in pins)/len(pins) if pins else 0
            result["components"].append({"ref":ref,"x":x,"y":y,"side":0,"rot":0,"nets":list({p["net"] for p in pins if p.get("net")})})

    else:
        # Variant A: standard wiki format
        parts_list = []
        for l in get_section("Parts", ["Pins","Nails"]):
            p = l.split()
            if len(p) >= 3:
                try:
                    name=p[0]; ptype=int(p[1]); end_pins=int(p[2])
                    parts_list.append((name, 1 if ptype==10 else 0, end_pins))
                except: pass

        def part_for_pin(idx1):
            prev = 0
            for i,(name,side,ep) in enumerate(parts_list):
                if prev < idx1 <= ep: return i
                prev = ep
            return None

        abs_pin_idx = 1
        for l in get_section("Pins", ["Nails"]):
            p = l.split()
            if len(p) >= 3:
                try:
                    x,y=float(p[0]),float(p[1])
                    pin_name=p[3] if len(p)>=4 else str(abs_pin_idx)
                    net_name=p[4] if len(p)>=5 else ""
                    pi=part_for_pin(abs_pin_idx)
                    ref=parts_list[pi][0] if pi is not None and pi<len(parts_list) else f"UNK{abs_pin_idx}"
                    side=parts_list[pi][1] if pi is not None and pi<len(parts_list) else 0
                    if net_name: add_net(net_name)
                    result["pins"].append({"ref":ref,"pin":str(pin_name),"x":x,"y":y,"net":net_name,"side":side,"radius":10.0})
                except: pass
            abs_pin_idx += 1

        comp_pins = defaultdict(list)
        for pin in result["pins"]: comp_pins[pin["ref"]].append(pin)
        for name,side,_ in parts_list:
            pins=comp_pins.get(name,[])
            x=sum(p["x"] for p in pins)/len(pins) if pins else 0
            y=sum(p["y"] for p in pins)/len(pins) if pins else 0
            result["components"].append({"ref":name,"x":x,"y":y,"side":side,"rot":0,"nets":list({p["net"] for p in pins if p.get("net")})})

    if not result["outline"] and result["pins"]:
        xs=[p["x"] for p in result["pins"]]; ys=[p["y"] for p in result["pins"]]
        pad=(max(xs)-min(xs))*0.03
        x0,x1=min(xs)-pad,max(xs)+pad; y0,y1=min(ys)-pad,max(ys)+pad
        result["outline"]=[[x0,y0,x1,y0],[x1,y0,x1,y1],[x1,y1,x0,y1],[x0,y1,x0,y0]]
        result["outline_type"]="bbox_generated"

    result["stats"]={"components":len(result["components"]),"nets":len(result["nets"]),"pins":len(result["pins"])}
    return result

if __name__ == "__main__":
    if len(sys.argv) < 2:
        DB = Path.home() / "repair_ai_db"
        for bd in sorted(DB.iterdir()):
            for brd_f in list(bd.glob("*.brd")) + list(bd.glob("*.BRD")):
                out = bd / "boardview_parsed.json"
                existing = {}
                if out.exists():
                    try: existing = json.loads(out.read_text())
                    except: pass
                if existing.get("format") == "bvraw3": continue  # skip already-good BVR boards
                parsed = parse_brd(decode_brd(brd_f.read_bytes()))
                out.write_text(json.dumps(parsed, indent=2))
                s = parsed["stats"]
                print(f"  {bd.name}: {s['components']} comps, {s['nets']} nets, {s['pins']} pins")
        sys.exit(0)

    brd = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else brd.with_suffix(".json")
    parsed = parse_brd(decode_brd(brd.read_bytes()))
    out.write_text(json.dumps(parsed, indent=2))
    s = parsed["stats"]
    print(f"{brd.name}: {s['components']} comps, {s['nets']} nets, {s['pins']} pins")
    if parsed["components"]: print("sample comps:", [c["ref"] for c in parsed["components"][:5]])
    if parsed["pins"]: print("sample pin:", parsed["pins"][0])
