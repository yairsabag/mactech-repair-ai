#!/usr/bin/env python3
"""
case_intake.py — v2
====================
Converts free-form technician messages into structured repair state.
"""
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CaseIntake:
    symptom: str = ""
    board: str = ""
    water_damage: Optional[bool] = None   # None=unknown, False=no water, True=water found
    short_found: Optional[bool] = None    # None=unknown, False=no short, True=short found
    measurements: dict = field(default_factory=dict)
    actions_done: list = field(default_factory=list)
    components_replaced: list = field(default_factory=list)
    components_removed: list = field(default_factory=list)
    negatives: list = field(default_factory=list)
    current_stage: str = "initial"
    confidence: float = 0.0


# ── Normalization ──────────────────────────────────────────────────────────

def normalize_component(s: str) -> str:
    return s.strip().upper()

def parse_numeric_voltage(val: str) -> Optional[float]:
    """Parse '1v', '0.5-1v', '12V', '3.8' → float or None."""
    val = val.lower().strip()
    # Range like "0.5-1v" → take lower
    m = re.match(r'(\d+\.?\d*)\s*[-–]\s*\d+\.?\d*\s*v?', val)
    if m: return float(m.group(1))
    m = re.match(r'(\d+\.?\d*)\s*v?', val)
    if m: return float(m.group(1))
    return None

def normalize_value(val: str) -> str:
    """Normalize measurement value to lowercase stripped string."""
    return val.lower().strip().replace(' ', '')


# ── Patterns ───────────────────────────────────────────────────────────────

# Component token: U5200, F5200, C5254, R1234, etc.
COMP_TOKEN = r'([URFQCDLTPKJSBN][A-Z]?\d{3,4}[A-Z]?)'
COMP_RE    = re.compile(COMP_TOKEN)

# Net names
NET_RE = re.compile(
    r'\b(PP[A-Z0-9_]{2,30}|PPBUS[A-Z_]*|[A-Z]{2,}_[A-Z0-9_]{2,25})\b'
)

# Net+value: "PPBUS_AON 1v" or "PPBUS_AON: 1V"
NET_VALUE_RE = re.compile(
    r'\b(PP[A-Z0-9_]{2,30})\b\s*[:=]?\s*(\d+\.?\d*\s*(?:v|a|ohm|k)?)',
    re.IGNORECASE
)

REPLACE_RE = re.compile(
    rf'\b(replac(?:ed|e|ing)|swap(?:ped)?|changed?|install(?:ed)?)\s+{COMP_TOKEN}',
    re.IGNORECASE
)
REMOVE_RE = re.compile(
    rf'\b(remov(?:ed|e|)|took?\s+out|lifted?|pulled?)\s+{COMP_TOKEN}',
    re.IGNORECASE
)


# ── Parsers ────────────────────────────────────────────────────────────────

def parse_measurements(text: str) -> dict:
    """Extract net=value pairs. Prioritizes NET VALUE pattern over raw numbers."""
    results = {}

    # Priority 1: explicit "NET value" pattern
    for m in NET_VALUE_RE.finditer(text):
        net = m.group(1).upper()
        val = normalize_value(m.group(2))
        results[net] = val

    # Priority 2: sentence-level — find net + nearby value
    sentences = re.split(r'[.,;\n]', text)
    for sent in sentences:
        nets = NET_RE.findall(sent.upper())
        # Find voltage value in sentence (exclude things like "5V 0A" at start)
        vm = re.search(r'(\d+\.?\d*)\s*(v|volt)\b', sent, re.IGNORECASE)
        if nets and vm and nets[0] not in results:
            val = normalize_value(vm.group(1) + (vm.group(2) or 'v'))
            results[nets[0]] = val

    return results


def parse_actions_done(text: str) -> tuple:
    """Extract replaced/removed components."""
    replaced, removed, actions = [], [], []

    for m in REPLACE_RE.finditer(text):
        comp = normalize_component(m.group(2))
        if comp not in replaced:
            replaced.append(comp)
            actions.append(f"replaced {comp}")

    for m in REMOVE_RE.finditer(text):
        comp = normalize_component(m.group(2))
        if comp not in removed:
            removed.append(comp)
            actions.append(f"removed {comp}")

    return replaced, removed, actions


def parse_negatives(text: str) -> list:
    """Extract negative findings."""
    negs = []
    t = text.lower()

    if re.search(r'no\s+short(?:\s+found)?|not\s+shorted', t):
        negs.append("no_short")
    if re.search(r'no\s+water|no\s+liquid|no\s+corrosion|not\s+water', t):
        negs.append("no_water_damage")
    if re.search(r'still\s+(same|the\s+same)|no\s+change|nothing\s+changed|still\s+\d', t):
        negs.append("still_same_after_action")

    # Positive detections
    if re.search(r'water\s+damage|liquid\s+damage|corrosion\s+found', t) and 'no_water_damage' not in negs:
        negs.append("water_damage_confirmed")
    if re.search(r'\bshort(?:ed)?\b(?!\s*found\s+no)', t):
        negs.append("short_confirmed")

    return negs


# ── Stage inference ────────────────────────────────────────────────────────

CHARGER_ICS = {'U5200', 'U5300', 'U5340'}
FUSES       = {'F5200', 'FF200', 'FF201', 'F5100', 'F5000'}

def infer_stage(intake: CaseIntake) -> str:
    replaced = set(intake.components_replaced)
    removed  = set(intake.components_removed)
    negs     = set(intake.negatives)
    meas     = intake.measurements

    # Normalize PPBUS voltage for comparison
    ppbus_raw = meas.get('PPBUS_AON', '')
    ppbus_v = parse_numeric_voltage(ppbus_raw) if ppbus_raw else None
    ppbus_low = ppbus_v is not None and ppbus_v < 5.0

    # Post charger IC replacement, still issue
    if replaced & CHARGER_ICS:
        if 'still_same_after_action' in negs or ppbus_low:
            return "post_charger_ic_replacement"

    # Fuse removed
    if removed & FUSES:
        if 'no_short' in negs:
            return "post_fuse_removal"

    # Has measurements, no short, nothing replaced yet
    if ppbus_low and 'no_short' in negs and not replaced:
        return "voltage_verification"

    # Has measurements + short
    if ppbus_low and 'short_confirmed' in negs:
        return "injection_phase"

    # Has some data
    if meas or replaced or removed:
        return "advanced_initial"

    return "initial"


# ── Next action inference ──────────────────────────────────────────────────

def infer_next_action(intake: CaseIntake) -> Optional[dict]:
    """Return recommended next action, or None if stage is initial."""
    stage = intake.current_stage
    if stage == "initial":
        return None

    replaced = set(intake.components_replaced)
    removed  = set(intake.components_removed)

    if stage == "post_charger_ic_replacement":
        charger = next((c for c in intake.components_replaced if c in CHARGER_ICS), 'U5200')
        keep_str = (", ".join(f"keep {c} removed" for c in removed) + ". ") if removed else ""
        return {
            "decision_type": "ask_measurement",
            "net": f"{charger}_SIGNALS",
            "ask": (
                f"{keep_str}Post {charger} basic voltages: "
                f"P_IN, AUX_DET, SMC_RST_IN, VDD/P, EN_MVR, AUX_OK, A/BMON. "
                f"Also check resistance across C5220/C5260."
            ),
            "reason": (
                f"{charger} was already replaced and the issue persists. "
                f"Next step is to verify surrounding signals on {charger}."
            ),
            "stage": stage,
        }

    if stage == "post_fuse_removal":
        fuse = next((c for c in removed if c.startswith('F')), 'F5200')
        return {
            "decision_type": "ask_measurement",
            "net": "fuse_pads",
            "ask": (
                f"With {fuse} removed, measure voltage on both pads of the fuse footprint "
                f"and check resistance to ground on the downstream side."
            ),
            "reason": f"{fuse} was removed. Need to isolate if short is upstream or downstream.",
            "stage": stage,
        }

    if stage == "voltage_verification":
        ppbus = intake.measurements.get('PPBUS_AON', '')
        return {
            "decision_type": "ask_measurement",
            "net": "PPBUS_AON_SOURCE",
            "ask": (
                f"PPBUS_AON is {ppbus} with no short. "
                f"Measure voltage on PPVBUS_G3H and PP3V8_AON_VDDMAIN to check upstream rails."
            ),
            "reason": "Low PPBUS with no short — need to check upstream power path.",
            "stage": stage,
        }

    return None


# ── State summary ──────────────────────────────────────────────────────────

def intake_to_state_summary(intake: CaseIntake) -> str:
    lines = ["[Case Intake — known facts]:"]

    if intake.measurements:
        lines.append("Measurements: " + ", ".join(
            f"{k}={v}" for k, v in intake.measurements.items()))

    if intake.components_replaced:
        lines.append("Already replaced (no fix): " + ", ".join(intake.components_replaced))

    if intake.components_removed:
        lines.append("Currently removed: " + ", ".join(intake.components_removed))

    neg_str = []
    if "no_short" in intake.negatives:        neg_str.append("no short found")
    if "no_water_damage" in intake.negatives: neg_str.append("no water damage")
    if "still_same_after_action" in intake.negatives: neg_str.append("issue persists after last action")
    if neg_str:
        lines.append("Negatives: " + ", ".join(neg_str))

    lines.append(f"Stage: {intake.current_stage}")

    if intake.current_stage != "initial":
        lines.append("⚠ Do NOT restart from step 1 — continue from current stage.")

    return "\n".join(lines)


# ── Intake from multiple messages ──────────────────────────────────────────

def parse_case_intake(message: str, board: str = "",
                      recent_user_msgs: list = None) -> CaseIntake:
    """
    Parse intake from one or multiple user messages.
    recent_user_msgs: list of recent user message strings to merge.
    """
    # Combine messages for richer parsing
    all_text = message
    if recent_user_msgs:
        all_text = " ".join(recent_user_msgs) + " " + message

    intake = CaseIntake(board=board)

    intake.measurements         = parse_measurements(all_text)
    replaced, removed, actions  = parse_actions_done(all_text)
    intake.components_replaced  = replaced
    intake.components_removed   = removed
    intake.actions_done         = actions
    intake.negatives            = parse_negatives(all_text)

    # Fix: None=unknown, not True/False by default
    intake.water_damage = (
        False if "no_water_damage" in intake.negatives
        else True if "water_damage_confirmed" in intake.negatives
        else None
    )
    intake.short_found = (
        False if "no_short" in intake.negatives
        else True if "short_confirmed" in intake.negatives
        else None
    )

    intake.current_stage = infer_stage(intake)

    score = 0.0
    if intake.measurements:         score += 0.3
    if intake.actions_done:         score += 0.4
    if intake.negatives:            score += 0.2
    if intake.current_stage != "initial": score += 0.1
    intake.confidence = min(1.0, score)

    return intake


if __name__ == "__main__":
    import json

    msg = "5V 0A - no short found no water damage. PPBUS_AON 1v. take out F5200 - C5254 still 0.5-1v. replace U5200 still the same.."
    intake = parse_case_intake(msg, "820-02016")

    print("=== Intake ===")
    print(f"Stage:     {intake.current_stage}")
    print(f"Meas:      {intake.measurements}")
    print(f"Replaced:  {intake.components_replaced}")
    print(f"Removed:   {intake.components_removed}")
    print(f"Negatives: {intake.negatives}")
    print(f"Water:     {intake.water_damage}  Short: {intake.short_found}")
    print(f"Confidence:{intake.confidence:.2f}")

    print("\n=== Next Action ===")
    action = infer_next_action(intake)
    print(json.dumps(action, indent=2) if action else "None")

    print("\n=== State Summary ===")
    print(intake_to_state_summary(intake))
