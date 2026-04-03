#!/usr/bin/env python3
"""
decision_engine.py
==================
Rules-based decision engine for MacBook board repair.

Architecture:
  Case Retriever  → finds similar forum cases
  State Tracker   → remembers what was already measured
  Decision Engine → decides next step / final fix
  Response Gen    → LLM only formats the output in natural language

The LLM is the MOUTH, not the BRAIN.
"""

import re
from dataclasses import dataclass, field
from typing import Optional


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class Measurement:
    net: str
    value: str          # "12v", "0v", "short", "open", "0.45"
    unit: str = ""      # "v", "ohm", "diode"

@dataclass
class RepairState:
    """Everything we know about the current repair session."""
    board: str = ""
    symptom: str = ""
    measurements: dict = field(default_factory=dict)   # net → value
    heated_components: list = field(default_factory=list)
    lifted_components: list = field(default_factory=list)
    confirmed_conditions: list = field(default_factory=list)
    current_step: int = 1
    diagnosis_complete: bool = False
    final_component: str = ""
    final_action: str = ""


@dataclass
class Decision:
    action: str             # "measure", "inject", "replace", "check_fuse", "ask_confirm", "done"
    target: str = ""        # net name, component ref, or question
    reasoning: str = ""     # internal reasoning (not shown to user)
    message: str = ""       # what LLM should say (template)
    is_final: bool = False


# ── Value parsing ──────────────────────────────────────────────────────────

VALUE_RE = re.compile(
    r'(\d+\.?\d*)\s*(v|volt|ohm|k|m\b)?|'
    r'\b(short(?:ed)?|open|ol\b|0v|no short|shorted)\b',
    re.IGNORECASE
)

def parse_value(text: str) -> Optional[Measurement]:
    """Parse a measurement value from user text."""
    text = text.strip().lower()

    # Explicit keywords
    if re.search(r'\bshort(?:ed)?\b', text):
        return Measurement(net="", value="short", unit="ohm")
    if re.search(r'\bopen\b|\bol\b|\bno short\b', text):
        return Measurement(net="", value="open", unit="ohm")

    # Voltage
    m = re.search(r'(\d+\.?\d*)\s*v', text)
    if m:
        v = float(m.group(1))
        return Measurement(net="", value=f"{v}v", unit="v")

    # Pure number
    m = re.search(r'^(\d+\.?\d*)$', text.strip())
    if m:
        v = float(m.group(1))
        if v > 100:
            return Measurement(net="", value=f"{v}ohm", unit="ohm")
        elif v > 5:
            return Measurement(net="", value=f"{v}v", unit="v")
        elif v < 2:
            return Measurement(net="", value=f"{v}", unit="diode")

    return None


def is_voltage_ok(value: str, expected_min: float, expected_max: float) -> bool:
    """Check if a voltage reading is within expected range."""
    m = re.search(r'(\d+\.?\d*)', value)
    if m:
        v = float(m.group(1))
        return expected_min <= v <= expected_max
    return False


def extract_measurements_from_history(history: list) -> dict:
    """
    Scan conversation history and extract all user-reported measurements.
    Returns: {net_name: value}
    """
    results = {}

    # Net patterns we care about
    NET_RE = re.compile(
        r'\b(PP[A-Z0-9_]{2,30}|LCDBKLT[A-Z0-9_]*|BL_PWR_EN|'
        r'PPVOUT_LCDBKLT|PPVIN_LCDBKLT_F|PPBUS_AON)\b',
        re.IGNORECASE
    )

    for msg in history:
        if msg.get("role") != "user":
            continue
        txt = msg.get("content", "")

        # Check if this message mentions a net AND a value
        nets = NET_RE.findall(txt.upper())
        val = parse_value(txt)

        if nets and val:
            for net in nets:
                results[net] = val.value
        elif val and not nets:
            # Value only — associate with last asked net (from assistant)
            pass

    return results


def extract_components_from_history(history: list) -> dict:
    """
    Extract component events: heating, lifting, replacing.
    Returns: {"heated": [...], "lifted": [...], "replaced": [...]}
    """
    events = {"heated": [], "lifted": [], "replaced": []}

    COMP_RE = re.compile(r'\b([CRULQFRDP][A-Z]?\d{3,4}[A-Z]?)\b')
    HEAT_RE = re.compile(r'\b(hot|heat(?:ing)?|warm|gets hot)\b', re.IGNORECASE)
    LIFT_RE = re.compile(r'\b(lift(?:ed)?|remov(?:ed)?|took off)\b', re.IGNORECASE)
    REPL_RE = re.compile(r'\b(replac(?:ed)?|swap(?:ped)?|install(?:ed)?)\b', re.IGNORECASE)

    for msg in history:
        if msg.get("role") != "user":
            continue
        txt = msg.get("content", "")
        comps = COMP_RE.findall(txt.upper())

        if comps:
            if HEAT_RE.search(txt):
                events["heated"].extend(comps)
            if LIFT_RE.search(txt):
                events["lifted"].extend(comps)
            if REPL_RE.search(txt):
                events["replaced"].extend(comps)

    return events


# ── Hard rules ─────────────────────────────────────────────────────────────

def apply_hard_rules(state: RepairState, new_message: str) -> Optional[Decision]:
    """
    Apply hard deterministic rules BEFORE consulting LLM.
    Returns a Decision if a rule fires, else None.
    """
    msg_lower = new_message.lower()

    # ── RULE 1: Component heating during injection → REPLACE IT ──────────
    heated = [c for c in state.heated_components]
    if heated and any(w in msg_lower for w in ["hot", "heat", "warm", "gets hot"]):
        comp = state.heated_components[-1] if state.heated_components else "the component"
        return Decision(
            action="replace",
            target=comp,
            reasoning=f"{comp} heated during 1V injection → confirmed shorted",
            message=f"Replace {comp} — it's the shorted component causing the issue. "
                    f"After replacing, recheck the rail to confirm the short is gone.",
            is_final=True
        )

    # ── RULE 2: Short disappears after lifting component → REPLACE IT ────
    if state.lifted_components:
        last_lifted = state.lifted_components[-1]
        if any(w in msg_lower for w in ["no short", "gone", "ok now", "normal"]):
            return Decision(
                action="replace",
                target=last_lifted,
                reasoning=f"Short disappeared after lifting {last_lifted} → component is shorted",
                message=f"Confirmed: {last_lifted} is shorted. Replace it and the backlight should work.",
                is_final=True
            )

    # ── RULE 3: Open fuse (0V with no short) → replace fuse ──────────────
    rail = "PPVIN_LCDBKLT_F"
    if (state.measurements.get(rail) in ["0v", "0", "0.0"]
            and state.measurements.get(rail + "_DIODE") in ["open", "ol"]):
        return Decision(
            action="replace",
            target="FP800",
            reasoning="0V on PPVIN_LCDBKLT_F + diode mode open → fuse FP800 is blown",
            message="FP800 fuse is blown — replace it (0603, 3A, 32V). "
                    "Check for liquid damage corrosion near FP800 before replacing.",
            is_final=False  # verify after replacement
        )

    # ── RULE 4: Rail already measured OK → don't re-measure it ───────────
    for net, val in state.measurements.items():
        if net.upper() in new_message.upper() and val not in ["0v", "short"]:
            if any(w in msg_lower for w in ["measure", "check", "what is"]):
                return Decision(
                    action="skip",
                    target=net,
                    reasoning=f"{net} already measured: {val} — skip",
                    message=f"{net} was already confirmed at {val}. Moving to the next step.",
                )

    # ── RULE 5: User confirms rail is good → move on ─────────────────────
    if any(w in msg_lower for w in ["12v", "12.0", "12.6", "13v", "3.8v", "1.8v", "5v"]):
        return Decision(
            action="next_step",
            target="",
            reasoning="Rail voltage is OK — proceed to next protocol step",
            message="",  # LLM will fill in next measurement
        )

    return None


# ── Case pattern extractor ─────────────────────────────────────────────────

def extract_case_pattern(cases: list) -> str:
    """
    Extract actionable patterns from similar forum cases.
    Returns structured summary (not raw text).
    """
    if not cases:
        return ""

    patterns = []
    for case in cases[:3]:
        text = case.get("content", case.get("solution", ""))
        if not text:
            continue

        # Extract fix/component
        fix_match = re.search(
            r'(?:replac|fix|swap|change)[a-z]*\s+([A-Z][A-Z0-9]{2,6})\b',
            text, re.IGNORECASE
        )
        # Extract rail/short
        short_match = re.search(
            r'(PP[A-Z0-9_]{3,25}|LCDBKLT[A-Z0-9_]*)\s+(?:short|0v|shorted)',
            text, re.IGNORECASE
        )

        if fix_match or short_match:
            p = []
            if short_match:
                p.append(f"rail: {short_match.group(1)} shorted")
            if fix_match:
                p.append(f"fix: replace {fix_match.group(1).upper()}")
            patterns.append(" | ".join(p))

    if not patterns:
        return ""

    return "\n[Similar cases from repair database]:\n" + "\n".join(f"  • {p}" for p in patterns)


# ── Build state from history ───────────────────────────────────────────────

def build_state_from_history(history: list, board: str, symptom: str) -> RepairState:
    """Reconstruct full repair state from conversation history."""
    state = RepairState(board=board, symptom=symptom)

    # Extract measurements
    state.measurements = extract_measurements_from_history(history)

    # Extract component events
    events = extract_components_from_history(history)
    state.heated_components = events["heated"]
    state.lifted_components = events["lifted"]

    # Check confirmations
    for msg in history:
        if msg.get("role") == "user":
            txt = msg.get("content", "").lower()
            if any(w in txt for w in ["external", "yes image", "image on external"]):
                if "external_ok" not in state.confirmed_conditions:
                    state.confirmed_conditions.append("external_ok")
            if any(w in txt for w in ["known good", "good display"]):
                if "display_tested" not in state.confirmed_conditions:
                    state.confirmed_conditions.append("display_tested")

    return state


# ── State updater ─────────────────────────────────────────────────────────

def update_state(state: RepairState, new_message: str, last_assistant_msg: str = "") -> RepairState:
    """
    Update state with the latest user message.
    Called before decision engine.
    """
    msg_lower = new_message.lower()

    # Parse measurement value
    val = parse_value(new_message)

    # Associate with the net the assistant last asked about
    if val and last_assistant_msg:
        NET_RE = re.compile(r'(PP[A-Z0-9_]{2,30}|LCDBKLT[A-Z0-9_]*|BL_PWR_EN|PPVOUT_LCDBKLT)')
        nets = NET_RE.findall(last_assistant_msg.upper())
        if nets:
            state.measurements[nets[0]] = val.value

    # Detect component events in new message
    COMP_RE = re.compile(r'([CRULQFRDP][A-Z]?\d{3,4}[A-Z]?)')
    comps = COMP_RE.findall(new_message.upper())

    if comps:
        if re.search(r'(hot|heat(?:ing)?|warm|gets hot)', new_message, re.IGNORECASE):
            for c in comps:
                if c not in state.heated_components:
                    state.heated_components.append(c)

        if re.search(r'(lift(?:ed)?|remov(?:ed)?|took off)', new_message, re.IGNORECASE):
            for c in comps:
                if c not in state.lifted_components:
                    state.lifted_components.append(c)

    # Detect confirmations
    if re.search(r'(external|image on|screen on|yes image)', msg_lower):
        if "external_ok" not in state.confirmed_conditions:
            state.confirmed_conditions.append("external_ok")

    if re.search(r'(no short|gone|normal now|ok now)', msg_lower):
        if "short_gone" not in state.confirmed_conditions:
            state.confirmed_conditions.append("short_gone")

    return state


# ── Main entry point ───────────────────────────────────────────────────────

def decide(board: str, symptom: str, history: list, new_message: str,
           cases: list = None) -> dict:
    """
    Main decision function. Called before LLM.

    Returns:
        {
          "action": "measure"|"replace"|"inject"|"done"|"llm_decide",
          "target": "NET_NAME or COMP_REF",
          "reasoning": "internal",
          "llm_instruction": "Tell the LLM exactly what to say",
          "case_patterns": "extracted patterns from similar cases",
          "state_summary": "what we know so far",
        }
    """
    # Step 1: build state from history
    state = build_state_from_history(history, board, symptom)

    # Step 2: update state with the NEW message
    last_assistant = next(
        (m["content"] for m in reversed(history) if m.get("role") == "assistant"), ""
    )
    state = update_state(state, new_message, last_assistant)

    # Step 3: apply hard rules
    decision = apply_hard_rules(state, new_message)

    # Extract case patterns for LLM context
    case_patterns = extract_case_pattern(cases or [])

    # Build state summary
    state_lines = []
    if state.measurements:
        state_lines.append("Confirmed measurements:")
        for net, val in state.measurements.items():
            state_lines.append(f"  {net} = {val}")
    if state.heated_components:
        state_lines.append(f"Heated during injection: {', '.join(state.heated_components)}")
    if state.lifted_components:
        state_lines.append(f"Lifted components: {', '.join(state.lifted_components)}")
    if state.confirmed_conditions:
        state_lines.append(f"Confirmed: {', '.join(state.confirmed_conditions)}")

    state_summary = "\n".join(state_lines)

    if decision:
        return {
            "action": decision.action,
            "target": decision.target,
            "reasoning": decision.reasoning,
            "llm_instruction": decision.message,
            "is_final": decision.is_final,
            "case_patterns": case_patterns,
            "state_summary": state_summary,
        }

    # No hard rule fired → let protocol + LLM decide
    return {
        "action": "llm_decide",
        "target": "",
        "reasoning": "No hard rule matched — use protocol",
        "llm_instruction": "",
        "is_final": False,
        "case_patterns": case_patterns,
        "state_summary": state_summary,
    }


if __name__ == "__main__":
    # Test
    history = [
        {"role": "user", "content": "no backlight water damage"},
        {"role": "assistant", "content": "Test with known good display. Done?"},
        {"role": "user", "content": "yes image on external monitor"},
        {"role": "assistant", "content": "Measure PPVIN_LCDBKLT_F"},
        {"role": "user", "content": "PPVIN_LCDBKLT_F 0v"},
        {"role": "assistant", "content": "Check diode mode. Inject 1V/3A"},
        {"role": "user", "content": "rp800 getting hot"},
    ]
    result = decide("820-02016", "no backlight", history, "rp800 getting hot")
    print("Action:", result["action"])
    print("Target:", result["target"])
    print("Reasoning:", result["reasoning"])
    print("LLM instruction:", result["llm_instruction"])
    print("Is final:", result["is_final"])
