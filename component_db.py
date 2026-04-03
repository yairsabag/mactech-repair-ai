#!/usr/bin/env python3
"""
component_db.py
===============
Maps component reference designators to descriptions.
Used to give the AI context about what each chip does.
"""

# ── Component descriptions by ref prefix ──────────────────────────────────
# Format: ref_prefix -> description
COMP_DESCRIPTIONS = {
    # Power Management
    "U5200": "ISL9240 — main charger IC, boosts PPVBUS to PPBUS_AON",
    "U5300": "ISL9240 — main charger IC",
    "U5340": "load switch / enable logic for P3V8AON_PWR_EN",
    "U7700": "SPMU — secondary PMU, controls S1 rails and backlight boost",
    "U8100": "MPMU — main PMU, controls AON/S2 rails",
    "UP800": "backlight boost IC (M1) — boosts PPVIN_LCDBKLT_F to PPVOUT_LCDBKLT (21-43V)",
    "UP801": "backlight boost IC alternate",
    "UF400": "CD3217 — USB-C PD controller, port 0",
    "UF500": "CD3217 — USB-C PD controller, port 1",
    "UF401": "CD3217 — USB-C PD controller, port 0",
    "UF501": "CD3217 — USB-C PD controller, port 1",
    "U0600": "Apple SOC (M1/M2/M3)",
    "UN000": "NAND flash storage",
    "UN100": "NAND flash storage",
    "UL000": "LPDDR memory",
    "UR630": "USB retimer",
    "UR820": "USB retimer",
    "U8470": "T2 / embedded controller",
    "UE822": "display timing controller (TCON)",
    "UD960": "display driver IC",

    # Fuses
    "FP800": "fuse — protects PPVIN_LCDBKLT_F from PPBUS_AON (3A, 32V, 0603)",
    "FF200": "fuse — PPDCIN path",
    "FF201": "fuse — PPDCIN path",

    # MOSFETs
    "QP800": "P-channel MOSFET — backlight input switch, controlled by BL_PWR_EN",
    "QP801": "P-channel MOSFET — backlight input switch (M2 Max/LUXE boards)",
    "Q5230": "N-channel MOSFET — part of PP3V8_AON regulation",

    # Resistors (key ones)
    "RP844": "current sense resistor — backlight output path (should be 0Ω)",
    "RP845": "current sense resistor — backlight output path (should be 0Ω)",
    "R5220": "current sense resistor — charger input",
    "R5221": "current sense resistor — charger input",

    # Connectors
    "JP600": "display/backlight connector (eDP/MIPI-DSI)",
    "JT400": "USB-C connector port 0",
    "JT500": "USB-C connector port 1",
    "J5150": "battery connector",
    "J6200": "keyboard/trackpad connector",
}

# ── Board-specific overrides ───────────────────────────────────────────────
BOARD_OVERRIDES = {
    "820-02652": {
        "UP800": None,  # doesn't exist on M2 Max
        "FP800": None,  # doesn't exist on M2 Max
        "QP800": None,
        "U7700": "SPMU — secondary PMU, controls LUXE backlight and S1 rails",
    },
    "820-02841": {
        "U7700": "SPMU — secondary PMU, controls LUXE backlight and S1 rails",
    },
}


def get_component_description(ref: str, board: str = None) -> str:
    """
    Get description for a component reference.
    Tries exact match first, then prefix match.
    """
    # Check board-specific overrides
    if board:
        board_num = board[:10]
        overrides = BOARD_OVERRIDES.get(board_num, {})
        if ref in overrides:
            val = overrides[ref]
            return val if val else ""  # None = component doesn't exist on this board

    # Exact match
    if ref in COMP_DESCRIPTIONS:
        return COMP_DESCRIPTIONS[ref]

    # Prefix match (e.g. UF401 matches UF400 pattern)
    for prefix, desc in COMP_DESCRIPTIONS.items():
        if ref.startswith(prefix[:-1]) and len(ref) <= len(prefix) + 1:
            return desc

    return ""


def get_components_context(refs: list, board: str = None, max_items: int = 20) -> str:
    """
    Format component descriptions for AI prompt.
    Only includes refs that have known descriptions.
    """
    lines = []
    seen = set()
    for ref in refs[:max_items]:
        if ref in seen:
            continue
        seen.add(ref)
        desc = get_component_description(ref, board)
        if desc:
            lines.append(f"  {ref}: {desc}")

    if not lines:
        return ""
    return "\n[Key components on this board]:\n" + "\n".join(lines)


def enrich_boardview_context(board: str, relevant_nets: list, boardview_data: dict) -> str:
    """
    Given a list of relevant nets, find the components connected to them
    and return their descriptions.
    """
    if not boardview_data or not relevant_nets:
        return ""

    net_set = {n.upper() for n in relevant_nets}

    # Find components connected to these nets
    relevant_refs = []
    for comp in boardview_data.get("components", []):
        comp_nets = {n.upper() for n in comp.get("nets", [])}
        if comp_nets & net_set:
            relevant_refs.append(comp["ref"])

    return get_components_context(relevant_refs, board)


if __name__ == "__main__":
    # Test
    test_refs = ["UP800", "UF400", "FP800", "QP800", "U8100", "U7700", "U0600"]
    print("=== 820-02016 ===")
    print(get_components_context(test_refs, "820-02016"))
    print("\n=== 820-02652 (M2 Max) ===")
    print(get_components_context(test_refs, "820-02652"))
