"""Baseline local parking requirement calculation (Parking Step 2).

CRITICAL: Uses habitable rooms, NOT bedrooms, per LAMC 12.21 A.4(a).

Rounding convention:
  This module applies math.ceil() to the sum of residential + commercial
  parking to produce total_baseline. This is a local implementation convention
  for the baseline requirement. Reduction lanes (TOC, State DB, AB 2097) may
  apply their own rounding rules to their reduced totals.
"""

from __future__ import annotations

import math

from models.project import OccupancyArea, Project, UnitType
from parking.models import (
    BaselineParking,
    CommercialParkingLine,
    ParkingIssue,
    UnitParkingLine,
)

# LAMC 12.21 A.4(a) residential parking by habitable rooms
RESIDENTIAL_TIERS = [
    {"max_hab_rooms": 2, "rate": 1.0, "label": "Less than 3 habitable rooms"},
    {"exact_hab_rooms": 3, "rate": 1.5, "label": "3 habitable rooms"},
    {"min_hab_rooms": 4, "rate": 2.0, "label": "More than 3 habitable rooms"},
]

# Bedroom-to-habitable-rooms conversion (when only bedrooms available).
# This is an internal fallback heuristic, not a confirmed code determination.
# Results using this conversion must remain provisional.
BEDROOM_TO_HAB_ROOMS = {
    "Studio": 1,
    "0BR": 1,
    "1BR": 2,
    "2BR": 3,
    "3BR": 4,
    "4BR": 5,
}

# Commercial parking rates (LAMC 12.21 A.4).
# This is a subset of common use categories. Uses not in this table require
# manual rate determination.
COMMERCIAL_RATES: dict[str, dict] = {
    "retail": {"per_unit_sf": 1000, "ratio": 4.0},
    "office": {"per_unit_sf": 1000, "ratio": 2.0},
    "restaurant": {"per_unit_sf": 1000, "ratio": 10.0},
    "medical_office": {"per_unit_sf": 1000, "ratio": 4.0},
}


def _get_parking_rate(hab_rooms: int) -> float:
    """Look up parking rate by habitable room count per LAMC 12.21 A.4(a)."""
    if hab_rooms < 3:
        return 1.0
    elif hab_rooms == 3:
        return 1.5
    else:
        return 2.0


def _map_occupancy_to_use(occ: OccupancyArea) -> tuple[str | None, bool]:
    """Map occupancy group to commercial parking use key.

    This is a heuristic mapping based on use_description keywords and
    occupancy_group codes. It is NOT an authoritative use classification.

    Returns:
        (use_key or None, was_heuristic) — was_heuristic is True when the
        mapping was inferred from keywords/codes rather than directly supplied.
    """
    desc = occ.use_description.lower()

    # Direct keyword matches — still heuristic but higher confidence
    if "retail" in desc:
        return "retail", "retail" not in desc  # False if literal match
    if occ.occupancy_group == "M":
        return "retail", True
    if "office" in desc and "medical" not in desc:
        return "office", False
    if occ.occupancy_group == "B" and "office" not in desc:
        return "office", True  # Inferred from B occupancy
    if "restaurant" in desc or "dining" in desc:
        return "restaurant", False
    if "medical" in desc:
        return "medical_office", False

    return None, False


def _compute_residential(
    project: Project,
) -> tuple[list[UnitParkingLine], float, str, bool, list[ParkingIssue]]:
    """Compute residential parking lines.

    Returns:
        (lines, total, hab_rooms_source, used_default_assumption, issues)
    """
    issues: list[ParkingIssue] = []
    lines: list[UnitParkingLine] = []
    hab_rooms_source = "actual"
    used_default = False

    if project.unit_mix:
        for ut in project.unit_mix:
            hab = ut.habitable_rooms
            if hab == 0:
                # Attempt conversion from label/bedrooms
                converted = BEDROOM_TO_HAB_ROOMS.get(ut.label)
                if converted is None:
                    # Fallback: bedrooms + 1 (living room) — internal heuristic
                    converted = ut.bedrooms + 1
                hab = converted
                hab_rooms_source = "converted_from_bedrooms"
                issues.append(ParkingIssue(
                    step="STEP_2_baseline_parking",
                    field="habitable_rooms",
                    severity="warning",
                    message=(
                        f"Unit type '{ut.label}': habitable rooms not provided. "
                        f"Converted from bedrooms ({ut.bedrooms} BR -> {hab} hab rooms) "
                        f"using internal heuristic. This is not a confirmed code "
                        f"determination. Result is provisional."
                    ),
                    action_required="Confirm habitable room count per unit type.",
                    confidence_impact="degrades_to_provisional",
                ))

            rate = _get_parking_rate(hab)
            spaces = ut.count * rate
            lines.append(UnitParkingLine(
                unit_type=ut.label,
                count=ut.count,
                hab_rooms=hab,
                rate=rate,
                spaces=spaces,
            ))
    elif project.total_units > 0:
        # No unit mix provided. Internal fallback: assume all units have <3
        # habitable rooms (1.0 space/unit). This is a conservative internal
        # assumption, NOT a confirmed code determination. It likely understates
        # parking for projects with larger units.
        used_default = True
        issues.append(ParkingIssue(
            step="STEP_2_baseline_parking",
            field="unit_mix",
            severity="warning",
            message=(
                f"No unit mix provided. Using internal fallback assumption: "
                f"{project.total_units} units at 1.0 space/unit "
                f"(<3 habitable rooms assumed for all units). "
                f"This is not a confirmed parking determination — actual parking "
                f"depends on habitable room counts which are unknown."
            ),
            action_required="Provide unit mix with habitable room counts for accurate parking.",
            confidence_impact="degrades_to_provisional",
        ))
        lines.append(UnitParkingLine(
            unit_type="Unknown (fallback assumption)",
            count=project.total_units,
            hab_rooms=1,
            rate=1.0,
            spaces=float(project.total_units),
        ))

    total = sum(line.spaces for line in lines)
    return lines, total, hab_rooms_source, used_default, issues


def _compute_commercial(
    project: Project,
) -> tuple[list[CommercialParkingLine], float | None, str, list[ParkingIssue]]:
    """Compute commercial parking lines.

    Returns:
        (lines, total_or_None, commercial_mapping_confidence, issues)
    """
    issues: list[ParkingIssue] = []
    lines: list[CommercialParkingLine] = []
    mapping_confidence = "confirmed"
    has_unmapped = False
    unmapped_area_sf = 0.0

    for occ in project.occupancy_areas:
        if occ.occupancy_group in ("R-2", "R-1"):
            continue  # Skip residential occupancies

        use_key, was_heuristic = _map_occupancy_to_use(occ)

        if use_key and use_key in COMMERCIAL_RATES:
            rate_info = COMMERCIAL_RATES[use_key]
            spaces = (occ.area_sf / rate_info["per_unit_sf"]) * rate_info["ratio"]
            lines.append(CommercialParkingLine(
                use=occ.use_description,
                area_sf=occ.area_sf,
                rate=rate_info["ratio"],
                per_unit_sf=rate_info["per_unit_sf"],
                spaces=spaces,
            ))
            if was_heuristic:
                mapping_confidence = "provisional"
                issues.append(ParkingIssue(
                    step="STEP_2_baseline_parking",
                    field="commercial_parking",
                    severity="info",
                    message=(
                        f"Commercial use '{occ.use_description}' (occupancy {occ.occupancy_group}) "
                        f"mapped to '{use_key}' by internal heuristic. "
                        f"Verify this use classification is correct for parking."
                    ),
                    action_required=f"Confirm parking use category for '{occ.use_description}'.",
                    confidence_impact="degrades_to_provisional",
                ))
        else:
            has_unmapped = True
            unmapped_area_sf += occ.area_sf
            issues.append(ParkingIssue(
                step="STEP_2_baseline_parking",
                field="commercial_parking",
                severity="warning",
                message=(
                    f"No parking ratio for '{occ.use_description}' "
                    f"(occupancy {occ.occupancy_group}, {occ.area_sf:,.0f} sf). "
                    f"Commercial parking for this use is not included in baseline."
                ),
                action_required=f"Determine parking ratio for use '{occ.use_description}'.",
                confidence_impact="degrades_to_provisional",
            ))

    if has_unmapped:
        mapping_confidence = "provisional"

    total = sum(line.spaces for line in lines) if lines else None
    return lines, total, mapping_confidence, issues


def compute_baseline_parking(project: Project) -> BaselineParking:
    """STEP 2: Compute baseline local parking requirement.

    Residential parking based on habitable rooms per LAMC 12.21 A.4(a).
    Commercial parking computed separately per use category.

    Status is capped at provisional by any of:
      - bedroom-based habitable room conversion
      - default unit-mix fallback assumption
      - heuristic commercial use mapping
      - unmapped/unknown commercial uses
    """
    # ── Residential ─────────────────────────────────────────────────
    res_lines, res_total, hab_rooms_source, used_default, res_issues = _compute_residential(project)

    # ── Commercial ──────────────────────────────────────────────────
    com_lines, com_total, com_mapping_confidence, com_issues = _compute_commercial(project)

    # ── Combine issues ──────────────────────────────────────────────
    issues = res_issues + com_issues

    # ── Total with rounding ─────────────────────────────────────────
    # math.ceil() applied to the sum of residential + commercial.
    # This is a local implementation convention for the baseline requirement.
    total = math.ceil(res_total + (com_total or 0))

    # Guest parking: only for transient/hotel components (not standard multifamily)
    guest_rooms = None
    guest_parking = None

    # ── Status: capped by each independent confidence degrader ─────
    status = "confirmed"

    # Bedroom conversion degrades to provisional
    if hab_rooms_source == "converted_from_bedrooms":
        status = "provisional"

    # Default unit-mix fallback degrades to provisional
    if used_default:
        status = "provisional"

    # Heuristic or unmapped commercial degrades to provisional
    if com_mapping_confidence != "confirmed":
        status = "provisional"

    # Any warning/error issues cap at provisional
    if any(i.severity in ("warning", "error") for i in issues):
        status = "provisional"

    return BaselineParking(
        residential_by_unit_type=res_lines,
        residential_total=res_total,
        guest_rooms=guest_rooms,
        guest_parking=guest_parking,
        commercial_uses=com_lines if com_lines else None,
        commercial_total=com_total,
        total_baseline=total,
        hab_rooms_source=hab_rooms_source,
        used_default_unit_mix_assumption=used_default,
        commercial_mapping_confidence=com_mapping_confidence,
        total_rounding_convention="ceil_residential_plus_commercial",
        status=status,
        issues=issues,
    )
