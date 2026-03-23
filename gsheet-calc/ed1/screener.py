"""Pure deterministic ED1 screening logic.

No external API calls.  No live legal research.  Every unknown input is
surfaced as a missing_input rather than silently inferred.

Source: Mayor Bass Executive Directive No. 1 (3rd Revised, July 1, 2024).
"""

from __future__ import annotations

from ed1.models import (
    ED1BaselineComparison,
    ED1Confidence,
    ED1Input,
    ED1Result,
    ED1Status,
    EnvironmentalSiteStatus,
    HistoricResourceStatus,
)


# ── Section helpers ──────────────────────────────────────────────────────────
# Each helper mutates the shared lists (blockers, warnings, etc.) and returns
# nothing.  This keeps the top-level function readable.


def _check_affordability(inp: ED1Input, blockers: list[str], missing: list[str]) -> None:
    """§1 preamble — must be 100% affordable."""
    if inp.is_100_percent_affordable is None:
        missing.append(
            "is_100_percent_affordable: Cannot determine ED1 eligibility "
            "without confirming the project is 100% affordable."
        )
    elif not inp.is_100_percent_affordable:
        blockers.append(
            "Project is not 100% affordable. ED1 applies only to 100% "
            "affordable housing projects (or Shelter per LAMC 12.03)."
        )


def _check_discretionary_triggers(
    inp: ED1Input, blockers: list[str], missing: list[str],
) -> None:
    """§1 preamble — no zoning change, variance, or GPA."""
    if inp.requires_zone_change is None:
        missing.append(
            "requires_zone_change: Unknown whether the project requires a "
            "zoning change. ED1 is unavailable if a zoning change is needed."
        )
    elif inp.requires_zone_change:
        blockers.append(
            "Project requires a zoning change. ED1 exemption does not apply "
            "when a zoning change is required."
        )

    if inp.requires_variance is None:
        missing.append(
            "requires_variance: Unknown whether the project requires a "
            "variance. ED1 is unavailable if a variance is needed."
        )
    elif inp.requires_variance:
        blockers.append(
            "Project requires a variance. ED1 exemption does not apply "
            "when a variance is required."
        )

    if inp.requires_general_plan_amendment is None:
        missing.append(
            "requires_general_plan_amendment: Unknown whether the project "
            "requires a General Plan amendment. ED1 is unavailable if a GPA "
            "is needed."
        )
    elif inp.requires_general_plan_amendment:
        blockers.append(
            "Project requires a General Plan amendment. ED1 exemption does "
            "not apply when a GPA is required."
        )


def _check_zone_classification(
    inp: ED1Input, blockers: list[str], missing: list[str],
) -> None:
    """§1 preamble + §1.A — zone family restrictions."""
    if inp.zoning_is_single_family_or_more_restrictive is None:
        missing.append(
            "zoning_is_single_family_or_more_restrictive: Cannot confirm "
            "whether the site is in a single-family or more restrictive zone. "
            "ED1 is unavailable in such zones."
        )
    elif inp.zoning_is_single_family_or_more_restrictive:
        blockers.append(
            "Site is in a single-family or more restrictive zone. ED1 "
            "projects are prohibited in these zones."
        )

    if inp.manufacturing_zone_disallows_multifamily is None:
        missing.append(
            "manufacturing_zone_disallows_multifamily: Unknown whether "
            "the site includes parcels in a manufacturing zone that does "
            "not allow multifamily residential uses."
        )
    elif inp.manufacturing_zone_disallows_multifamily:
        blockers.append(
            "Site includes parcels in a manufacturing zone that does not "
            "allow multifamily residential uses (§1.A)."
        )


def _check_environmental(
    inp: ED1Input,
    blockers: list[str],
    warnings: list[str],
    missing: list[str],
) -> None:
    """§1.B — hazardous waste; §1.C — former gas/oil well."""
    # Hazardous waste (§1.B)
    if inp.hazardous_site_status is None or inp.hazardous_site_status == EnvironmentalSiteStatus.UNKNOWN:
        missing.append(
            "hazardous_site_status: Unknown whether the site is a listed "
            "hazardous waste site. If listed and not cleared for residential "
            "use, ED1 eligibility is blocked (§1.B)."
        )
    elif inp.hazardous_site_status == EnvironmentalSiteStatus.PRESENT_NOT_CLEARED:
        blockers.append(
            "Site is a listed hazardous waste site and has not been cleared "
            "by the applicable regulatory authority for residential or "
            "residential mixed use (§1.B)."
        )
    elif inp.hazardous_site_status == EnvironmentalSiteStatus.CLEARED:
        warnings.append(
            "Site was identified as a hazardous waste site but has been "
            "cleared by regulatory authority for residential use. Confirm "
            "clearance documentation is current and on file (§1.B)."
        )
    # NOT_PRESENT → no action needed

    # Former gas/oil well (§1.C)
    if inp.oil_well_site_status is None or inp.oil_well_site_status == EnvironmentalSiteStatus.UNKNOWN:
        missing.append(
            "oil_well_site_status: Unknown whether the site was previously "
            "used as a gas or oil well. If present and not cleared with a "
            "'No Further Action' letter, ED1 approval is blocked (§1.C)."
        )
    elif inp.oil_well_site_status == EnvironmentalSiteStatus.PRESENT_NOT_CLEARED:
        blockers.append(
            "Site is or was previously used as a gas or oil well and has "
            "not received required Phase I/II environmental assessment and "
            "'No Further Action' letter or comparable documentation (§1.C)."
        )
    elif inp.oil_well_site_status == EnvironmentalSiteStatus.CLEARED:
        warnings.append(
            "Site has former gas/oil well history but environmental "
            "clearance has been obtained. Confirm 'No Further Action' "
            "letter or comparable documentation is on file (§1.C)."
        )


def _check_fire_hillside(
    inp: ED1Input,
    blockers: list[str],
    missing: list[str],
) -> None:
    """§1.D — VHFHSZ *portion of* the Hillside Area Map.

    Important: The blocker is the intersection of VHFHSZ and Hillside,
    not VHFHSZ alone.
    """
    vhfhsz = inp.vhfhsz_flag
    hillside = inp.hillside_area_flag

    if vhfhsz is None:
        missing.append(
            "vhfhsz_flag: Unknown whether the site is in a Very High Fire "
            "Hazard Severity Zone. Needed to evaluate §1.D."
        )
    if hillside is None:
        missing.append(
            "hillside_area_flag: Unknown whether the site is in the "
            "Hillside Area. Needed to evaluate §1.D."
        )

    if vhfhsz is True and hillside is True:
        blockers.append(
            "Site is in the Very High Fire Hazard Severity Zone portion of "
            "the Hillside Area. ED1 projects are prohibited on these "
            "parcels (§1.D, per Council File 09-1390)."
        )


def _check_historic(
    inp: ED1Input,
    blockers: list[str],
    warnings: list[str],
    missing: list[str],
) -> None:
    """§1.E — historic resource / district / HPOZ / HCM / named plan areas."""
    status = inp.historic_resource_status

    if status is None or status == HistoricResourceStatus.UNKNOWN:
        missing.append(
            "historic_resource_status: Unknown whether the site includes "
            "parcels on the National Register, California Register, within "
            "an HPOZ, or designated as a City Historic-Cultural Monument. "
            "Any of these would block ED1 eligibility (§1.E)."
        )
    elif status == HistoricResourceStatus.DESIGNATED_OR_LISTED:
        blockers.append(
            "Site includes a designated or listed historic resource "
            "(National Register, California Register, HPOZ, or City HCM). "
            "ED1 projects are prohibited on these parcels (§1.E)."
        )

    # Named plan-area / CPIO historic checks
    if inp.protected_plan_area_historic_check_complete is None:
        missing.append(
            "protected_plan_area_historic_check_complete: The memo also "
            "excludes eligible historic or architectural resources in "
            "Westwood Village SP, Central City West SP, Echo Park CDO, "
            "North University Park SP, and eligible historic resources in "
            "South LA CPIO §1-6.C.5.b, Southeast LA CPIO §1-6.C.5.b, "
            "West Adams CPIO §6.C.5.b, and San Pedro CPIO §7.C.5.b. "
            "Confirm whether this site falls within any of these areas and "
            "if so, whether eligible resources are present (§1.E)."
        )
    elif not inp.protected_plan_area_historic_check_complete:
        warnings.append(
            "Protected plan area / CPIO historic resource check has not "
            "been completed. If the site is in one of the memo-listed plan "
            "areas or CPIOs, eligible historic resources would block ED1 (§1.E)."
        )


def _check_rso(
    inp: ED1Input,
    blockers: list[str],
    warnings: list[str],
    obligations: list[str],
    missing: list[str],
) -> None:
    """§1.L — RSO hard blocker; §1.K, M, N, O — obligations."""
    if inp.rso_subject_site is None:
        missing.append(
            "rso_subject_site: Unknown whether the site is subject to the "
            "Rent Stabilization Ordinance (RSO). If RSO applies and the "
            "site has 12+ total units occupied or occupied in the prior 5 "
            "years, ED1 is unavailable (§1.L)."
        )
    elif inp.rso_subject_site:
        # Check the hard blocker (§1.L)
        units = inp.rso_total_units
        occupied = inp.occupied_units_within_5_years

        if units is None:
            missing.append(
                "rso_total_units: Site is RSO-subject but total unit count "
                "is unknown. If 12 or more units are or were occupied in "
                "the prior 5 years, ED1 is blocked (§1.L)."
            )
        elif units >= 12:
            if occupied is None:
                missing.append(
                    "occupied_units_within_5_years: RSO site has 12+ total "
                    "units but occupancy within prior 5 years is unconfirmed. "
                    "If occupied or recently occupied, ED1 is blocked (§1.L)."
                )
            elif occupied:
                blockers.append(
                    "Site is subject to RSO with 12 or more total units that "
                    "are or were occupied in the five-year period preceding "
                    "the application. ED1 is unavailable (§1.L)."
                )

        # RSO obligations apply regardless of unit count (§1.M, N, O)
        obligations.append(
            "RSO replacement: All existing RSO units and RSO units "
            "demolished on or after January 1, 2020 must be replaced per "
            "California Government Code §65915(c)(3) (§1.M)."
        )
        obligations.append(
            "RSO replacement income rules: If occupant income is unknown "
            "or above lower income, replacement per §65915(c)(3)(C)(i). "
            "If lower-income households exercise right to return, "
            "replacement with covenant-based affordability (§1.M)."
        )
        obligations.append(
            "Right of first refusal: Returning tenants must be offered a "
            "comparable affordable replacement unit at initial rent no "
            "higher than most recent lawful RSO rent (or affordable rent "
            "if lower). Subsequent increases capped at RSO allowable rate. "
            "Limitation included in recorded covenant (§1.N)."
        )
        obligations.append(
            "Security deposit: Returning tenants may not be charged more "
            "than 50% of initial monthly rent as security deposit, with "
            "up to 90 days to pay after move-in (§1.O)."
        )

    if inp.replacement_unit_trigger is True:
        warnings.append(
            "Replacement unit obligations may apply. Confirm scope of "
            "replacement requirements per §1.M and Gov Code §65915(c)(3)."
        )


def _check_residential_capacity(
    inp: ED1Input, blockers: list[str], missing: list[str],
) -> None:
    """§1.I — residential-zoned sites must allow 5+ units pre-bonus."""
    if inp.is_residential_zone is None:
        # Can't evaluate this gate without knowing zone classification
        return

    if not inp.is_residential_zone:
        # Gate only applies to residential-zoned sites
        return

    if inp.residential_pre_bonus_allowed_units is None:
        missing.append(
            "residential_pre_bonus_allowed_units: Site has a residential "
            "zoning classification but pre-bonus unit capacity is unknown. "
            "ED1 requires the zoning to permit at least 5 units (rounded up) "
            "prior to any density bonus (§1.I)."
        )
    elif inp.residential_pre_bonus_allowed_units < 5:
        blockers.append(
            f"Residential-zoned site permits only "
            f"{inp.residential_pre_bonus_allowed_units} units pre-bonus. "
            f"ED1 requires at least 5 units (rounded up) prior to any "
            f"density bonus (§1.I)."
        )


def _add_design_warnings(
    inp: ED1Input, warnings: list[str],
) -> None:
    """§1.F, G, H — design/form conditions.

    These are required project features, surfaced as warnings so the
    design team can confirm compliance.  They are not eligibility
    blockers at screening stage.
    """
    warnings.append(
        "Parking screening required: All at-grade or above-grade parking "
        "must be screened with active uses or visually opaque materials "
        "along all facades visible from public rights-of-way, excluding "
        "driveway/garage entrances (§1.F)."
    )
    warnings.append(
        "Pedestrian entrance required: Any building fronting a public "
        "street must have at least one pedestrian entrance facing that "
        "street, with pedestrian access provided (§1.G)."
    )
    warnings.append(
        "Glazing minimums: All floors above ground floor require at least "
        "20% facade glazing. Ground floor facades in commercial zones "
        "fronting the primary street require at least 30% glazing (§1.H)."
    )


def _add_incentive_constraints(
    inp: ED1Input, constraints: list[str],
) -> None:
    """§1.J — incentive/waiver caps and limits.

    These are design constraints, not eligibility gates.  Presented so
    the architect/developer can evaluate the ED1 envelope.
    """
    constraints.append(
        "Max incentives/waivers: Up to 5 incentives and 1 waiver under "
        "LAMC §12.22A.25 (§1.J)."
    )

    # Residential land use designation limits (§1.J.1, J.3)
    if inp.is_residential_land_use_designation is not False:
        constraints.append(
            "FAR cap (residential land use): Max 100% increase in floor "
            "area, or up to FAR 3.5:1, whichever is greater (§1.J.1)."
        )
        constraints.append(
            "Yard minimums (residential land use): Side yard min 5 ft, "
            "rear yard min 8 ft. Front yard reduction limited to average "
            "of adjoining buildings; corner/vacant lot exceptions may "
            "apply. All yard adjustments count as one incentive/waiver "
            "(§1.J.3)."
        )

    # Residential zone limits (§1.J.2)
    if inp.is_residential_zone is not False:
        constraints.append(
            "Height cap (residential zone): Max total height increase of "
            "3 stories or 33 feet above otherwise applicable zoning height "
            "limit (§1.J.2)."
        )

    constraints.append(
        "Open space: Max 50% reduction in otherwise required open "
        "space (§1.J.4)."
    )
    constraints.append(
        "Bicycle parking: Max 50% reduction in otherwise required "
        "bicycle parking (§1.J.5)."
    )
    constraints.append(
        "Tree planting: Max 25% reduction in otherwise required tree "
        "planting requirements (§1.J.6)."
    )

    # Commercial zone limits (§1.J.7)
    if inp.is_commercial_zone is not False:
        constraints.append(
            "Ground story (commercial zone): Max 30% reduction in ground "
            "story requirements (min height, nonresidential floor area, "
            "glazing/transparency, pedestrian entrance spacing). Multiple "
            "modifications may be combined as one incentive/waiver, each "
            "capped at 30% (§1.J.7)."
        )

    constraints.append(
        "Height stepback (adjoining restrictive zones): Building height "
        "stepped back at 45-degree angle from horizontal plane 25 ft "
        "above grade at property line of any adjoining RW1 or more "
        "restrictive zone lot (§1.J.8)."
    )
    constraints.append(
        "Top-story stepback: For projects seeking 3+ stories or 22+ ft "
        "height increase, top story stepped back 10 ft from street-facing "
        "exterior face (all faces if 70+ ft front width). Exempt if "
        "already 10+ ft from required yards or if frontage is on a "
        "Boulevard/Avenue per General Plan (§1.J.9)."
    )


def _add_covenant_obligation(
    inp: ED1Input, obligations: list[str],
) -> None:
    """§1.P — affordability covenant duration."""
    if inp.public_subsidy_covenant_exception_flag is True:
        obligations.append(
            "Affordability covenant: Project receives public subsidy tied "
            "to a specified covenant period (including LIHTC). Minimum "
            "covenant of 55 years for rental units or 45 years for for-sale "
            "units, as verified by LAHD (§1.P exception)."
        )
    elif inp.public_subsidy_covenant_exception_flag is False:
        obligations.append(
            "Affordability covenant: 99 years from issuance of Certificate "
            "of Occupancy. Covenant must be recorded with LA County Recorder "
            "and acceptable to LAHD prior to building permit issuance (§1.P)."
        )
    else:
        obligations.append(
            "Affordability covenant: Default is 99 years from Certificate "
            "of Occupancy, unless the project receives a public subsidy "
            "tied to a specified covenant period (including LIHTC), in which "
            "case 55 years (rental) or 45 years (for-sale) applies. Confirm "
            "funding structure to determine applicable term (§1.P)."
        )

    # §1.K — ADU / conversion covenant
    obligations.append(
        "ADU/conversion covenant: Any ADUs or future conversion of "
        "amenity spaces or parking areas into dwelling units must be "
        "provided as covenanted affordable units at the same affordability "
        "levels and terms as the approved project (§1.K)."
    )


def _add_procedural_benefits(benefits: list[str]) -> None:
    """§5, 6, 7 — procedural advantages."""
    benefits.append(
        "Exempt from discretionary review including Site Plan Review "
        "(LAMC §16.05, §13B.2.4), Haul Routes (LAMC §91.7006.7.5), and "
        "related Public Works reviews (LAMC §62.161–62.178, §46.00–46.06)."
    )
    benefits.append(
        "Streamlined ministerial review process, consistent with the "
        "process used for projects eligible under Government Code "
        "§65913.4 (State Density Bonus law) (§5)."
    )
    benefits.append(
        "Memo-stated target: Clearances and utility releases within "
        "5 business days of application (§6)."
    )
    benefits.append(
        "Memo-stated target: All reviews and approvals concluded within "
        "60 days of complete application submission. Required changes or "
        "amendments provided by day 30. Reviews to be conducted "
        "simultaneously, not sequentially (§7)."
    )


def _add_reference_notes_for_blocked(
    warnings: list[str],
    constraints: list[str],
    benefits: list[str],
) -> None:
    """Condensed reference notes when blockers make ED1 inapplicable.

    Instead of the full design/constraint/benefit lists, provide a
    single note in each section pointing the user to the memo if
    blockers are later resolved.
    """
    warnings.append(
        "ED1 design requirements (parking screening, pedestrian "
        "entrance, glazing minimums per §1.F–H) would apply if ED1 "
        "blockers are resolved."
    )
    constraints.append(
        "ED1 incentive/waiver constraints (§1.J) would apply if ED1 "
        "eligibility is established. See memo for details on FAR, "
        "height, setback, open space, and other caps."
    )
    benefits.append(
        "ED1 procedural benefits (streamlined ministerial review, "
        "5-business-day clearances, 60-day review target per §5–7) "
        "would apply if ED1 eligibility is established."
    )


def _build_comparison(
    blockers: list[str],
    missing: list[str],
) -> ED1BaselineComparison:
    """Build a structured comparison of baseline vs ED1 pathway."""
    if blockers:
        overall = (
            "Based on available inputs, ED1 eligibility appears unlikely "
            "due to identified blockers. The baseline discretionary review "
            "pathway would apply."
        )
    elif missing:
        overall = (
            "Based on available inputs, ED1 may be viable but several "
            "required confirmations are outstanding. Further investigation "
            "is needed before concluding ED1 applicability."
        )
    else:
        overall = (
            "Based on available inputs, ED1 appears likely viable. If "
            "confirmed, ED1 would provide significant procedural "
            "advantages over the baseline discretionary pathway."
        )

    return ED1BaselineComparison(
        review_pathway=(
            "Baseline: Discretionary review including Site Plan Review and "
            "related processes. ED1: Streamlined ministerial review, exempt "
            "from discretionary processes."
        ),
        entitlement_friction=(
            "Baseline: Subject to public hearing, neighborhood council "
            "review, and potential appeals. ED1: Ministerial processing "
            "is intended to eliminate discretionary hearing requirements."
        ),
        procedural_speed=(
            "Baseline: Timelines vary; discretionary review can take months "
            "to years. ED1: Memo targets 5-business-day clearances and "
            "60-day total review from complete submission."
        ),
        major_obligations=(
            "ED1 carries specific obligations not present in baseline: "
            "99-year (or 55/45-year) affordability covenant, RSO "
            "replacement requirements, tenant right-of-return protections, "
            "and design constraints (parking screening, glazing minimums, "
            "pedestrian entrance). These should be weighed against "
            "procedural benefits."
        ),
        overall_assessment=overall,
    )


# ── Status derivation ────────────────────────────────────────────────────────

def _derive_status(
    blockers: list[str],
    missing: list[str],
) -> tuple[ED1Status, ED1Confidence]:
    """Derive screening status and confidence from findings."""
    if blockers:
        # Any confirmed blocker → likely ineligible
        if missing:
            return ED1Status.LIKELY_INELIGIBLE, ED1Confidence.MEDIUM
        return ED1Status.LIKELY_INELIGIBLE, ED1Confidence.HIGH

    if not missing:
        return ED1Status.LIKELY_ELIGIBLE, ED1Confidence.HIGH

    # No blockers but some missing inputs
    critical_missing_keywords = [
        "is_100_percent_affordable",
        "requires_zone_change",
        "requires_variance",
        "requires_general_plan_amendment",
        "zoning_is_single_family",
    ]
    critical_count = sum(
        1 for m in missing
        if any(kw in m for kw in critical_missing_keywords)
    )

    if critical_count >= 3:
        return ED1Status.INSUFFICIENT_INFORMATION, ED1Confidence.LOW

    if critical_count >= 1:
        return ED1Status.POTENTIALLY_ELIGIBLE, ED1Confidence.LOW

    return ED1Status.POTENTIALLY_ELIGIBLE, ED1Confidence.MEDIUM


def _build_summary(
    status: ED1Status,
    blockers: list[str],
    missing: list[str],
) -> str:
    """Build a plain-English summary sentence."""
    if status == ED1Status.LIKELY_ELIGIBLE:
        return (
            "Based on available inputs, this project appears likely eligible "
            "for ED1 streamlined processing. All core eligibility conditions "
            "appear to be met. Review the listed obligations and design "
            "constraints before proceeding."
        )
    if status == ED1Status.LIKELY_INELIGIBLE:
        n = len(blockers)
        return (
            f"Based on available inputs, this project appears likely "
            f"ineligible for ED1 due to {n} identified blocker(s). "
            f"See blockers list for details."
        )
    if status == ED1Status.INSUFFICIENT_INFORMATION:
        return (
            "Insufficient information to determine ED1 eligibility. "
            f"{len(missing)} required input(s) are missing. See "
            f"missing_inputs for the specific confirmations needed."
        )
    # POTENTIALLY_ELIGIBLE
    return (
        "No confirmed blockers identified, but ED1 eligibility cannot "
        f"be fully determined due to {len(missing)} missing input(s). "
        f"Additional confirmation is required."
    )


# ── Main entry point ─────────────────────────────────────────────────────────

def screen_ed1(inp: ED1Input) -> ED1Result:
    """Run deterministic ED1 screening against the provided inputs.

    Pure function — no side effects, no external calls.

    Returns an ED1Result with status, confidence, blockers, warnings,
    obligations, missing inputs, procedural benefits, incentive
    constraints, and a baseline comparison.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    obligations: list[str] = []
    missing: list[str] = []
    assumptions: list[str] = []
    benefits: list[str] = []
    constraints: list[str] = []

    # ── Core eligibility gates ───────────────────────────────────────────
    _check_affordability(inp, blockers, missing)
    _check_discretionary_triggers(inp, blockers, missing)
    _check_zone_classification(inp, blockers, missing)

    # ── Environmental / site conditions ──────────────────────────────────
    _check_environmental(inp, blockers, warnings, missing)

    # ── Fire / hillside ──────────────────────────────────────────────────
    _check_fire_hillside(inp, blockers, missing)

    # ── Historic resource ────────────────────────────────────────────────
    _check_historic(inp, blockers, warnings, missing)

    # ── RSO / tenant protection ──────────────────────────────────────────
    _check_rso(inp, blockers, warnings, obligations, missing)

    # ── Residential capacity gate ────────────────────────────────────────
    _check_residential_capacity(inp, blockers, missing)

    # ── Conditional sections ────────────────────────────────────────────
    # When hard blockers exist, design/constraint/benefit details are
    # misleading if presented as actively applicable.  Gate them so
    # ineligible projects get a clean, focused output.
    if not blockers:
        _add_design_warnings(inp, warnings)
        _add_incentive_constraints(inp, constraints)
        _add_procedural_benefits(benefits)
    else:
        _add_reference_notes_for_blocked(warnings, constraints, benefits)

    # Covenant obligation is always surfaced — it helps explain the
    # weight of the ED1 path even when blocked, and is relevant to
    # screening posture.
    _add_covenant_obligation(inp, obligations)

    # ── Derive status and build result ───────────────────────────────────
    status, confidence = _derive_status(blockers, missing)
    summary = _build_summary(status, blockers, missing)
    comparison = _build_comparison(blockers, missing)

    return ED1Result(
        status=status,
        confidence=confidence,
        summary=summary,
        blockers=blockers,
        warnings=warnings,
        obligations=obligations,
        missing_inputs=missing,
        assumptions_used=assumptions,
        procedural_benefits=benefits,
        incentive_constraints=constraints,
        comparison_to_baseline=comparison,
    )
