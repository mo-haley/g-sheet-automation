"""AB 2097 eligibility check (Parking Step 1.5).

Checks whether AB 2097 restricts the city's ability to impose automobile
parking minimums for this project at this location.

AB 2097 is a state preemption that applies when:
  1. The parcel is within 1/2 mile of a qualifying "major transit stop"
     (Public Resources Code 21155(b))
  2. The project use is not excluded (certain transient lodging / hotel uses
     may have different treatment depending on statute version and filing date)

This module returns a conservative eligibility assessment. `confirmed` is
reserved for cases where BOTH transit qualification AND project-use exclusion
checks are affirmatively cleared. Transit proximity alone supports `provisional`
at best.
"""

from __future__ import annotations

from models.project import Project
from models.site import Site
from parking.models import AB2097Result, ParkingIssue

# 1/2 mile in feet
HALF_MILE_FT = 2640.0

# Occupancy groups / use descriptions that may trigger AB 2097 use exclusions
# or require separate treatment. This is a conservative screen, not exhaustive.
_TRANSIENT_OCCUPANCY_GROUPS = {"R-1"}
_TRANSIENT_USE_KEYWORDS = {"hotel", "motel", "transient", "lodging", "hostel", "inn"}


def _check_project_use_exclusions(
    project: Project | None,
) -> tuple[bool, bool | None, list[ParkingIssue]]:
    """Check whether project uses may exclude or restrict AB 2097 applicability.

    Returns:
        (exclusions_checked, exclusion_found_or_None, issues)

    exclusion_found:
        True  = disqualifying or restricting use detected
        False = checked, no exclusion found
        None  = could not check (no project data)
    """
    issues: list[ParkingIssue] = []

    if project is None:
        issues.append(ParkingIssue(
            step="STEP_1.5_ab2097",
            field="project_use_exclusions",
            severity="info",
            message="No project data available for AB 2097 use-exclusion screening.",
            action_required="Provide project data to screen for AB 2097 use exclusions.",
            confidence_impact="degrades_to_provisional",
        ))
        return False, None, issues

    # Screen occupancy areas for transient lodging / hotel uses
    for occ in project.occupancy_areas:
        if occ.occupancy_group in _TRANSIENT_OCCUPANCY_GROUPS:
            issues.append(ParkingIssue(
                step="STEP_1.5_ab2097",
                field="project_use_exclusions",
                severity="warning",
                message=(
                    f"Occupancy group '{occ.occupancy_group}' "
                    f"('{occ.use_description}', {occ.area_sf:,.0f} sf) "
                    f"is a transient occupancy use. AB 2097 applicability may be "
                    f"restricted or require separate analysis for this component."
                ),
                action_required="Verify AB 2097 applicability for transient occupancy components.",
                confidence_impact="degrades_to_provisional",
            ))
            return True, True, issues

        desc_lower = occ.use_description.lower()
        if any(kw in desc_lower for kw in _TRANSIENT_USE_KEYWORDS):
            issues.append(ParkingIssue(
                step="STEP_1.5_ab2097",
                field="project_use_exclusions",
                severity="warning",
                message=(
                    f"Use description '{occ.use_description}' contains transient "
                    f"lodging keywords. AB 2097 applicability may be restricted."
                ),
                action_required="Verify AB 2097 applicability for transient/lodging components.",
                confidence_impact="degrades_to_provisional",
            ))
            return True, True, issues

    # No disqualifying uses found in available data.
    # Note: this screen is limited to occupancy_areas; it cannot catch
    # every possible exclusion. Result is best-effort, not exhaustive.
    return True, False, issues


def _assess_transit_eligibility(
    site: Site,
) -> tuple[bool | None, str | None, float | None, str, list[ParkingIssue]]:
    """Assess transit-side AB 2097 eligibility from site data.

    Returns:
        (eligible_or_None, transit_type, distance, transit_confidence, issues)

    transit_confidence here reflects only the transit leg — NOT final AB 2097
    confidence (which also depends on project-use exclusions).
    """
    issues: list[ParkingIssue] = []

    # ── ZIMAS AB 2097 area flag ─────────────────────────────────────
    if site.ab2097_area is True:
        transit_type = site.transit_stop_type
        distance = site.nearest_transit_stop_distance_ft

        if transit_type in ("rail", "brt"):
            # Strong transit indicator — but this is mapped data, not field-verified.
            issues.append(ParkingIssue(
                step="STEP_1.5_ab2097",
                field="transit_eligibility",
                severity="info",
                message=(
                    f"ZIMAS flags parcel in AB 2097 area with {transit_type} transit stop. "
                    f"Mapped eligibility is a strong indicator but does not constitute "
                    f"final legal confirmation of AB 2097 applicability."
                ),
                confidence_impact="none",
            ))
            return True, transit_type, distance, "provisional", issues

        elif transit_type == "bus_intersection":
            issues.append(ParkingIssue(
                step="STEP_1.5_ab2097",
                field="transit_type",
                severity="warning",
                message=(
                    "ZIMAS flags AB 2097 area based on bus intersection. "
                    "Bus headway data may change; manual verification required."
                ),
                action_required="Verify qualifying bus routes maintain <=15 min peak headways.",
                confidence_impact="degrades_to_provisional",
            ))
            return True, "bus_intersection", distance, "provisional", issues

        else:
            issues.append(ParkingIssue(
                step="STEP_1.5_ab2097",
                field="transit_type",
                severity="warning",
                message="ZIMAS flags AB 2097 area but transit stop type not determined.",
                action_required="Identify qualifying transit stop type (rail, BRT, or bus intersection).",
                confidence_impact="degrades_to_provisional",
            ))
            return True, None, distance, "provisional", issues

    elif site.ab2097_area is False:
        # ZIMAS explicitly says not in AB 2097 area — strong negative indicator.
        return False, "none", None, "provisional", issues

    # ── No ZIMAS flag — check proximity data ────────────────────────
    if site.nearest_transit_stop_distance_ft is not None:
        within_half_mile = site.nearest_transit_stop_distance_ft <= HALF_MILE_FT
        if within_half_mile:
            transit_type = site.transit_stop_type
            if transit_type in ("rail", "brt"):
                issues.append(ParkingIssue(
                    step="STEP_1.5_ab2097",
                    field="transit_eligibility",
                    severity="info",
                    message=(
                        f"Parcel within {site.nearest_transit_stop_distance_ft:,.0f} ft "
                        f"of {transit_type} stop (< {HALF_MILE_FT:,.0f} ft threshold). "
                        f"AB 2097 transit leg appears satisfied."
                    ),
                    confidence_impact="none",
                ))
                return True, transit_type, site.nearest_transit_stop_distance_ft, "provisional", issues
            elif transit_type == "bus_intersection":
                issues.append(ParkingIssue(
                    step="STEP_1.5_ab2097",
                    field="transit_type",
                    severity="warning",
                    message="Potential AB 2097 eligibility via bus intersection. Manual headway verification required.",
                    action_required="Verify qualifying bus routes maintain <=15 min peak headways.",
                    confidence_impact="degrades_to_provisional",
                ))
                return True, "bus_intersection", site.nearest_transit_stop_distance_ft, "provisional", issues
            else:
                issues.append(ParkingIssue(
                    step="STEP_1.5_ab2097",
                    field="transit_type",
                    severity="info",
                    message="Parcel within 1/2 mile of transit but stop type not classified.",
                    action_required="Classify nearest transit stop for AB 2097 eligibility.",
                    confidence_impact="degrades_to_provisional",
                ))
                return None, None, site.nearest_transit_stop_distance_ft, "provisional", issues
        else:
            return False, "none", site.nearest_transit_stop_distance_ft, "provisional", issues

    # ── No transit data at all ──────────────────────────────────────
    issues.append(ParkingIssue(
        step="STEP_1.5_ab2097",
        field="ab2097_eligible",
        severity="info",
        message="No AB 2097 or transit proximity data available. Cannot determine eligibility.",
        action_required="Check ZIMAS AB 2097 layer or measure distance to nearest major transit stop.",
        confidence_impact="degrades_to_unresolved",
    ))
    return None, None, None, "unresolved", issues


def check_ab2097(site: Site, project: Project | None = None) -> AB2097Result:
    """STEP 1.5: AB 2097 threshold gate.

    Assesses both transit-side eligibility and project-use exclusions.
    `confirmed` requires both legs to be affirmatively cleared.
    Transit proximity alone supports `provisional` at best.

    Args:
        site: Parsed site data with transit proximity / ZIMAS flags.
        project: Project data for use-exclusion screening. Optional; if not
                 provided, use exclusions cannot be checked and confidence
                 is capped at provisional.
    """
    # ── Transit leg ─────────────────────────────────────────────────
    transit_eligible, transit_type, distance, transit_confidence, transit_issues = (
        _assess_transit_eligibility(site)
    )

    # ── Project-use exclusion leg ───────────────────────────────────
    exclusions_checked, exclusion_found, exclusion_issues = (
        _check_project_use_exclusions(project)
    )

    all_issues = transit_issues + exclusion_issues

    # ── Combine into final result ───────────────────────────────────
    # Not eligible on transit side — return early
    if transit_eligible is False:
        return AB2097Result(
            eligible=False,
            transit_type=transit_type,
            distance_to_stop=distance,
            project_use_exclusions_checked=exclusions_checked,
            project_use_exclusion_found=exclusion_found,
            confidence="provisional",
            issues=all_issues,
        )

    # Transit unresolved
    if transit_eligible is None:
        return AB2097Result(
            eligible=None,
            transit_type=transit_type,
            distance_to_stop=distance,
            project_use_exclusions_checked=exclusions_checked,
            project_use_exclusion_found=exclusion_found,
            confidence=transit_confidence,
            issues=all_issues,
        )

    # Transit eligible — now factor in use exclusions
    if exclusion_found is True:
        # Disqualifying use detected — AB 2097 may not apply cleanly
        all_issues.append(ParkingIssue(
            step="STEP_1.5_ab2097",
            field="ab2097_eligible",
            severity="warning",
            message=(
                "AB 2097 transit eligibility appears met, but project contains "
                "use types that may restrict or exclude AB 2097 applicability. "
                "Final applicability should be confirmed with project counsel."
            ),
            action_required="Confirm AB 2097 applicability given project use mix.",
            confidence_impact="degrades_to_provisional",
        ))
        return AB2097Result(
            eligible=None,  # Cannot confirm eligible when exclusion exists
            transit_type=transit_type,
            distance_to_stop=distance,
            project_use_exclusions_checked=True,
            project_use_exclusion_found=True,
            max_parking_if_eligible="per_AB_2097_at_filing",
            confidence="provisional",
            issues=all_issues,
        )

    if not exclusions_checked:
        # Transit eligible but exclusions not checked — provisional
        all_issues.append(ParkingIssue(
            step="STEP_1.5_ab2097",
            field="ab2097_eligible",
            severity="info",
            message=(
                "AB 2097 transit eligibility appears met. Project-use exclusion "
                "screening not performed (no project data). Final applicability "
                "should be confirmed at filing."
            ),
            action_required="Provide project data to complete AB 2097 use-exclusion screening.",
            confidence_impact="degrades_to_provisional",
        ))
        return AB2097Result(
            eligible=True,
            transit_type=transit_type,
            distance_to_stop=distance,
            project_use_exclusions_checked=False,
            project_use_exclusion_found=None,
            max_parking_if_eligible="per_AB_2097_at_filing",
            confidence="provisional",
            issues=all_issues,
        )

    # Transit eligible AND exclusions checked with none found.
    # This is the strongest result this module can produce. However,
    # mapped/ZIMAS eligibility is not field-verified, and there may be
    # exclusion conditions beyond what this module screens. We use
    # `provisional` as the ceiling for this module's own assessment.
    # A downstream process or explicit user confirmation can upgrade
    # to confirmed if appropriate.
    all_issues.append(ParkingIssue(
        step="STEP_1.5_ab2097",
        field="ab2097_eligible",
        severity="info",
        message=(
            "AB 2097 appears applicable: transit eligibility met and no "
            "disqualifying project uses detected in available data. "
            "Final applicability should be confirmed at filing. "
            "This module's assessment is provisional — mapped transit data "
            "and limited use-exclusion screening do not constitute final "
            "legal confirmation."
        ),
        confidence_impact="none",
    ))
    return AB2097Result(
        eligible=True,
        transit_type=transit_type,
        distance_to_stop=distance,
        project_use_exclusions_checked=True,
        project_use_exclusion_found=False,
        max_parking_if_eligible="per_AB_2097_at_filing",
        confidence="provisional",
        issues=all_issues,
    )
