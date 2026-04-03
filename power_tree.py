#!/usr/bin/env python3
"""
power_tree.py — Circuit power flow knowledge for MacBook boards.
Used to give the AI structured circuit context instead of guessing.

Each tree entry: net → {expected_v, source, via_component, downstream, common_faults}
"""

POWER_TREES = {

# ─────────────────────────── 820-02652 / 820-02841 (M2 Max/Pro) ────────────────
"820-02652": {
    "backlight": {
        "PPBUS_AON": {"expected": "12–13.1V", "source": "U5200"},
        "PPBUS_AON_LUXE": {
            "expected": "~12V",
            "source": "PPBUS_AON via fuse",
            "note": "M2 Max uses LUXE controller instead of UP800",
            "fault_if_zero": "Check fuse between PPBUS_AON and PPBUS_AON_LUXE. Check QP801 MOSFET.",
        },
        "LUXE IC": {
            "type": "boost converter (replaces UP800 on M2 Max)",
            "downstream": ["PPLUXE_LX1", "PPLUXE_LX2"],
            "note": "Check LUXE_BT_C1 and LUXE_BT_C2 (~4.8V). If 0V output: check BL_PWR_EN_R (should be 1.2V+).",
        },
        "PPVOUT_LUXE": {
            "expected": "21–43V",
            "note": "Output rail name differs from M1. Check QP801 for shorts.",
        },
    },
    "power": {
        "PPBUS_AON": {"expected": "12–13.1V"},
        "PP3V8_AON_VDDMAIN": {"expected": "3.8V"},
        "PP5V_S2": {"expected": "5V"},
    },
},

# ─────────────────────────── 820-02016 / 820-02020 (M1) ──────────────────────
"820-02016": {
    "backlight": {
        "PPBUS_AON": {
            "expected": "12.0-13.1V",
            "source": "U5200 (ISL9240)",
            "downstream": ["FP800"],
        },
        "FP800": {
            "type": "fuse",
            "note": "3A backlight input fuse — blown fuse = PPVIN_LCDBKLT_F = 0V",
            "downstream": ["PPVIN_LCDBKLT_F"],
        },
        "PPVIN_LCDBKLT_F": {
            "expected": "~12V",
            "source": "PPBUS_AON via FP800",
            "via": "QP800 (MOSFET, gated by BL_PWR_EN)",
            "downstream": ["UP800"],
            "fault_if_zero": "Check FP800 continuity first, then diode mode on line",
            "fault_if_low": "Partial short — check capacitors CP800-CP803 area",
        },
        "BL_PWR_EN": {
            "expected": "1.8V when backlight active",
            "source": "PMU (U8100)",
            "note": "Enable signal for QP800 gate — 0V = backlight disabled by PMU",
        },
        "UP800": {
            "type": "boost converter IC",
            "note": "Boosts 12V to ~43V for LED strings. Liquid damage common on pins 9/10.",
            "downstream": ["PPVOUT_LCDBKLT", "PP5V_BKLT_A", "PP5V_BKLT_D"],
        },
        "PPVOUT_LCDBKLT": {
            "expected": "21-43V (depends on brightness)",
            "note": "Boosted output. Short here = check output capacitors CP860 area.",
        },
        "PP5V_BKLT_A": {
            "expected": "5V",
            "via": "RP845 (should be 0Ω — common failure point)",
            "fault_if_missing": "Check RP845 resistance — should be 0Ω, often found at 200Ω after liquid",
        },
        "PP5V_BKLT_D": {
            "expected": "5V",
            "via": "RP844 (should be 0Ω — common failure point)",
        },
    },
    "power": {
        "PPDCIN_AON_CHGR_R": {
            "expected": "5V (USB-C input, pre-negotiation)",
            "source": "CD3217 (UF400/UF500)",
            "downstream": ["U5200"],
        },
        "PPBUS_AON": {
            "expected": "12.0-13.1V",
            "source": "U5200 (ISL9240 charger IC)",
            "note": "If 0V: check U5200, R5221/R5222 current sense resistors (should be 1Ω)",
            "downstream": ["PP3V8_AON_VDDMAIN", "PPVIN_LCDBKLT_F", "PPBUS_5VS2_VIN"],
        },
        "PP3V8_AON_VDDMAIN": {
            "expected": "3.8V",
            "source": "U5700 (buck converter)",
            "note": "If unstable: check Q5230. If 0V: check P3V8_PWR_EN enable signal.",
            "downstream": ["PP5V_AON_P3V8VRLDO"],
        },
        "PP5V_S2": {
            "expected": "5V",
            "source": "UC300 (enabled by P5VS2_EN from U8100)",
            "note": "If 0V: check P5VS2_EN from U8100, then UC300 itself",
        },
        "PP3V3_S2_UPC": {
            "expected": "3.3V",
            "source": "U8100 LDO",
        },
        "PP1V25_S2": {
            "expected": "1.25V",
            "source": "U7700 BUCK13 LDO",
            "note": "If missing: DFU not possible — check U7700",
        },
    },
    "usb_c": {
        "PP3V3_UPC0_LDO": {"expected": "3.3V", "source": "UF400 (CD3217 port 0)"},
        "PP3V3_UPC1_LDO": {"expected": "3.3V", "source": "UF500 (CD3217 port 1)"},
        "PP1V5_UPC0_LDO_CORE": {"expected": "1.5V", "source": "UF400"},
        "PP1V5_UPC1_LDO_CORE": {"expected": "1.5V", "source": "UF500"},
    },
},

# ─────────────────────────── 820-02020 (M1 Pro 13") ─────────────────────────
"820-02020": {
    "backlight": {
        "PPBUS_AON": {"expected": "12.0-13.1V", "source": "U5200"},
        "FP800": {"type": "fuse", "downstream": ["PPVIN_LCDBKLT_F"]},
        "PPVIN_LCDBKLT_F": {
            "expected": "~12V",
            "fault_if_zero": "Check FP800 fuse, then diode mode. Common: liquid damage to UP800 area",
        },
        "UP800": {
            "type": "boost converter",
            "note": "Liquid damage very common on 820-02020. Check pins 9/10 for corrosion.",
            "downstream": ["PPVOUT_LCDBKLT"],
        },
        "PPVOUT_LCDBKLT": {"expected": "21-43V"},
    },
    "power": {
        "PPBUS_AON": {
            "expected": "12.0-13.1V",
            "source": "U5200",
            "note": "Measuring point: C5403",
        },
        "PP3V8_AON_VDDMAIN": {
            "expected": "3.8V",
            "source": "U5700",
            "note": "Measuring points: L5800, L5820, L5840",
        },
        "PP5V_S2": {"expected": "5V", "source": "UC300"},
        "PP1V25_S2": {"expected": "1.25V", "source": "U7700"},
        "PP3V3_S2_UPC": {"expected": "3.3V", "source": "U8100"},
    },
},

}

# Same tree for boards that share the same circuit
POWER_TREES["820-02020"] = POWER_TREES["820-02016"].copy()
POWER_TREES["820-02020"]["power"]["PPBUS_AON"]["note"] = "Measuring point: C5403"

def get_power_tree(board, subsystem=None):
    """Get power tree for a board, optionally filtered by subsystem."""
    tree = POWER_TREES.get(board)
    if not tree:
        # Try parent board (e.g. 820-02098 → use 820-02016 as fallback)
        for known in POWER_TREES:
            if known in board or board in known or board[:7] == known[:7]:
                tree = POWER_TREES[known]
                break
    if not tree:
        return {}
    if subsystem:
        return tree.get(subsystem, tree)
    return tree

def detect_subsystem(symptom):
    """Detect which subsystem is relevant from the symptom description."""
    s = symptom.lower()
    if any(w in s for w in ["backlight","no display","screen dark","bklt","lcdbklt","no backlight"]):
        return "backlight"
    if any(w in s for w in ["usb","charge","port","usb-c","typec","pd negotiation"]):
        return "usb_c"
    if any(w in s for w in ["5v","no power","not turning","dead board","stuck"]):
        return "power"
    # Default: if image/display mentioned without backlight context → check both
    if any(w in s for w in ["image","display"]):
        return "backlight"
    return "power"

def format_tree_for_prompt(board, symptom=""):
    """Format power tree as concise context string for AI prompt."""
    subsystem = detect_subsystem(symptom)
    tree = get_power_tree(board, subsystem)
    if not tree:
        return ""

    lines = [f"\n[Power sequence — {board} {subsystem}]:"]
    lines.append("  Follow this order. Do not skip steps.")
    for net, info in tree.items():
        if not isinstance(info, dict):
            continue
        # Build readable line: NET = VALUE (type/source) | faults | note
        name_part = net
        if info.get("expected"): name_part += f" = {info['expected']}"
        if info.get("type"):     name_part += f" ({info['type']})"
        elif info.get("source"): name_part += f" (from {info['source']})"

        fault_parts = []
        if info.get("fault_if_zero"):   fault_parts.append(f"0V→ {info['fault_if_zero']}")
        if info.get("fault_if_low_1v"): fault_parts.append(f"1V→ {info['fault_if_low_1v']}")
        if info.get("fault_if_low"):    fault_parts.append(f"low→ {info['fault_if_low']}")
        if info.get("fault"):           fault_parts.append(f"⚠ {info['fault']}")
        if info.get("note"):            fault_parts.append(info['note'])
        if info.get("downstream"):      fault_parts.append(f"feeds {', '.join(info['downstream'])}")

        line = f"  • {name_part}"
        if fault_parts: line += " → " + " → ".join(fault_parts)
        lines.append(line)

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    board = sys.argv[1] if len(sys.argv) > 1 else "820-02016"
    symptom = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "backlight"
    print(format_tree_for_prompt(board, symptom))

POWER_TREES["820-02841"] = POWER_TREES["820-02652"]
