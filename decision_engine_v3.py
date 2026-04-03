#!/usr/bin/env python3
"""
decision_engine_v3.py
=====================
DE שולט. LLM רק מנסח.

שלושה עקרונות:
  1. RepairState  — זוכר הכל, כולל current_draw ו-confirmed_rails
  2. get_next_step() — מחזיר JSON נעול. LLM אסור לשנות target/action
  3. validate_llm_output() — מוודא שה-LLM לא "התחכם"

Current-draw shortcuts (הצ'יט):
  0.00–0.02 A  → dead board, charging IC / PPBUS issue
  0.05–0.12 A  → PPBUS up, stuck before SMC wake (T2: classic 5V/60mA pattern)
  0.18–0.30 A  → SMC awake, stuck in S5 (power rails starting)
  0.30–0.60 A  → S3/S4 reached, stuck before S0
  0.60+  A     → S0 attempting, CPU/GPU issue or thermal shutdown
"""

import re
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Power trees — per board family, hierarchical
# כל rail: (name, expected_voltage, parent_rail, fuse/chip_responsible)
# ─────────────────────────────────────────────────────────────────────────────

POWER_TREES = {

    # ── Intel MagSafe era (820-0xxxx) ────────────────────────────────────────
    "intel_magsafe": {
        "boards": ["820-00165", "820-2850", "820-3115", "820-3332",
                   "820-3437", "820-3476", "820-4924", "820-00045",
                   "820-00138"],
        "rails": [
            {"name": "PP3V42_G3H",     "voltage": 3.42, "parent": None,
             "chip": "ISL6259",        "fuse": None,
             "symptom": ["no_power", "no_charge"]},
            {"name": "PPBUS_G3H",      "voltage": 12.6, "parent": "PP3V42_G3H",
             "chip": "ISL6259/U7100",  "fuse": None,
             "symptom": ["no_power", "no_charge"]},
            {"name": "PP5V_S5",        "voltage": 5.0,  "parent": "PPBUS_G3H",
             "chip": "U7501",          "fuse": "FF1",
             "symptom": ["no_power"]},
            {"name": "PP3V3_S5",       "voltage": 3.3,  "parent": "PPBUS_G3H",
             "chip": "U7501",          "fuse": None,
             "symptom": ["no_power"]},
            {"name": "PP5V_S3",        "voltage": 5.0,  "parent": "PP5V_S5",
             "chip": "U7501",          "fuse": None,
             "symptom": ["no_power"]},
            {"name": "PP3V3_S3",       "voltage": 3.3,  "parent": "PP3V3_S5",
             "chip": "U7501",          "fuse": None,
             "symptom": ["no_power"]},
            {"name": "PPVCC_S0_CPU",   "voltage": 1.05, "parent": "PPBUS_G3H",
             "chip": "U8100",          "fuse": None,
             "symptom": ["no_power"]},
            {"name": "PPVIN_LCDBKLT_F","voltage": 12.6, "parent": "PPBUS_G3H",
             "chip": None,             "fuse": "FP800",
             "symptom": ["no_backlight"]},
            {"name": "PPVOUT_LCDBKLT", "voltage": 45.0, "parent": "PPVIN_LCDBKLT_F",
             "chip": "UP800/LP8549",   "fuse": None,
             "symptom": ["no_backlight"]},
        ]
    },

    # ── USB-C Intel T2 (820-008xx) ────────────────────────────────────────────
    "intel_usbc_t2": {
        "boards": ["820-00840", "820-00850", "820-01521", "820-01598",
                   "820-01700", "820-01814", "820-02016", "820-02020"],
        "rails": [
            {"name": "PPBUS_G3H",      "voltage": 12.6, "parent": None,
             "chip": "CD3215/ISL9240", "fuse": None,
             "symptom": ["no_power", "no_charge"]},
            {"name": "PP3V3_G3H",      "voltage": 3.3,  "parent": "PPBUS_G3H",
             "chip": "T2/Calpe",       "fuse": None,
             "symptom": ["no_power"]},
            {"name": "PP5V_S2",        "voltage": 5.0,  "parent": "PPBUS_G3H",
             "chip": "UC300",          "fuse": "FF200",
             "symptom": ["no_power"]},
            {"name": "PP3V3_S3",       "voltage": 3.3,  "parent": "PP3V3_G3H",
             "chip": "T2",             "fuse": None,
             "symptom": ["no_power"]},
            {"name": "PPVCC_S0_CPU",   "voltage": 1.05, "parent": "PPBUS_G3H",
             "chip": "U8100",          "fuse": None,
             "symptom": ["no_power"]},
            {"name": "PPVIN_LCDBKLT_F","voltage": 12.6, "parent": "PPBUS_G3H",
             "chip": None,             "fuse": "FF200",
             "symptom": ["no_backlight"]},
            {"name": "PPVOUT_LCDBKLT", "voltage": 45.0, "parent": "PPVIN_LCDBKLT_F",
             "chip": "UP800",          "fuse": None,
             "symptom": ["no_backlight"]},
        ]
    },

    # ── Apple Silicon M1/M2 (820-028xx / 820-029xx) ───────────────────────────
    "apple_silicon_m1": {
        "boards": ["820-02382", "820-02443", "820-02773", "820-02840",
                   "820-02841", "820-02863", "820-03250", "820-03971"],
        "rails": [
            {"name": "PPBUS_AON",      "voltage": 12.6, "parent": None,
             "chip": "ISL9240/CD3215", "fuse": None,
             "symptom": ["no_power", "no_charge"]},
            {"name": "PP3V8_AON",      "voltage": 3.8,  "parent": "PPBUS_AON",
             "chip": "PMU/U8100",      "fuse": None,
             "symptom": ["no_power"]},
            {"name": "PP5V_S2",        "voltage": 5.0,  "parent": "PPBUS_AON",
             "chip": "UC300",          "fuse": "FF200",
             "symptom": ["no_power"]},
            {"name": "PP1V8_AON",      "voltage": 1.8,  "parent": "PP3V8_AON",
             "chip": "PMU",            "fuse": None,
             "symptom": ["no_power"]},
            {"name": "PPVCC_CPU",      "voltage": 1.05, "parent": "PPBUS_AON",
             "chip": "PMU",            "fuse": None,
             "symptom": ["no_power"]},
            {"name": "PPVIN_LCDBKLT_F","voltage": 12.6, "parent": "PPBUS_AON",
             "chip": None,             "fuse": "FF200",
             "symptom": ["no_backlight"]},
            {"name": "PPVOUT_LCDBKLT", "voltage": 45.0, "parent": "PPVIN_LCDBKLT_F",
             "chip": "LP8549",         "fuse": None,
             "symptom": ["no_backlight"]},
            {"name": "LCDBKLT_EN_L",   "voltage": 3.3,  "parent": "PPVOUT_LCDBKLT",
             "chip": "LP8549",         "fuse": None,
             "symptom": ["no_backlight"]},
            {"name": "LCDBKLT_PWM",    "voltage": 3.3,  "parent": "PPVOUT_LCDBKLT",
             "chip": "LP8549",         "fuse": None,
             "symptom": ["no_backlight"]},
        ]
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Current-draw → automatic rail confirmation table
# ─────────────────────────────────────────────────────────────────────────────

CURRENT_SHORTCUTS = [
    # (min_A, max_A, confirmed_rails, skip_to_symptom_hint, note)
    (0.00, 0.02, [],
     "check_charging_circuit",
     "Board dead. No rails up. Start with PP3V42_G3H / PPBUS_G3H."),

    (0.02, 0.08, ["PP3V42_G3H"],
     "check_ppbus",
     "PP3V42_G3H likely up (SMC alive), PPBUS_G3H may be missing or shorted."),

    (0.08, 0.20, ["PP3V42_G3H", "PPBUS_G3H"],
     "check_s5_rails",
     "PPBUS_G3H up. Board in G3/S5. Check S5 rails (PP5V_S5, PP3V3_S5)."),

    (0.20, 0.40, ["PP3V42_G3H", "PPBUS_G3H", "PP5V_S5", "PP3V3_S5"],
     "check_s3_rails",
     "S5 rails up. Board reaching S3. Check PM_SLP_S4_L and S3 rails."),

    (0.40, 0.70, ["PP3V42_G3H", "PPBUS_G3H", "PP5V_S5", "PP3V3_S5",
                  "PP5V_S3", "PP3V3_S3"],
     "check_s0_rails",
     "S3 rails up. Board attempting S0. Check CPU/GPU vcore rails."),

    (0.70, 9.99, ["PP3V42_G3H", "PPBUS_G3H", "PP5V_S5", "PP3V3_S5",
                  "PP5V_S3", "PP3V3_S3", "PPVCC_S0_CPU"],
     "check_s0_full",
     "All main rails likely up. Board in S0. Check display, GPU, or thermal."),
]


# Rail name aliases — Intel ↔ Apple Silicon naming differences
# DE checks both the canonical name AND its alias when looking up confirmed_rails
RAIL_ALIASES: dict[str, str] = {
    "PPBUS_G3H":   "PPBUS_AON",
    "PPBUS_AON":   "PPBUS_G3H",
    "PP3V42_G3H":  "PP3V8_AON",
    "PP3V8_AON":   "PP3V42_G3H",
    "PP3V3_G3H":   "PP3V3_G3H",   # same across families
}


def _is_confirmed(state: "RepairState", rail_name: str) -> str:
    """Return status of a rail, checking both its name and any alias."""
    direct = state.confirmed_rails.get(rail_name, {}).get("status", "unknown")
    if direct != "unknown":
        return direct
    alias = RAIL_ALIASES.get(rail_name)
    if alias:
        return state.confirmed_rails.get(alias, {}).get("status", "unknown")
    return "unknown"


def _get_confirmed_entry(state: "RepairState", rail_name: str) -> dict:
    """Return confirmed_rails entry, checking alias if primary is missing."""
    if rail_name in state.confirmed_rails:
        return state.confirmed_rails[rail_name]
    alias = RAIL_ALIASES.get(rail_name)
    if alias and alias in state.confirmed_rails:
        return state.confirmed_rails[alias]
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Action templates — DE fills target, LLM only translates to natural language
# ─────────────────────────────────────────────────────────────────────────────

ACTION_TEMPLATES = {
    "measure_voltage": {
        "instruction": "Measure voltage on {target}. Expected: {expected_voltage}V.",
        "follow_up":   "What voltage do you read?",
    },
    "check_fuse": {
        "instruction": "Check fuse {target} — measure both sides. "
                       "If {expected_voltage}V input but 0V output → fuse is blown, replace it.",
        "follow_up":   "Input voltage? Output voltage?",
    },
    "check_short": {
        "instruction": "Check {target} for short to ground. "
                       "Set multimeter to resistance/diode mode, probe {target} to GND. "
                       "Reading below 10Ω = shorted.",
        "follow_up":   "Resistance reading? (or 'short' / 'open')",
    },
    "inject_voltage": {
        "instruction": "Inject 1V / 3A directly onto {target}. "
                       "Watch for the component that heats up — that is the shorted part.",
        "follow_up":   "Which component got warm?",
    },
    "check_enable_signal": {
        "instruction": "Check enable signal {target}. "
                       "Should be high ({expected_voltage}V) when board is powered. "
                       "0V here means the chip upstream is not enabling this rail.",
        "follow_up":   "Voltage on enable pin?",
    },
    "replace_component": {
        "instruction": "Replace {target}. "
                       "This component has been identified as the fault.",
        "follow_up":   "After replacement — does the board power on?",
    },
    "check_chip": {
        "instruction": "Focus on {target}. "
                       "Check all input voltages and enable signals on this IC. "
                       "If inputs OK but output missing → chip is dead.",
        "follow_up":   "Input voltages present? Enable signal present?",
    },
    "diode_mode_scan": {
        "instruction": "Scan {target} rail in diode mode. "
                       "Probe every capacitor on this rail — the one reading 0.000 (short) "
                       "is the suspect.",
        "follow_up":   "Any cap reading 0.000?",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# RepairState
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RepairState:
    board: str = ""                          # e.g. "820-02016"
    board_family: str = ""                   # e.g. "apple_silicon_m1"
    symptom: str = ""                        # "no_power" | "no_charge" | "no_backlight" | "no_image"

    # Current draw — the "cheat code"
    current_draw_amps: Optional[float] = None   # מה ה-bench PSU מראה

    # confirmed_rails: net_name → {"value": "12.6V", "status": "ok"|"short"|"missing"|"low"}
    confirmed_rails: dict = field(default_factory=dict)

    # Step tracking — avoid asking the same question twice
    step_history: list = field(default_factory=list)   # list of action dicts already sent
    last_action: Optional[dict] = None

    # Repair conclusion (when reached)
    conclusion: Optional[str] = None        # e.g. "replace FP800"
    resolved: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Utility: resolve board family
# ─────────────────────────────────────────────────────────────────────────────

def resolve_board_family(board: str) -> Optional[str]:
    """Return the family key for a given board number."""
    board_clean = board.strip().upper().replace(" ", "")
    for family, data in POWER_TREES.items():
        for b in data["boards"]:
            if b.upper().replace(" ", "") in board_clean or board_clean in b.upper():
                return family
    return None


def get_rail_tree(family: str) -> list:
    return POWER_TREES.get(family, {}).get("rails", [])


# ─────────────────────────────────────────────────────────────────────────────
# apply_current_shortcuts — auto-confirm rails based on current draw
# ─────────────────────────────────────────────────────────────────────────────

def apply_current_shortcuts(state: RepairState) -> str:
    """
    Given state.current_draw_amps, auto-populate state.confirmed_rails
    with rails that are 'implied ok' and return a hint for the DE.
    Returns skip_to hint string.
    """
    if state.current_draw_amps is None:
        return ""

    amps = state.current_draw_amps
    matched_note = ""
    skip_hint = ""
    implied_rails = []

    for (min_a, max_a, rails, hint, note) in CURRENT_SHORTCUTS:
        if min_a <= amps < max_a:
            implied_rails = rails
            skip_hint = hint
            matched_note = note
            break

    # Auto-confirm implied rails (only if not already explicitly measured)
    for rail_name in implied_rails:
        if rail_name not in state.confirmed_rails:
            state.confirmed_rails[rail_name] = {
                "value": "implied_ok",
                "status": "ok",
                "source": "current_draw_inference"
            }

    return skip_hint


# ─────────────────────────────────────────────────────────────────────────────
# get_next_step — the core DE function
# Returns a locked JSON dict. LLM only translates template to natural language.
# ─────────────────────────────────────────────────────────────────────────────

def get_next_step(state: RepairState) -> dict:
    """
    Determine the next diagnostic step.

    Returns:
        {
          "action": str,
          "target": str,
          "template": str,          ← LLM fills this into natural language ONLY
          "follow_up": str,
          "expected_voltage": float | None,
          "step_number": int,
          "locked": True,            ← signals to LLM: do not deviate
          "debug_reason": str        ← internal explanation (not shown to user)
        }

    Or if resolved:
        {"resolved": True, "conclusion": str, "action": "replace_component", ...}
    """
    if state.resolved:
        return {"resolved": True, "conclusion": state.conclusion}

    if not state.board_family:
        state.board_family = resolve_board_family(state.board) or ""

    # Apply current-draw shortcuts first
    skip_hint = apply_current_shortcuts(state)

    rails = get_rail_tree(state.board_family)
    symptom = state.symptom

    # Filter to rails relevant for this symptom
    relevant = [r for r in rails if symptom in r.get("symptom", [])]

    # Walk the hierarchy: find first rail NOT yet confirmed as OK
    for rail in relevant:
        rail_name = rail["name"]
        status = _is_confirmed(state, rail_name)
        confirmed = _get_confirmed_entry(state, rail_name)

        if status == "ok":
            continue  # already verified — skip

        if status == "short":
            # Rail is shorted → inject voltage onto it
            return _build_step(
                action="inject_voltage",
                target=rail_name,
                expected_voltage=1.0,
                reason=f"{rail_name} confirmed shorted. Inject to find hot component.",
                step_number=len(state.step_history) + 1,
            )

        if status == "missing":
            # 0V + no short → check fuse, then chip
            fuse = rail.get("fuse")
            chip = rail.get("chip")
            # Split multi-chip fields ("ISL6259/U7100") for individual lookup
            chip_tokens = [c.strip() for c in re.split(r"[/,]", chip)] if chip else []

            if fuse and _is_confirmed(state, fuse) not in ("ok", "missing"):
                return _build_step(
                    action="check_fuse",
                    target=fuse,
                    expected_voltage=rail["voltage"],
                    reason=f"{rail_name} is 0V / no short. Fuse {fuse} is the prime suspect.",
                    step_number=len(state.step_history) + 1,
                )
            elif chip:
                # Check each chip token — skip ones already tried
                for chip_token in chip_tokens:
                    chip_status = _is_confirmed(state, chip_token)
                    if chip_status in ("ok", "missing"):
                        continue  # already tried — move on
                    return _build_step(
                        action="check_chip",
                        target=chip_token,
                        expected_voltage=rail["voltage"],
                        reason=f"{rail_name} is 0V. Check {chip_token}.",
                        step_number=len(state.step_history) + 1,
                    )
                # All chips tried and still missing → escalate
                return {
                    "action": "escalate",
                    "target": rail_name,
                    "template": (
                        f"{rail_name} is 0V. All associated chips ({chip}) have been checked. "
                        f"Inspect solder joints, check enable signal, or consider board-level trace damage."
                    ),
                    "follow_up": "Any visible corrosion or damage near the area?",
                    "locked": True,
                    "debug_reason": f"Exhausted all chips for missing {rail_name}.",
                    "step_number": len(state.step_history) + 1,
                }

        if status == "low":
            # Rail present but voltage is low → diode scan for partial short
            return _build_step(
                action="diode_mode_scan",
                target=rail_name,
                expected_voltage=rail["voltage"],
                reason=f"{rail_name} voltage is low (partial short suspected).",
                step_number=len(state.step_history) + 1,
            )

        # status == "unknown" → measure it
        # For no_backlight: skip parent check — board is running so main rails are OK.
        # For no_power / no_charge: check parent first (top-down rule).
        parent_name = rail.get("parent")
        if parent_name and symptom != "no_backlight":
            parent_status = _is_confirmed(state, parent_name)
            if parent_status != "ok":
                # Measure parent first
                parent_rail = next((r for r in rails if r["name"] == parent_name), None)
                if parent_rail:
                    return _build_step(
                        action="measure_voltage",
                        target=parent_name,
                        expected_voltage=parent_rail["voltage"],
                        reason=f"Need {parent_name} before checking {rail_name}. Top-down rule.",
                        step_number=len(state.step_history) + 1,
                    )

        # Parent is OK, measure this rail
        return _build_step(
            action="measure_voltage",
            target=rail_name,
            expected_voltage=rail["voltage"],
            reason=f"Next unconfirmed rail in symptom path for '{symptom}'.",
            step_number=len(state.step_history) + 1,
        )

    # All relevant rails are OK — no fault found in power tree
    return {
        "action": "escalate",
        "target": "advanced_diagnostics",
        "template": "All power rails check out. Issue may be firmware, SMC, or T2. "
                    "Consider DFU restore or check enable signals / PG lines.",
        "follow_up": "Has a DFU restore been attempted?",
        "locked": True,
        "debug_reason": "Exhausted all rails in symptom tree without finding fault.",
        "step_number": len(state.step_history) + 1,
    }


def _build_step(action: str, target: str, expected_voltage: float,
                reason: str, step_number: int) -> dict:
    template_def = ACTION_TEMPLATES.get(action, {})
    template_str = template_def.get("instruction", "Check {target}.")
    follow_up = template_def.get("follow_up", "Result?")

    return {
        "action": action,
        "target": target,
        "expected_voltage": expected_voltage,
        "template": template_str.format(target=target, expected_voltage=expected_voltage),
        "follow_up": follow_up,
        "locked": True,
        "debug_reason": reason,
        "step_number": step_number,
    }


# ─────────────────────────────────────────────────────────────────────────────
# update_state — parse technician's measurement result into RepairState
# ─────────────────────────────────────────────────────────────────────────────

def update_state(state: RepairState, user_message: str,
                 last_step: Optional[dict] = None) -> RepairState:
    """
    Parse what the technician reported and update confirmed_rails.
    """
    text = user_message.lower().strip()
    target = (last_step or {}).get("target", "")
    expected = (last_step or {}).get("expected_voltage", 0.0)

    if not target:
        return state

    # Extract voltage reading
    v_match = re.search(r"(\d+\.?\d*)\s*v", text)
    measured_v = float(v_match.group(1)) if v_match else None

    # Classify result
    is_short = bool(re.search(r"\bshort(ed)?\b|0\.0{1,3}\b", text))
    is_open = bool(re.search(r"\bopen\b|\bol\b|no short|infinity", text))
    is_blown_fuse = (last_step or {}).get("action") == "check_fuse" and (
        (measured_v is not None and measured_v < 0.5) or is_open
    )

    action = (last_step or {}).get("action", "")

    # ── Verbal / confirmatory responses (no voltage number) ──────────────────
    # Handles: "ok", "yes", "present", "replaced still same", "chip is fine", etc.
    is_ok_verbal = bool(re.search(
        r"\b(ok|okay|yes|present|good|fine|working|all present|confirmed)\b", text
    ))
    is_bad_verbal = bool(re.search(
        r"\b(bad|dead|faulty|not present|missing|still (the )?same|no (output|signal|enable)|"
        r"replaced.{0,20}(same|still)|nothing|no luck)\b", text
    ))

    # ── Build confirmed entry ─────────────────────────────────────────────────
    if is_short:
        entry = {"value": "short", "status": "short", "source": "measured"}

    elif is_blown_fuse:
        entry = {"value": "blown", "status": "missing", "source": "measured"}

    elif measured_v is not None:
        tolerance = expected * 0.15 if expected else 0.5
        if measured_v < 0.1:
            entry = {"value": f"{measured_v}V", "status": "missing", "source": "measured"}
        elif abs(measured_v - expected) <= tolerance:
            entry = {"value": f"{measured_v}V", "status": "ok", "source": "measured"}
        else:
            entry = {"value": f"{measured_v}V", "status": "low", "source": "measured"}

    elif is_ok_verbal:
        # Any action: "ok / present / fine / confirmed" → mark rail as ok
        entry = {"value": "ok_verbal", "status": "ok", "source": "measured"}

    elif is_bad_verbal:
        # Any action: "still same / replaced / bad / missing" → mark as missing
        # Exception: if replaced + working, treat as resolved
        if action == "replace_component" and ("boot" in text or "work" in text or "fixed" in text):
            entry = {"value": "replaced_ok", "status": "ok", "source": "measured"}
            state.resolved = True
            state.conclusion = f"Replaced {target} — board recovered."
        else:
            entry = {"value": "bad_verbal", "status": "missing", "source": "measured"}

    else:
        # Truly unparseable — avoid infinite loop: if asked same target twice, force advance
        asked_count = sum(1 for s in state.step_history if s.get("target") == target)
        if asked_count >= 2:
            entry = {"value": "assumed_ok", "status": "ok", "source": "inferred"}
        else:
            return state

    state.confirmed_rails[target] = entry
    if last_step:
        state.step_history.append({**last_step, "result": entry})
        state.last_action = last_step

    # Fuse replacement resolution
    if is_blown_fuse and action == "check_fuse":
        state.conclusion = f"Replace fuse {target}"
        state.resolved = True

    return state


# ─────────────────────────────────────────────────────────────────────────────
# validate_llm_output — the guardrail
# Ensures LLM didn't swap target or suggest something not in the DE step
# ─────────────────────────────────────────────────────────────────────────────

# Nets that LLM commonly hallucinates — if any appear without DE sanction, reject
def _build_sanctioned_set() -> set[str]:
    """
    Dynamically derive every net name, fuse, and chip from POWER_TREES.
    Called fresh each validation so new entries in POWER_TREES are picked up
    automatically — no manual sync required.
    """
    sanctioned: set[str] = set()
    for family_data in POWER_TREES.values():
        for rail in family_data["rails"]:
            sanctioned.add(rail["name"].upper())
            if rail.get("fuse"):
                sanctioned.add(rail["fuse"].upper())
            if rail.get("chip"):
                # chip field may be "ISL6259/U7100" — split on "/" and ","
                for part in re.split(r"[/,]", rail["chip"]):
                    token = part.strip().upper()
                    if token:
                        sanctioned.add(token)
    return sanctioned


def validate_llm_output(llm_text: str, de_step: dict) -> tuple[bool, str]:
    """
    Check that LLM response:
      1. Contains the exact target from DE step
      2. Does not mention an unsanctioned net/component as a target

    Returns: (is_valid: bool, reason: str)
    """
    locked_target = de_step.get("target", "").upper()
    text_upper = llm_text.upper()

    # Rule 1: locked target must appear in LLM output
    if locked_target and locked_target not in text_upper:
        return False, (
            f"LLM omitted the required target '{de_step['target']}'. "
            f"Must mention it explicitly."
        )

    # Rule 2: LLM must not introduce a different net as the primary focus.
    # Build the sanctioned set fresh from POWER_TREES so it is always in sync —
    # any rail/fuse/chip added to POWER_TREES is automatically covered here
    # without a separate manual update.
    sanctioned = _build_sanctioned_set()

    # The regex is intentionally broad; the sanctioned-set filter below
    # discards false positives (e.g. a word that starts with "UP" + 3 digits
    # but is not a real component in any POWER_TREES entry).
    mentioned = set(re.findall(
        r"\b(PP[A-Z0-9_]{2,30}|FF\d{1,3}|FP\d{1,3}|UP\d{3}|LP\d{4}|"
        r"ISL\d{4}|CD\d{4}|UC\d{3}|U\d{4}|T2)\b",
        text_upper
    ))

    # Only flag tokens that exist in the current POWER_TREES data.
    # Anything not in `sanctioned` is either a regex false-positive or a net
    # not yet defined — both are ignored rather than raising a spurious error.
    unsanctioned = {n for n in mentioned - {locked_target} if n in sanctioned}

    if unsanctioned:
        return False, (
            f"LLM introduced unsanctioned target(s): {unsanctioned}. "
            f"Only '{de_step['target']}' is allowed in this step."
        )

    return True, "ok"


# ─────────────────────────────────────────────────────────────────────────────
# format_step_for_llm — builds the system prompt injection for this step
# ─────────────────────────────────────────────────────────────────────────────

def format_step_for_llm(de_step: dict) -> str:
    """
    Returns the string to inject into the LLM system prompt / user context.
    Instructs LLM to ONLY rephrase the template — nothing more.
    """
    if de_step.get("resolved"):
        return (
            f"\n\n[DIAGNOSIS COMPLETE]\n"
            f"Conclusion: {de_step.get('conclusion')}\n"
            f"Instruct technician to perform this repair."
        )

    return (
        f"\n\n[DECISION ENGINE — STEP {de_step['step_number']}]\n"
        f"Action   : {de_step['action']}\n"
        f"Target   : {de_step['target']}\n"
        f"Template : {de_step['template']}\n"
        f"Follow-up: {de_step['follow_up']}\n\n"
        f"STRICT RULES FOR YOUR RESPONSE:\n"
        f"1. Rephrase the Template above into natural, friendly English. Nothing else.\n"
        f"2. You MUST mention '{de_step['target']}' exactly as written.\n"
        f"3. End with the Follow-up question EXACTLY as written.\n"
        f"4. Do NOT suggest any other component, rail, or measurement.\n"
        f"5. Do NOT explain why this step was chosen.\n"
        f"6. Max 3 sentences total.\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# High-level interface — used by start_server.py
# ─────────────────────────────────────────────────────────────────────────────

def process_repair_turn(
    state: RepairState,
    user_message: str,
) -> tuple[dict, str]:
    """
    Main entry point per chat turn.

    1. Updates state from user's last message
    2. Runs DE to get next step
    3. Returns (de_step, llm_injection_string)

    start_server.py appends llm_injection to the LLM context,
    then validates LLM output with validate_llm_output().
    """
    # Update state if we have a pending action
    if state.last_action:
        state = update_state(state, user_message, state.last_action)

    # Apply current shortcuts if current draw just provided
    if state.current_draw_amps is not None and not state.confirmed_rails:
        apply_current_shortcuts(state)

    # Get next step from DE
    de_step = get_next_step(state)

    # Record as pending
    state.last_action = de_step if not de_step.get("resolved") else None

    # Build LLM injection
    llm_injection = format_step_for_llm(de_step)

    return de_step, llm_injection


# ─────────────────────────────────────────────────────────────────────────────
# Quick smoke test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Decision Engine v3 — smoke test")
    print("=" * 60)

    # Scenario: M1 MacBook, no backlight, bench PSU shows 0.35A
    state = RepairState(
        board="820-02016",
        symptom="no_backlight",
        current_draw_amps=0.35,
    )
    state.board_family = resolve_board_family(state.board)
    print(f"Board family: {state.board_family}")

    # Apply current shortcuts
    hint = apply_current_shortcuts(state)
    print(f"Current draw 0.35A → skip_hint: '{hint}'")
    print(f"Auto-confirmed rails: {list(state.confirmed_rails.keys())}")
    print()

    # Get step 1
    step1, injection1 = process_repair_turn(state, "board shows 0.35A on PSU")
    print(f"Step 1: action={step1['action']}, target={step1['target']}")
    print(f"Template: {step1['template']}")
    print()

    # Simulate technician: "FP800 measures 12.6V in, 0V out"
    step2, injection2 = process_repair_turn(state, "FP800: 12.6V input, 0V output")
    print(f"After 'FP800 blown' report:")
    print(f"Resolved: {state.resolved}")
    print(f"Conclusion: {state.conclusion}")
    print()

    # Test validator
    good_response = "Please check fuse FF200 on both sides."
    bad_response  = "Please check PPVBUS_G3H and also PPVCC_CPU."
    de_step_test  = {"target": "FF200", "action": "check_fuse", "step_number": 1}

    ok1, r1 = validate_llm_output(good_response, de_step_test)
    ok2, r2 = validate_llm_output(bad_response,  de_step_test)
    print(f"Validator — good response: valid={ok1}, reason={r1}")
    print(f"Validator — bad  response: valid={ok2}, reason={r2}")
