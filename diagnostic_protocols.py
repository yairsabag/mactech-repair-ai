#!/usr/bin/env python3
"""
diagnostic_protocols.py
========================
Structured diagnostic protocols for MacBook board-level repair.
Based on 2informaticos methodology from Rossmann forum.

Each protocol is a decision tree:
  - steps with measurements
  - branching based on results
  - clear actions at each decision point
"""

# ─────────────────────────────────────────────────────────────────
# PROTOCOL DEFINITIONS
# ─────────────────────────────────────────────────────────────────

PROTOCOLS = {

# ══════════════════════════════════════════════════════════════════
# BACKLIGHT — 820-02016 / 820-02020 (M1)
# ══════════════════════════════════════════════════════════════════
"backlight_m1": {
    "boards": ["820-02016", "820-02020", "820-02098", "820-02100",
               "820-02382", "820-02443", "820-02841"],
    "symptoms": ["no backlight", "backlight", "dark screen", "no display",
                 "screen dark", "bklt", "lcdbklt"],
    "steps": [
        {
            "id": 1,
            "instruction": "First confirm the display itself is not the issue — test with a known good display. Also confirm image exists on external monitor. Done?",
            "type": "confirm",
            "next_yes": 2,
        },
        {
            "id": 2,
            "instruction": "PPBUS_AON check — if the board turns on and shows image, PPBUS is OK (skip to step 3). "
                           "If board does NOT turn on at all, start no-power protocol instead.",
            "type": "confirm",
            "next_yes": 3,
        },
        {
            "id": 3,
            "measure": "PPVIN_LCDBKLT_F",
            "expected": "~12V (same as PPBUS_AON)",
            "note": "This rail comes from PPBUS_AON through fuse FP800",
            "if_ok": 5,
            "if_zero": 4,
            "if_low": "PPVIN_LCDBKLT_F is low (not 0V) — partial short likely. "
                      "Check diode mode to ground on PPVIN_LCDBKLT_F (normal ~0.45). "
                      "If <0.15 → short, inject 1V/3A to locate. If normal → check QP800 gate.",
        },
        {
            "id": 4,
            "instruction": "PPVIN_LCDBKLT_F is 0V. Check diode mode to ground on PPVIN_LCDBKLT_F (power off, red probe to GND, black probe to rail).",
            "expected_diode": "~0.45 (normal) / <0.1 = short",
            "if_short": "Short confirmed — inject 1V / 3A limit into PPVIN_LCDBKLT_F with thermal camera or IPA. "
                        "Most common culprit: shorted capacitor in CP800–CP803 area near FP800.",
            "if_open": "No short — check continuity across FP800 fuse. "
                       "If open: replace FP800 (0603, 3A, 32V). "
                       "If FP800 is OK: check QP800 MOSFET and BL_PWR_EN signal (expected 1.8V).",
            "next": 5,
        },
        {
            "id": 5,
            "measure": "BL_PWR_EN",
            "expected": "1.8V when backlight should be active",
            "note": "Enable signal from U8100 PMU to QP800 gate. 0V = PMU not enabling backlight.",
            "if_ok": 6,
            "if_zero": "BL_PWR_EN is 0V — PMU is not enabling backlight. "
                       "Check PP1V8_AON (expected 1.8V) and LCDBKLT_EN_L (should be low = 0V to enable). "
                       "Also verify display connector JP600 is seated — I2C communication issue can block enable.",
        },
        {
            "id": 6,
            "instruction": "PPVIN present and BL_PWR_EN OK — check UP800 output.",
            "measure": "PPVOUT_LCDBKLT",
            "expected": "21–43V (brightness dependent)",
            "if_ok": "All rails present — UP800 is working. Check output path: "
                     "verify RP844 and RP845 are 0Ω (measure resistance — liquid damage often raises these to 50–200Ω). "
                     "Also check continuity from DP800 through JP600 to display connector pin 43.",
            "if_zero": 7,
            "if_low": "PPVOUT is low — short on output side. "
                      "Check diode mode on PPVOUT_LCDBKLT (normal ~0.5). "
                      "Unplug display first to rule out display-side short.",
        },
        {
            "id": 7,
            "instruction": "PPVOUT_LCDBKLT is 0V with input OK — UP800 is not boosting.",
            "actions": [
                "1. Check diode mode on PPVOUT_LCDBKLT — if <0.15, short on output. Unplug display first.",
                "2. Inspect UP800 visually for corrosion — especially pins 9/10 (current sense). Common on liquid damage boards.",
                "3. Check PP5V_BKLT_A and PP5V_BKLT_D — should both be 5V. Check RP845 and RP844 resistance (should be 0Ω).",
                "4. If all inputs/enables look good but no output → replace UP800 from same-model donor board (UP800 has firmware).",
            ],
        },
    ],
},

# ══════════════════════════════════════════════════════════════════
# BACKLIGHT — 820-02652 / 820-02841 (M2 Max/Pro) — LUXE controller
# ══════════════════════════════════════════════════════════════════
"backlight_m2max": {
    "boards": ["820-02652", "820-02841", "820-02100", "820-02382", "820-02443"],
    "symptoms": ["no backlight", "backlight", "dark screen", "no display",
                 "screen dark", "bklt", "luxe"],
    "steps": [
        {
            "id": 1,
            "instruction": "Test with known good display and confirm image on external monitor.",
            "type": "confirm",
            "next_yes": 2,
        },
        {
            "id": 2,
            "measure": "PPBUS_AON",
            "expected": "12–13.1V",
            "if_ok": 3,
            "if_zero": "PPBUS_AON absent — start no-power protocol.",
        },
        {
            "id": 3,
            "measure": "PPBUS_AON_LUXE",
            "expected": "~12V",
            "note": "M2 Max uses LUXE controller — different from M1 UP800 circuit",
            "if_ok": 4,
            "if_zero": "PPBUS_AON_LUXE is 0V — check fuse between PPBUS_AON and PPBUS_AON_LUXE. "
                       "Also check BL_PWR_EN_R (should be 1.2–1.8V). Check QP801 gate/drain.",
            "if_low": "Abnormal low voltage — check diode mode on PPBUS_AON_LUXE for partial short. "
                      "Check QP801 MOSFET for leakage.",
        },
        {
            "id": 4,
            "measure": "LUXE_BT_C1",
            "expected": "~4.8V (bootstrap capacitor voltage)",
            "if_ok": 5,
            "if_zero": "Bootstrap voltage missing — LUXE IC is not switching. "
                       "Check LUXE_COMP and LUXE_COMP_R signals. Inspect LUXE IC for corrosion.",
        },
        {
            "id": 5,
            "instruction": "Check PPVOUT_LUXE (backlight output). Compare diode mode with reference table if no output.",
            "actions": [
                "1. Measure PPVOUT_LUXE — expected 21–43V",
                "2. If 0V: check diode mode on PPVOUT_LUXE — unplug display first",
                "3. If short: check QP801 (commonly shorted on liquid damage boards)",
                "4. If no short but no output: LUXE IC likely bad — replace from same-model donor",
            ],
        },
    ],
},

# ══════════════════════════════════════════════════════════════════
# NO POWER / 5V STUCK — 820-02016 / 820-02020 (M1)
# ══════════════════════════════════════════════════════════════════
"nopower_5v_m1": {
    "boards": ["820-02016", "820-02020", "820-02098", "820-02100",
               "820-02382", "820-02443", "820-02536", "820-02841"],
    "symptoms": ["no power", "not turning on", "5v", "dead", "stuck 5v",
                 "0.05a", "0.04a", "no charge", "stuck at 5"],
    "steps": [
        {
            "id": 1,
            "measure": "PPBUS_AON",
            "expected": "12.0–13.1V",
            "test_point": "C5960 (820-02016) / C5403 (820-02020)",
            "if_ok": 3,
            "if_zero": 2,
            "if_low": "PPBUS_AON is low (e.g. 8–11V) — unstable or partial short. "
                      "Check diode mode on PPBUS_AON (normal ~2.5MΩ). "
                      "If shorted (<100Ω): inject 1V/5A, check Q5230 and capacitors near L5230.",
        },
        {
            "id": 2,
            "instruction": "PPBUS_AON is 0V. Check PPDCIN_AON_CHGR_R (expected 5V — same as charger input).",
            "measure": "PPDCIN_AON_CHGR_R",
            "expected": "5V (charger input voltage)",
            "if_ok": "PPDCIN present but PPBUS 0V — U5200 (ISL9240) is not boosting. "
                     "Check R5221/R5222/R5261/R5262 current sense resistors (should be 1.0Ω each). "
                     "If resistors OK → replace U5200.",
            "if_zero": "PPDCIN is also 0V — CD3217 (UF400/UF500) is not passing voltage. "
                       "Check PP3V3_UPC0_LDO and PP1V5_UPC0_LDO_CORE on both ports. "
                       "If missing on one port → that CD3217 is likely bad.",
        },
        {
            "id": 3,
            "measure": "PP3V8_AON_VDDMAIN",
            "expected": "3.8V",
            "test_point": "C5887 or C5889 (820-02016)",
            "if_ok": 5,
            "if_zero": 4,
            "if_low": "PP3V8_AON is low — check Q5230 (common failure: shorted MOSFET pulls rail down). "
                      "Also check P3V8_PWR_EN enable signal.",
        },
        {
            "id": 4,
            "instruction": "PP3V8_AON is 0V. Check diode mode on PP3V8_AON_VDDMAIN.",
            "expected_diode": "~0.35 (normal)",
            "if_short": "Short on PP3V8 — inject 1V/3A. Common culprits: Q5230, capacitors near L5800.",
            "if_open": "No short — check P3V8_PWR_EN (comes from CHGR_EN_MVR via U5340). "
                       "If P3V8_PWR_EN missing: check U5200 CHGR_EN_MVR output, then U5340.",
            "next": 5,
        },
        {
            "id": 5,
            "measure": "PP5V_S2",
            "expected": "5V",
            "if_ok": 6,
            "if_zero": "PP5V_S2 missing — check P5VS2_EN from U8100 PMU. "
                       "If EN missing: U8100 issue or firmware. "
                       "If EN present but PP5V_S2 missing: check UC300.",
        },
        {
            "id": 6,
            "measure": "PP1V25_S2",
            "expected": "1.25V",
            "if_ok": "All main S2 rails present — DFU should be possible. "
                     "Try DFU recovery via Apple Configurator 2 on another M1 Mac. "
                     "If DFU fails: check NAND and SOC area.",
            "if_zero": "PP1V25_S2 missing — U7700 issue. "
                       "Check diode mode on PP1V25_S2 (normal ~0.49). "
                       "If short: inject 1V/3A. If not shorted: check U7700 enable and replace if needed.",
        },
    ],
},

# ══════════════════════════════════════════════════════════════════
# NO CHARGE / STUCK 5V — USB-C path
# ══════════════════════════════════════════════════════════════════
"nocharge_m1": {
    "boards": ["820-02016", "820-02020", "820-02098"],
    "symptoms": ["no charge", "5v only", "won't charge", "not charging",
                 "usb dead", "port not working"],
    "steps": [
        {
            "id": 1,
            "instruction": "Test both USB-C ports — does the issue affect one or both ports?",
            "type": "confirm",
            "if_one_port": "One port only → likely that port's CD3217 (UF400 for port 0, UF500 for port 1). "
                           "Measure PP3V3_UPC_LDO and PP1V5_UPC_LDO_CORE on the affected port.",
            "if_both": 2,
        },
        {
            "id": 2,
            "measure": "PPBUS_AON",
            "expected": "12–13.1V",
            "if_ok": 3,
            "if_zero": "PPBUS_AON absent — see no-power protocol.",
        },
        {
            "id": 3,
            "instruction": "Both ports at 5V — check if PPBUS reaches 20V after negotiation.",
            "measure": "PPVBUS_USBC0",
            "expected": "5V initially, should reach 20V after PD negotiation",
            "if_stays_5v": "Stuck at 5V on both ports — likely CD3217 firmware issue or both chips bad. "
                           "Check PP3V3_UPC0_LDO and PP3V3_UPC1_LDO (both should be 3.3V). "
                           "Also check CC1/CC2 lines — should not be shorted to GND.",
            "if_ok": "Reaches 20V — charging circuit is working. Issue may be elsewhere.",
        },
    ],
},

}


# ─────────────────────────────────────────────────────────────────
# PROTOCOL MATCHING
# ─────────────────────────────────────────────────────────────────

def find_protocol(board, symptom):
    """Find the best matching protocol for a board + symptom."""
    symptom_lower = symptom.lower()
    best = None
    best_score = 0

    for name, proto in PROTOCOLS.items():
        # Check board match
        board_match = any(board.startswith(b[:7]) for b in proto["boards"])
        if not board_match:
            continue

        # Check symptom match
        score = sum(1 for s in proto["symptoms"] if s in symptom_lower)
        if score > best_score:
            best_score = score
            best = (name, proto)

    return best


def format_protocol_for_prompt(board, symptom, conversation_history=None):
    """
    Given a board + symptom + conversation so far,
    return the next step instruction for the AI.
    """
    result = find_protocol(board, symptom)
    if not result:
        return ""

    proto_name, proto = result

    # Determine current step from conversation history
    current_step = _infer_current_step(proto, conversation_history or [])

    # Format the protocol context
    lines = [f"\n[Diagnostic Protocol: {proto_name}]"]
    lines.append(f"Current step: {current_step['id']}")

    # Show already-measured values from history
    import re as _re
    _val_re = _re.compile(r'(\d+\.?\d*\s*v|short(?:ed)?|open|0v|ol\b|no short)', _re.IGNORECASE)
    measured_so_far = []
    for _step in proto["steps"]:
        if _step["id"] >= current_step["id"]: break
        _net = _step.get("measure")
        if _net:
            for _msg in reversed(conversation_history or []):
                if _msg.get("role") == "user" and _net.upper() in _msg.get("content","").upper():
                    _m = _val_re.search(_msg.get("content",""))
                    if _m:
                        measured_so_far.append(f"  {_net} = {_m.group(0)} (confirmed)")
                        break
    if measured_so_far:
        lines.append("Already confirmed:")
        lines.extend(measured_so_far)

    # If current step net already has a user-reported value, make a decision
    _cur_net = current_step.get("measure","")
    _cur_val = None
    if _cur_net:
        for _msg in reversed(conversation_history or []):
            if _msg.get("role") == "user" and _cur_net.upper() in _msg.get("content","").upper():
                _m = _val_re.search(_msg.get("content",""))
                if _m:
                    _cur_val = _m.group(0).strip()
                    break
    if _cur_val:
        lines.append(f"User reported {_cur_net} = {_cur_val}")
        _v = _cur_val.lower()
        if "short" in _v or _v.startswith("0"):
            _decision = current_step.get("if_zero", "check for short — diode mode, then inject 1V/3A if confirmed")
            lines.append(f"DECISION: {_decision}")
        elif any(x in _v for x in ["12","13","3.8","5v","1.8"]):
            _ok = current_step.get("if_ok")
            if _ok:
                lines.append(f"DECISION: voltage OK — proceed to step {_ok}")
        elif "low" in _v or ("v" in _v and not _v.startswith("12")):
            _low = current_step.get("if_low","partial short — check diode mode")
            lines.append(f"DECISION: {_low}")
        lines.append("GIVE THE NEXT ACTION BASED ON THIS DECISION — do not re-ask for the measurement.")


    if current_step.get("measure"):
        lines.append(f"Next measurement: {current_step['measure']} — expected: {current_step.get('expected','?')}")
        if current_step.get("test_point"):
            lines.append(f"Test point: {current_step['test_point']}")
        lines.append(f"If OK → proceed to step {current_step.get('if_ok','?')}")
        lines.append(f"If 0V → {current_step.get('if_zero','check for short')}")
        if current_step.get("if_low"):
            lines.append(f"If low → {current_step.get('if_low')}")

    elif current_step.get("instruction"):
        lines.append(f"Action: {current_step['instruction']}")
        if current_step.get("if_short"):
            lines.append(f"If short (<0.1Ω): {current_step['if_short']}")
        if current_step.get("if_open"):
            lines.append(f"If no short: {current_step['if_open']}")

    elif current_step.get("actions"):
        lines.append("Actions to take:")
        for a in current_step["actions"]:
            lines.append(f"  {a}")

    lines.append("\nFOLLOW THIS PROTOCOL EXACTLY. Do not deviate or ask about other rails.")

    return "\n".join(lines)


def _infer_current_step(proto, history):
    """
    Infer current step from conversation history.
    Logic: step is 'done' if the user reported a measurement result for it.
    We look for both the net name AND a numeric value in the user's messages.
    """
    import re

    # Patterns that indicate a measurement was reported
    VALUE_PATTERN = re.compile(
        r'(\d+\.?\d*\s*v|\d+\.?\d*\s*volt|short|open|ol|0v|shorted|no short)',
        re.IGNORECASE
    )

    user_messages = [m for m in history if m.get("role") == "user"]

    completed = set()
    for step in proto["steps"]:
        net = step.get("measure", "")
        if not net:
            # confirm/action step — mark done if user replied with yes/done/confirmed
            # OR if they said they don't have the part (skip it)
            for msg in user_messages:
                txt = msg.get("content", "").lower()
                if any(w in txt for w in ["yes", "done", "confirmed", "ok", "yep", "sure",
                                           "external", "screen", "image", "no display", 
                                           "dont have", "don't have", "skip", "no good"]):
                    completed.add(step["id"])
            continue

        net_upper = net.upper()
        # Step is complete if user mentioned this net AND gave a value
        for msg in user_messages:
            txt = msg.get("content", "")
            if net_upper in txt.upper() and VALUE_PATTERN.search(txt):
                completed.add(step["id"])
                break
            # Also complete if AI already asked about it and user gave just a value
            # (user replied to AI question about this net with a number)

    # Find first step not yet completed
    for step in proto["steps"]:
        if step["id"] not in completed:
            return step

    return proto["steps"][-1]


def extract_last_measurement(history, net_name):
    """
    Extract the most recent user-reported value for a specific net.
    Returns a string like '12v', '0v', 'short', or None.
    """
    import re
    VALUE_RE = re.compile(
        r'(\d+\.?\d*\s*[vm]?v?|short(?:ed)?|open|ol|no short)',
        re.IGNORECASE
    )
    net_upper = net_name.upper()
    for msg in reversed(history):
        if msg.get("role") == "user":
            txt = msg.get("content", "")
            if net_upper in txt.upper():
                m = VALUE_RE.search(txt)
                if m:
                    return m.group(0).strip()
    return None


if __name__ == "__main__":
    import sys
    board = sys.argv[1] if len(sys.argv) > 1 else "820-02016"
    symptom = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "no backlight water damage"
    ctx = format_protocol_for_prompt(board, symptom)
    print(ctx or "No protocol found")