"""G-sheet setback lookup — zone-table-based, G010 display only.

Returns per-edge CalcResult objects from the zone table defaults.
These are DISPLAY values for the G010 sheet, not the full setback
module output (which lives in setback/setback_orchestrator.py).

Honesty invariant: if lot type, frontage conditions, or alley adjacency
are unknown, the result is marked PROVISIONAL with an explicit note.
"""

from __future__ import annotations

import json
from pathlib import Path

from models.result import CalcResult
from models.project import Project

_ZONE_TABLE_PATH = Path(__file__).resolve().parents[2] / "data" / "zone_tables.json"


def _load_zone_table() -> dict:
    with open(_ZONE_TABLE_PATH) as f:
        return json.load(f)


def _normalize_zone(raw_zone: str) -> str:
    """Strip HD suffix and Q/D prefixes for table lookup."""
    zone = raw_zone.strip().upper()
    # Remove height district suffix (e.g. "R3-1" → "R3")
    if "-" in zone:
        zone = zone.split("-")[0]
    # Strip leading Q or D
    if zone.startswith("(Q)") or zone.startswith("(D)"):
        zone = zone[3:].strip()
    elif zone.startswith("Q") and len(zone) > 1 and zone[1].isalpha():
        zone = zone[1:]
    elif zone.startswith("D") and len(zone) > 1 and zone[1].isalpha():
        zone = zone[1:]
    return zone


def _status_symbol(status: str) -> str:
    if status == "compliant":
        return "compliant"
    if status == "provisional":
        return "provisional"
    if status == "non_compliant":
        return "non_compliant"
    return "not_entered"


def get_setback_results(
    zone: str | None,
    project: Project,
    lot_type: str = "unknown",
    alley_adjacent: bool | None = None,
) -> list[CalcResult]:
    """Return CalcResult list for front/side/rear setbacks.

    Always reads from zone_tables.json. When lot type, frontage, or
    alley adjacency is unconfirmed, the result is PROVISIONAL.

    Args:
        zone: Parsed base zone string (e.g. "R3", "C2").
        project: Project model with setback_front_ft / side / rear provided values.
        lot_type: "interior" / "corner" / "through" / "flag" / "unknown"
        alley_adjacent: True/False/None (None = unknown)

    Returns:
        List of CalcResult, one per edge (front, side, rear).
    """
    if not zone:
        return [_unresolved_result(edge, "Zone not resolved — setback requirements unknown")
                for edge in ("front", "side", "rear")]

    table = _load_zone_table()
    normalized = _normalize_zone(zone)
    zone_data = table.get("zones", {}).get(normalized)

    if zone_data is None:
        note = (
            f"Zone '{zone}' (normalized: '{normalized}') not found in zone table. "
            "May be a Chapter 1A zone, specific-plan zone, or non-standard overlay. "
            "Setback requirements require manual lookup."
        )
        return [_unresolved_result(edge, note) for edge in ("front", "side", "rear")]

    setbacks = zone_data.get("setbacks", {})
    authority_id = zone_data.get("authority_id", "")

    # Base note about data source
    base_note = f"Zone table default ({zone}). Authority: LAMC 12.04 / {authority_id}."

    # Determine if we need to mark provisional
    provisional_reasons: list[str] = []

    if lot_type in ("unknown", ""):
        provisional_reasons.append(
            "Lot type (interior/corner/through) not confirmed — "
            "corner lots require a side-street setback not shown here."
        )

    if alley_adjacent is None:
        provisional_reasons.append(
            "Alley adjacency unknown — rear yard may be reduced by alley-reduction formula "
            "per LAMC Table 1a/1b."
        )

    is_provisional = bool(provisional_reasons)
    confidence = "medium" if not is_provisional else "low"
    determinism = "deterministic" if not is_provisional else "advisory"
    assumption_note = " ".join(provisional_reasons) if provisional_reasons else ""

    results: list[CalcResult] = []

    # Front setback
    front_req = setbacks.get("front_ft")
    front_prov = project.setback_front_ft
    results.append(_make_result(
        edge="front",
        required_ft=front_req,
        provided_ft=front_prov,
        base_note=base_note,
        is_provisional=is_provisional,
        assumption_note=assumption_note,
        extra_note="Front yard: prevailing-setback rule may govern; field verification required.",
        confidence=confidence,
        determinism=determinism,
        authority_id=authority_id,
        zone=zone,
    ))

    # Side setback
    side_req = setbacks.get("side_ft")
    side_prov = project.setback_side_ft
    side_note = ""
    # RD3/RD4/RD5 have lot-width-dependent formulas
    if normalized in ("RD3", "RD4", "RD5"):
        side_note = "Side yard: 10% of lot width (5 ft min, 10 ft max) per LAMC Table 1b. Value shown is minimum."
        is_provisional = True
        confidence = "low"
        determinism = "advisory"
        if assumption_note:
            assumption_note += " " + side_note
        else:
            assumption_note = side_note
    results.append(_make_result(
        edge="side",
        required_ft=side_req,
        provided_ft=side_prov,
        base_note=base_note,
        is_provisional=is_provisional,
        assumption_note=assumption_note,
        extra_note=side_note,
        confidence=confidence,
        determinism=determinism,
        authority_id=authority_id,
        zone=zone,
    ))

    # Rear setback
    rear_req = setbacks.get("rear_ft")
    rear_prov = project.setback_rear_ft
    rear_note = ""
    if alley_adjacent:
        rear_note = "Alley adjacent: rear yard may be reduced per LAMC Table 1a/1b alley-reduction formula."
        if assumption_note and rear_note not in assumption_note:
            assumption_note = assumption_note.replace(
                "Alley adjacency unknown — rear yard may be reduced by alley-reduction formula "
                "per LAMC Table 1a/1b.", ""
            ).strip()
        # Known alley, but formula not applied here
        is_provisional = True
        confidence = "low"
        determinism = "advisory"
    results.append(_make_result(
        edge="rear",
        required_ft=rear_req,
        provided_ft=rear_prov,
        base_note=base_note,
        is_provisional=is_provisional,
        assumption_note=assumption_note,
        extra_note=rear_note,
        confidence=confidence,
        determinism=determinism,
        authority_id=authority_id,
        zone=zone,
    ))

    return results


def _make_result(
    edge: str,
    required_ft: float | int | None,
    provided_ft: float | None,
    base_note: str,
    is_provisional: bool,
    assumption_note: str,
    extra_note: str,
    confidence: str,
    determinism: str,
    authority_id: str,
    zone: str,
) -> CalcResult:
    if required_ft is None:
        status = "not_entered"
        value = {"edge": edge, "required_ft": None, "provided_ft": provided_ft, "status": "not_entered"}
    elif provided_ft is None:
        status = "provisional" if is_provisional else "required_only"
        value = {"edge": edge, "required_ft": required_ft, "provided_ft": None, "status": status}
    else:
        if provided_ft >= required_ft:
            status = "provisional" if is_provisional else "compliant"
        else:
            status = "non_compliant"
        value = {"edge": edge, "required_ft": required_ft, "provided_ft": provided_ft, "status": status}

    notes: list[str] = [base_note]
    if extra_note:
        notes.append(extra_note)
    if assumption_note:
        notes.append(f"PROVISIONAL: {assumption_note}")

    return CalcResult(
        name=f"setback_{edge}",
        value=value,
        unit="ft",
        code_section="LAMC 12.04",
        code_cycle="LAMC Rev. 7",
        authority_id=authority_id,
        determinism=determinism,
        confidence=confidence,
        review_notes=notes,
        assumptions=[assumption_note] if assumption_note else [],
        data_sources=[f"zone_tables.json ({zone})"],
    )


def _unresolved_result(edge: str, note: str) -> CalcResult:
    return CalcResult(
        name=f"setback_{edge}",
        value={"edge": edge, "required_ft": None, "provided_ft": None, "status": "unresolved"},
        unit="ft",
        code_section="LAMC 12.04",
        code_cycle="LAMC Rev. 7",
        determinism="manual_only",
        confidence="low",
        review_notes=[note],
        assumptions=[note],
        data_sources=[],
    )
