"""Per-frontage screening logic for the dedication_screen module.

Pure functions. No external dependencies beyond models and standards.
Implements spec steps 2-6:
    Step 2: Resolve designation
    Step 3: Resolve standard dimensions
    Step 4: Resolve apparent current condition
    Step 5: Compute screening delta
    Step 6: Apply complexity flags
"""

from __future__ import annotations

from dedication_screen.models import (
    DedicationIssue,
    FrontageInput,
    FrontageResult,
    ScreeningConfidence,
    ScreeningStatus,
    ScreeningTolerances,
)
from dedication_screen.standards import (
    STANDARDS_TABLE_VERSION,
    lookup_designation_by_street_name,
    lookup_standard,
)


# -- Complexity flags that force MANUAL_REVIEW_REQUIRED -----------------------

_FORCED_MANUAL_REVIEW_FLAGS = frozenset({
    "divided_street",
    "frontage_service_road",
    "arterial_transition",
    "conflicting_designation",
    "cul_de_sac",
    "hillside_nonstandard",
})

# -- Complexity flags that cap confidence -------------------------------------

_CONFIDENCE_DOWNGRADE_FLAGS = frozenset({
    "corner_lot_frontage",
    "curved_or_meandering",
    "irregular_parcel_geometry",
})


def _min_confidence(
    a: ScreeningConfidence, b: ScreeningConfidence
) -> ScreeningConfidence:
    """Return the lower of two confidence levels."""
    order = [
        ScreeningConfidence.UNRESOLVED,
        ScreeningConfidence.LOW,
        ScreeningConfidence.MEDIUM,
        ScreeningConfidence.HIGH,
    ]
    return order[min(order.index(a), order.index(b))]


# -- Step 2: Resolve designation ----------------------------------------------


def _resolve_designation(frontage: FrontageInput, result: FrontageResult) -> None:
    """Resolve street designation for one frontage. Mutates result in place.

    Priority:
        1. user_override_designation
        2. Street name convenience lookup (capped at MEDIUM)
        3. Unresolved
    """
    if frontage.user_override_designation:
        result.designation_class = frontage.user_override_designation
        result.designation_source = "user_override"
        result.designation_confidence = ScreeningConfidence.MEDIUM
        return

    name_match = lookup_designation_by_street_name(frontage.street_name)
    if name_match is not None:
        result.designation_class = name_match
        result.designation_source = "name_lookup_v0"
        result.designation_confidence = ScreeningConfidence.MEDIUM
        return

    # Unresolved
    result.designation_class = None
    result.designation_source = "unresolved"
    result.designation_confidence = ScreeningConfidence.UNRESOLVED
    result.frontage_status = ScreeningStatus.MANUAL_REVIEW_REQUIRED
    result.issues.append(DedicationIssue(
        step="designation_lookup",
        field="designation_class",
        severity="warning",
        message=(
            f"Street designation could not be resolved for "
            f"'{frontage.street_name}' from available sources."
        ),
        action_required="Provide designation via user_override_designation or another source.",
        confidence_impact="degrades_to_unresolved",
    ))


# -- Step 3: Resolve standard dimensions --------------------------------------


def _resolve_standard_dimensions(
    frontage: FrontageInput, result: FrontageResult
) -> None:
    """Resolve standard ROW dimensions. Mutates result in place.

    user_override_standard_row_ft takes precedence over designation-derived
    standard. If override is set, designation is still stored for provenance
    but does not drive dimensions.
    """
    # Override standard takes absolute precedence
    if frontage.user_override_standard_row_ft is not None:
        result.standard_row_ft = frontage.user_override_standard_row_ft
        result.standard_half_row_ft = frontage.user_override_standard_row_ft / 2.0
        result.standard_source = "user_override"
        result.standard_is_range = False
        return

    # Designation-derived standard
    if result.designation_class is None:
        # Already MANUAL_REVIEW_REQUIRED from Step 2
        return

    entry = lookup_standard(result.designation_class)
    if entry is None:
        result.frontage_status = ScreeningStatus.MANUAL_REVIEW_REQUIRED
        result.issues.append(DedicationIssue(
            step="standards_resolution",
            field="standard_row_ft",
            severity="warning",
            message=(
                f"Standard dimensions not found for designation "
                f"'{result.designation_class}' in standards table."
            ),
            action_required="Provide standard via user_override_standard_row_ft.",
            confidence_impact="degrades_to_unresolved",
        ))
        return

    result.standard_row_ft = entry.standard_row_ft
    result.standard_half_row_ft = entry.standard_row_ft / 2.0
    result.standard_source = "standards_table"
    result.standard_is_range = entry.is_range

    if entry.is_range:
        result.frontage_confidence = _min_confidence(
            result.frontage_confidence, ScreeningConfidence.MEDIUM
        )
        result.issues.append(DedicationIssue(
            step="standards_resolution",
            field="standard_row_ft",
            severity="info",
            message=(
                f"Standard for '{result.designation_class}' is a range "
                f"({entry.range_min_ft}-{entry.range_max_ft} ft); "
                f"midpoint ({entry.standard_row_ft} ft) used for screening."
            ),
            confidence_impact="degrades_to_medium",
        ))


# -- Step 4: Resolve apparent current condition --------------------------------


def _resolve_apparent_condition(
    frontage: FrontageInput, result: FrontageResult
) -> None:
    """Resolve apparent current ROW condition. Mutates result in place."""
    if frontage.apparent_current_half_row_ft is not None:
        result.apparent_current_half_row_ft = frontage.apparent_current_half_row_ft
        result.apparent_condition_source = "user_input"
    else:
        result.apparent_condition_source = "unresolved"
        # Cannot compute delta — but don't override status if already MANUAL_REVIEW
        if result.frontage_status != ScreeningStatus.MANUAL_REVIEW_REQUIRED:
            result.frontage_status = ScreeningStatus.MANUAL_REVIEW_REQUIRED
        result.issues.append(DedicationIssue(
            step="apparent_condition",
            field="apparent_current_half_row_ft",
            severity="warning",
            message=(
                f"Apparent current half-ROW not provided for "
                f"'{frontage.street_name}'. Cannot compute dedication delta."
            ),
            action_required=(
                "Enter apparent current half-ROW from NavigateLA or field observation."
            ),
            confidence_impact="degrades_to_unresolved",
        ))


# -- Step 5: Compute screening delta ------------------------------------------


def _compute_delta(
    result: FrontageResult, tolerances: ScreeningTolerances
) -> None:
    """Compute screening delta. Mutates result in place.

    Only runs when both standard_half_row_ft and apparent_current_half_row_ft
    are available. If either is missing, result retains its current status
    (already set to MANUAL_REVIEW_REQUIRED by prior steps).
    """
    if result.standard_half_row_ft is None or result.apparent_current_half_row_ft is None:
        return

    shortfall = result.standard_half_row_ft - result.apparent_current_half_row_ft
    result.screening_shortfall_ft = shortfall

    # Base confidence from designation quality; user-reported apparent
    # condition caps at MEDIUM (not surveyed).
    base_conf = _min_confidence(
        result.designation_confidence, ScreeningConfidence.MEDIUM
    )

    if shortfall <= 0:
        result.frontage_status = ScreeningStatus.NO_APPARENT_DEDICATION
        result.estimated_dedication_depth_ft = 0.0
        result.frontage_confidence = result.designation_confidence
    elif shortfall <= tolerances.screening_tolerance_ft:
        result.frontage_status = ScreeningStatus.POSSIBLE_DEDICATION
        result.estimated_dedication_depth_ft = shortfall
        result.frontage_confidence = base_conf
    else:
        result.frontage_status = ScreeningStatus.LIKELY_DEDICATION
        result.estimated_dedication_depth_ft = shortfall
        result.frontage_confidence = base_conf

    # Area estimate
    if (
        result.estimated_dedication_depth_ft is not None
        and result.estimated_dedication_depth_ft > 0
        and result.frontage_length_ft is not None
    ):
        result.estimated_dedication_area_sf = (
            result.estimated_dedication_depth_ft * result.frontage_length_ft
        )


# -- Step 6: Apply complexity flags -------------------------------------------


def _apply_complexity_flags(
    result: FrontageResult,
    lot_type: str,
    num_frontages: int,
) -> None:
    """Apply complexity flags based on lot type and frontage conditions.

    Forced-manual-review flags override any computed status.
    Confidence-downgrade flags cap confidence at MEDIUM.
    """
    if lot_type == "corner" and num_frontages >= 2:
        result.complexity_flags.append("corner_lot_frontage")

    if lot_type == "through" and num_frontages >= 2:
        result.complexity_flags.append("through_lot_frontage")

    # Check for forced manual review
    forced = [f for f in result.complexity_flags if f in _FORCED_MANUAL_REVIEW_FLAGS]
    if forced:
        result.frontage_status = ScreeningStatus.MANUAL_REVIEW_REQUIRED
        result.frontage_confidence = _min_confidence(
            result.frontage_confidence, ScreeningConfidence.LOW
        )

    # Check for confidence downgrades
    downgrades = [f for f in result.complexity_flags if f in _CONFIDENCE_DOWNGRADE_FLAGS]
    if downgrades:
        result.frontage_confidence = _min_confidence(
            result.frontage_confidence, ScreeningConfidence.MEDIUM
        )

    # Range standard also caps at MEDIUM (already handled in Step 3 via issue,
    # but reinforce here for flag-based confidence floor)
    if result.standard_is_range:
        result.frontage_confidence = _min_confidence(
            result.frontage_confidence, ScreeningConfidence.MEDIUM
        )

    # Note-level warning when nonzero delta detected
    if (
        result.screening_shortfall_ft is not None
        and result.screening_shortfall_ft > 0
    ):
        result.issues.append(DedicationIssue(
            step="complexity_flags",
            field="screening_shortfall_ft",
            severity="info",
            message=(
                "Dedication/improvement obligations may extend beyond the "
                "roadway width comparison (e.g., sidewalk widening, parkway). "
                "Not assessed by this screening."
            ),
        ))


# -- Public entry point: screen one frontage -----------------------------------


def screen_frontage(
    frontage: FrontageInput,
    tolerances: ScreeningTolerances,
    lot_type: str,
    num_frontages: int,
) -> FrontageResult:
    """Screen one frontage for dedication risk.

    Runs steps 2-6 in order. Returns a fully populated FrontageResult.
    """
    result = FrontageResult(
        edge_id=frontage.edge_id,
        street_name=frontage.street_name,
        frontage_length_ft=frontage.frontage_length_ft,
    )

    # Step 2
    _resolve_designation(frontage, result)

    # Step 3
    _resolve_standard_dimensions(frontage, result)

    # Step 4
    _resolve_apparent_condition(frontage, result)

    # Step 5
    _compute_delta(result, tolerances)

    # Step 6
    _apply_complexity_flags(result, lot_type, num_frontages)

    return result
