"""Density authority resolution: parcel regime, zone-to-density mapping, and authority interrupters.

Implements Steps 1, 1.5, and 2 of the Density decision sequence.
"""

from __future__ import annotations

import re

from density.models import (
    AuthorityInterrupters,
    CMCandidateOption,
    DensityIssue,
    DensityStandard,
    GPDensityLookup,
    ParcelRegime,
)
from models.site import Site

# Zone-to-Density Inheritance Table (LAMC)
ZONE_DENSITY_TABLE: dict[str, dict] = {
    "RD1.5": {"inherited_zone": "RD1.5", "sf_per_du": 1500, "lamc_source": "LAMC 12.09.5"},
    "RD2":   {"inherited_zone": "RD2",   "sf_per_du": 2000, "lamc_source": "LAMC 12.09.5"},
    "RD3":   {"inherited_zone": "RD3",   "sf_per_du": 3000, "lamc_source": "LAMC 12.09.5"},
    "RD4":   {"inherited_zone": "RD4",   "sf_per_du": 4000, "lamc_source": "LAMC 12.09.5"},
    "RD5":   {"inherited_zone": "RD5",   "sf_per_du": 5000, "lamc_source": "LAMC 12.09.5"},
    "RD6":   {"inherited_zone": "RD6",   "sf_per_du": 6000, "lamc_source": "LAMC 12.09.5"},
    "R3":    {"inherited_zone": "R3",    "sf_per_du": 800,  "lamc_source": "LAMC 12.10"},
    "RAS3":  {"inherited_zone": "R3",    "sf_per_du": 800,  "lamc_source": "LAMC 12.10.5"},
    "R4":    {"inherited_zone": "R4",    "sf_per_du": 400,  "lamc_source": "LAMC 12.11"},
    "RAS4":  {"inherited_zone": "R4",    "sf_per_du": 400,  "lamc_source": "LAMC 12.11.5"},
    "R5":    {"inherited_zone": "R5",    "sf_per_du": 200,  "lamc_source": "LAMC 12.12"},
    "C1":    {"inherited_zone": "R3",    "sf_per_du": 800,  "lamc_source": "LAMC 12.14"},
    "C1.5":  {"inherited_zone": "R4",    "sf_per_du": 400,  "lamc_source": "LAMC 12.14.5"},
    "C2":    {"inherited_zone": "R4",    "sf_per_du": 400,  "lamc_source": "LAMC 12.14.5"},
    "C4":    {"inherited_zone": "R4",    "sf_per_du": 400,  "lamc_source": "LAMC 12.16"},
    "C5":    {"inherited_zone": "R4",    "sf_per_du": 400,  "lamc_source": "LAMC 12.16.5"},
    "CR":    {"inherited_zone": "R4",    "sf_per_du": 400,  "lamc_source": "LAMC 12.13"},
    "CM":    {"inherited_zone": None,    "sf_per_du": None,  "lamc_source": "LAMC 12.17.5"},
    "MR1":   {"inherited_zone": "R4",    "sf_per_du": 400,  "lamc_source": "LAMC 12.17.5"},
    "MR2":   {"inherited_zone": "R5",    "sf_per_du": 200,  "lamc_source": "LAMC 12.17.5"},
}

# General Plan land use designation -> density range scaffold.
# Intentionally incomplete. Entries are best-effort from the LA City GP framework.
# When a designation is missing, the lookup returns gp_density_resolved=False.
GP_DENSITY_RANGES: dict[str, dict] = {
    "Low Residential":            {"min_sf_per_du": 5000, "max_sf_per_du": 11000, "source": "LA GP Framework Element Table 3-1"},
    "Low Medium I Residential":   {"min_sf_per_du": 2500, "max_sf_per_du": 5000,  "source": "LA GP Framework Element Table 3-1"},
    "Low Medium II Residential":  {"min_sf_per_du": 1500, "max_sf_per_du": 2500,  "source": "LA GP Framework Element Table 3-1"},
    "Medium Residential":         {"min_sf_per_du": 800,  "max_sf_per_du": 1500,  "source": "LA GP Framework Element Table 3-1"},
    "High Medium Residential":    {"min_sf_per_du": 400,  "max_sf_per_du": 800,   "source": "LA GP Framework Element Table 3-1"},
    "High Residential":           {"min_sf_per_du": 200,  "max_sf_per_du": 400,   "source": "LA GP Framework Element Table 3-1"},
    "Very High Residential":      {"min_sf_per_du": 100,  "max_sf_per_du": 200,   "source": "LA GP Framework Element Table 3-1"},
    # Commercial designations that permit residential — ranges are approximate
    "Neighborhood Commercial":    {"min_sf_per_du": 800,  "max_sf_per_du": 800,   "source": "LA GP Framework (approximate)"},
    "Community Commercial":       {"min_sf_per_du": 400,  "max_sf_per_du": 800,   "source": "LA GP Framework (approximate)"},
    "Regional Commercial":        {"min_sf_per_du": 200,  "max_sf_per_du": 400,   "source": "LA GP Framework (approximate)"},
    "Regional Center":            {"min_sf_per_du": 200,  "max_sf_per_du": 400,   "source": "LA GP Framework (approximate)"},
}

# Regex for extracting a base zone from a raw zoning string.
# LA City format: optional [Q]/[T] prefix, base zone, optional height district/suffix.
# Examples: "C2-2D", "[Q]C2-1-CPIO", "R4-1", "RAS4-1VL", "RD1.5-1XL", "C1.5-2"
_BASE_ZONE_PATTERN = re.compile(
    r"(?:\[.*?\])*"          # strip [Q], [T], [D] prefixes
    r"\s*"
    r"(RD1\.5|RD[2-6]"      # RD zones (must precede R catch-all)
    r"|RAS[34]"              # RAS zones
    r"|MR[12]"               # MR zones
    r"|R[1-5]"               # R zones
    r"|C1\.5|C[1-5]"         # C zones (C1.5 before C1)
    r"|CR|CM"                # CR, CM
    r")",
    re.IGNORECASE,
)


def extract_base_zone(raw_zone: str | None) -> tuple[str | None, str]:
    """Normalize and extract the base zone from a raw zoning string.

    Returns:
        (base_zone or None, failure_reason or "")
    """
    if not raw_zone or not raw_zone.strip():
        return None, "raw_zone_missing"

    m = _BASE_ZONE_PATTERN.search(raw_zone.strip())
    if not m:
        return None, "extraction_failed"

    # Normalize casing to match ZONE_DENSITY_TABLE keys
    extracted = m.group(1)
    # Try exact match first (preserves case like "RD1.5", "C1.5")
    for key in ZONE_DENSITY_TABLE:
        if key.upper() == extracted.upper():
            return key, ""

    return extracted.upper(), ""


def _find_cpio_overlays(overlay_zones: list[str]) -> list[str]:
    """Return all CPIO-matching entries from overlay list."""
    return [o for o in overlay_zones if "CPIO" in o.upper()]


def lookup_gp_density(designation: str | None) -> GPDensityLookup:
    """Look up GP land use designation in the density range scaffold."""
    if not designation:
        return GPDensityLookup()

    entry = GP_DENSITY_RANGES.get(designation)
    if entry:
        return GPDensityLookup(
            designation=designation,
            min_sf_per_du=entry["min_sf_per_du"],
            max_sf_per_du=entry["max_sf_per_du"],
            gp_density_resolved=True,
            source=entry["source"],
        )

    return GPDensityLookup(
        designation=designation,
        gp_density_resolved=False,
    )


def establish_parcel_regime(site: Site) -> ParcelRegime:
    """STEP 1: Extract parcel regime from site data."""
    confidence = "confirmed"

    if site.zone_code_chapter == "unknown":
        confidence = "provisional"
    if site.parcel_match_confidence == "low":
        confidence = "provisional"

    overlays = list(site.overlay_zones)

    # Fix #3: select the actual CPIO overlay, not blindly overlay_zones[0]
    cpio_matches = _find_cpio_overlays(overlays)
    cpio = cpio_matches[0] if cpio_matches else None

    return ParcelRegime(
        base_zone=site.zone,
        height_district=site.height_district,
        overlays=overlays,
        specific_plan=site.specific_plan,
        cpio=cpio,
        cpio_subarea=site.specific_plan_subarea,
        d_limitation=site.d_limitations[0] if site.d_limitations else None,
        q_condition=site.q_conditions[0] if site.q_conditions else None,
        general_plan_land_use=site.general_plan_land_use,
        toc_tier_zimas=site.toc_tier,
        toc_tier_verified=False,
        near_major_transit=site.ab2097_area,
        regime_confidence=confidence,
    )


def map_zone_to_density_standard(regime: ParcelRegime) -> tuple[DensityStandard, list[DensityIssue]]:
    """STEP 1.5: Hard lookup of zone to residential density standard.

    Uses extract_base_zone() for robust normalization before table lookup.
    """
    issues: list[DensityIssue] = []

    # Fix #2: robust zone normalization with distinct failure modes
    raw_zone = regime.base_zone
    base_zone, failure_reason = extract_base_zone(raw_zone)

    if failure_reason == "raw_zone_missing":
        issues.append(DensityIssue(
            step="STEP_1.5_zone_density_mapping",
            field="base_zone",
            severity="error",
            message="No base zone available. Cannot determine density standard.",
            action_required="Confirm base zone from ZIMAS or planning documents.",
            confidence_impact="degrades_to_unresolved",
        ))
        return DensityStandard(), issues

    if failure_reason == "extraction_failed":
        issues.append(DensityIssue(
            step="STEP_1.5_zone_density_mapping",
            field="base_zone",
            severity="error",
            message=f"Could not extract a recognized base zone from raw string '{raw_zone}'. Manual review required.",
            action_required=f"Manually identify the base zone from '{raw_zone}'.",
            confidence_impact="degrades_to_unresolved",
        ))
        return DensityStandard(), issues

    entry = ZONE_DENSITY_TABLE.get(base_zone)
    if not entry:
        issues.append(DensityIssue(
            step="STEP_1.5_zone_density_mapping",
            field="base_zone",
            severity="error",
            message=f"Base zone '{base_zone}' (from raw '{raw_zone}') not found in density inheritance table. Manual review required.",
            action_required=f"Determine residential density standard for zone '{base_zone}'.",
            confidence_impact="degrades_to_unresolved",
        ))
        return DensityStandard(), issues

    # Fix #5: CM zone — structured unresolved with candidate options
    if base_zone == "CM":
        issues.append(DensityIssue(
            step="STEP_1.5_zone_density_mapping",
            field="sf_per_du",
            severity="warning",
            message="CM zone: residential density varies by use type. R3 uses = 800 sf/du, other residential at floor level = 400 sf/du. User confirmation required.",
            action_required="Confirm CM zone use type to select 800 vs 400 sf/du.",
            confidence_impact="degrades_to_unresolved",
        ))
        return DensityStandard(
            inherited_zone=None,
            sf_per_du=None,
            lamc_source=entry["lamc_source"],
            is_provisional=True,
            cm_candidate_options=[
                CMCandidateOption(inherited_zone="R3", sf_per_du=800, use_type_label="R3 uses"),
                CMCandidateOption(inherited_zone="R4", sf_per_du=400, use_type_label="other residential at floor level"),
            ],
        ), issues

    return DensityStandard(
        inherited_zone=entry["inherited_zone"],
        sf_per_du=entry["sf_per_du"],
        lamc_source=entry["lamc_source"],
        is_provisional=False,
    ), issues


def check_authority_interrupters(
    regime: ParcelRegime,
    density_standard: DensityStandard,
) -> AuthorityInterrupters:
    """STEP 2: Check whether site-specific controls override base density standard.

    Checks: specific plan, CPIO, D limitation, Q conditions, prior entitlements, GP mismatch.

    Key invariant: baseline density fields are always populated from the zone lookup.
    Governing density fields are ONLY populated when no unresolved interrupter could
    affect density. If any interrupter is unresolved, governing = None.
    """
    issues: list[DensityIssue] = []
    confidence = "confirmed"
    has_unresolved_density_interrupter = False

    sp_overrides = None
    sp_density = None
    cpio_overrides = None
    cpio_density = None
    d_affects = None
    q_affects = None
    gp_mismatch = None

    # 1. Specific Plan
    if regime.specific_plan:
        sp_overrides = None  # Unknown until document pulled
        has_unresolved_density_interrupter = True
        issues.append(DensityIssue(
            step="STEP_2_authority_interrupters",
            field="specific_plan_density",
            severity="warning",
            message=f"Specific plan '{regime.specific_plan}' found but document not pulled. Cannot determine if it overrides density.",
            action_required=f"Download and review specific plan '{regime.specific_plan}' for density provisions.",
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    # 2. CPIO
    if regime.cpio:
        cpio_overrides = None  # Unknown until document pulled
        has_unresolved_density_interrupter = True
        issues.append(DensityIssue(
            step="STEP_2_authority_interrupters",
            field="cpio_density",
            severity="warning",
            message=f"CPIO '{regime.cpio}' (subarea: {regime.cpio_subarea or 'unknown'}) found but document not parsed for density.",
            action_required=f"Review CPIO '{regime.cpio}' subarea {regime.cpio_subarea or '?'} for density provisions.",
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    # 3. D limitation
    if regime.d_limitation:
        d_affects = None  # Unknown until ordinance reviewed
        has_unresolved_density_interrupter = True
        issues.append(DensityIssue(
            step="STEP_2_authority_interrupters",
            field="d_limitation_density",
            severity="warning",
            message=f"D limitation '{regime.d_limitation}' present but ordinance not reviewed for density impact.",
            action_required=f"Review D limitation ordinance '{regime.d_limitation}' for density restrictions.",
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    # 4. Q conditions
    if regime.q_condition:
        q_affects = None  # Unknown until ordinance reviewed
        has_unresolved_density_interrupter = True
        issues.append(DensityIssue(
            step="STEP_2_authority_interrupters",
            field="q_condition_density",
            severity="warning",
            message=f"Q condition '{regime.q_condition}' present but ordinance not reviewed for density impact.",
            action_required=f"Review Q condition ordinance '{regime.q_condition}' for density restrictions.",
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    # 5. Prior entitlements — Fix #4: None = not checked (honest default)
    prior_entitlements: bool | None = None
    issues.append(DensityIssue(
        step="STEP_2_authority_interrupters",
        field="prior_entitlements",
        severity="info",
        message="Prior entitlements / case conditions not checked. ZIMAS case data not queried.",
        action_required="Check ZIMAS for case numbers or prior entitlements affecting density.",
        confidence_impact="degrades_to_provisional",
    ))
    # Unchecked entitlements should degrade confidence at least to provisional
    if confidence == "confirmed":
        confidence = "provisional"

    # 6. GP mismatch check — Fix #6: use GP density scaffold
    gp_lookup = lookup_gp_density(regime.general_plan_land_use)
    if gp_lookup.gp_density_resolved and density_standard.sf_per_du is not None:
        # Check if zoning density falls within GP range
        zone_sf = density_standard.sf_per_du
        if zone_sf < gp_lookup.min_sf_per_du or zone_sf > gp_lookup.max_sf_per_du:
            gp_mismatch = True
            issues.append(DensityIssue(
                step="STEP_2_authority_interrupters",
                field="gp_mismatch",
                severity="warning",
                message=(
                    f"GP land use '{regime.general_plan_land_use}' implies "
                    f"{gp_lookup.min_sf_per_du}-{gp_lookup.max_sf_per_du} sf/du, "
                    f"but zoning yields {zone_sf} sf/du. Possible GP/zoning inconsistency."
                ),
                action_required="Verify GP land use density range against zoning density. GP may govern for State DB base density.",
                confidence_impact="none",
            ))
        else:
            gp_mismatch = False
            issues.append(DensityIssue(
                step="STEP_2_authority_interrupters",
                field="gp_mismatch",
                severity="info",
                message=(
                    f"GP land use '{regime.general_plan_land_use}' range "
                    f"({gp_lookup.min_sf_per_du}-{gp_lookup.max_sf_per_du} sf/du) "
                    f"is consistent with zoning ({zone_sf} sf/du)."
                ),
                confidence_impact="none",
            ))
    elif regime.general_plan_land_use:
        gp_mismatch = None
        issues.append(DensityIssue(
            step="STEP_2_authority_interrupters",
            field="gp_mismatch",
            severity="info",
            message=f"GP land use '{regime.general_plan_land_use}' not in density range scaffold. Cannot auto-check for mismatch.",
            action_required="Manually compare GP land use density range with zoning density.",
            confidence_impact="none",
        ))

    # If density standard itself is unresolved (e.g. CM zone), degrade further
    if density_standard.sf_per_du is None or density_standard.is_provisional:
        confidence = "unresolved"

    # Fix #1: baseline always available, governing only when interrupters are clear
    baseline_sf = density_standard.sf_per_du
    baseline_src = density_standard.lamc_source

    if has_unresolved_density_interrupter or density_standard.is_provisional:
        governing_sf = None
        governing_src = ""
    else:
        governing_sf = baseline_sf
        governing_src = baseline_src

    return AuthorityInterrupters(
        specific_plan_overrides_density=sp_overrides,
        specific_plan_density_sf_per_du=sp_density,
        cpio_overrides_density=cpio_overrides,
        cpio_density_sf_per_du=cpio_density,
        d_limitation_affects_density=d_affects,
        q_condition_affects_density=q_affects,
        prior_entitlements_present=prior_entitlements,
        gp_mismatch=gp_mismatch,
        gp_density_lookup=gp_lookup,
        baseline_density_sf_per_du=baseline_sf,
        baseline_density_source=baseline_src,
        governing_density_sf_per_du=governing_sf,
        governing_density_source=governing_src,
        confidence=confidence,
        issues=issues,
    )
