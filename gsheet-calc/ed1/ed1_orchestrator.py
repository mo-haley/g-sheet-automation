"""ED1 module orchestrator — thin adapter layer.

Maps Site/Project fields to ED1Input, runs the screener, and wraps
the ED1Result into the app-standard ModuleResult contract.

Usage:
    from ed1.ed1_orchestrator import run_ed1_module

    module_result = run_ed1_module(site, project)
    module_result = run_ed1_module(site, project, ed1_overrides=overrides)
"""

from __future__ import annotations

from typing import Any, Optional

from ed1.models import (
    ED1Confidence,
    ED1Input,
    ED1Result,
    ED1Status,
    EnvironmentalSiteStatus,
    HistoricResourceStatus,
)
from ed1.screener import screen_ed1
from models.result_common import (
    ActionPosture,
    Assumption,
    Citation,
    ConfidenceLevel,
    CoverageLevel,
    Finding,
    Interpretation,
    Issue,
    ModuleResult,
    Provenance,
    RunStatus,
    Severity,
    WarningMessage,
)
from models.site import Site
from models.project import Project


# ── Zone classification helpers ──────────────────────────────────────────────

_SINGLE_FAMILY_ZONES = frozenset({
    "RE", "RE9", "RE11", "RE15", "RE20", "RE40",
    "RS", "R1", "RU", "RW1", "RW2",
    "RA", "RE",
    "A1", "A2",
})

# M1/M2/M3 are pure manufacturing zones that likely disallow multifamily,
# but the app does not currently parse LAMC use permissions to confirm.
# MR1/MR2 are manufacturing-residential zones that DO allow multifamily
# (inherit R4/R5 density).  We return None (unknown) for all M-family
# zones so the screener surfaces it as a missing input rather than
# producing a false blocker.
_MANUFACTURING_NO_MULTIFAMILY: frozenset[str] = frozenset()

# MR zones allow multifamily — recognized but not in any blocking set.
_MANUFACTURING_ALLOWS_MULTIFAMILY = frozenset({"MR1", "MR2"})

# Pure manufacturing — recognized so _classify_zone doesn't return
# all-None, but mfg_disallows_mf is set to None (unknown, not False).
_MANUFACTURING_UNKNOWN = frozenset({"M1", "M2", "M3"})

_RESIDENTIAL_ZONES = frozenset({
    "R1", "RU", "RW1", "RW2", "RD1.5", "RD2", "RD3", "RD4", "RD5", "RD6",
    "R2", "R3", "R4", "R5",
    "RS", "RE", "RE9", "RE11", "RE15", "RE20", "RE40",
    "RA", "A1", "A2",
})

_COMMERCIAL_ZONES = frozenset({
    "C1", "C1.5", "C2", "C4", "C5", "CR", "CM",
    "RAS3", "RAS4",
})


def _classify_zone(base_zone: str | None) -> dict[str, bool | None]:
    """Derive zone-family flags from base_zone string.

    Returns None for each flag if base_zone is missing or unrecognized,
    so the screener reports it as missing rather than wrongly classifying.
    """
    if not base_zone:
        return {
            "is_single_family": None,
            "mfg_disallows_mf": None,
            "is_residential": None,
            "is_commercial": None,
        }

    normalized = base_zone.strip().upper()
    # Strip height district suffix (e.g., "R3-1" → "R3")
    core = normalized.split("-")[0]

    is_sf = core in _SINGLE_FAMILY_ZONES
    is_res = core in _RESIDENTIAL_ZONES
    is_com = core in _COMMERCIAL_ZONES
    is_mfg_allows_mf = core in _MANUFACTURING_ALLOWS_MULTIFAMILY
    is_mfg_unknown = core in _MANUFACTURING_UNKNOWN

    # If the zone doesn't match any known set, return None (unknown)
    recognized = is_sf or is_res or is_com or is_mfg_allows_mf or is_mfg_unknown
    if not recognized:
        return {
            "is_single_family": None,
            "mfg_disallows_mf": None,
            "is_residential": None,
            "is_commercial": None,
        }

    # MR zones allow multifamily → mfg_disallows_mf = False
    # Pure M zones → mfg_disallows_mf = None (unknown, needs confirmation)
    if is_mfg_allows_mf:
        mfg_disallows_mf: bool | None = False
    elif is_mfg_unknown:
        mfg_disallows_mf = None
    else:
        mfg_disallows_mf = False

    return {
        "is_single_family": is_sf,
        "mfg_disallows_mf": mfg_disallows_mf,
        "is_residential": is_res,
        "is_commercial": is_com,
    }


def _map_historic_status(site: Site) -> HistoricResourceStatus | None:
    """Best-effort map of Site.historic_status to ED1 enum."""
    hs = site.historic_status
    if hs is None:
        return None

    normalized = hs.strip().lower()
    if normalized in ("", "unknown", "not checked"):
        return HistoricResourceStatus.UNKNOWN
    if normalized in ("none", "not identified", "no"):
        return HistoricResourceStatus.NOT_IDENTIFIED
    # Any positive value → treat as designated
    return HistoricResourceStatus.DESIGNATED_OR_LISTED


def _map_fire_hillside(site: Site) -> tuple[bool | None, bool | None]:
    """Map Site fire/hillside fields to ED1 flags."""
    vhfhsz: bool | None = None
    if site.fire_hazard_zone is not None:
        normalized = site.fire_hazard_zone.strip().lower()
        vhfhsz = "very high" in normalized or "vhfhsz" in normalized

    hillside = site.hillside_area
    return vhfhsz, hillside


def _is_100_pct_affordable(project: Project) -> bool | None:
    """Derive 100% affordability from Project fields."""
    if project.project_type and "affordable" in project.project_type.lower():
        # project_type alone isn't sufficient to confirm 100%
        if project.affordability is not None:
            market = project.affordability.market_pct
            if market == 0.0:
                return True
            if market > 0.0:
                return False
        return None  # project_type suggests affordable but % unconfirmed

    if project.affordability is not None:
        if project.affordability.market_pct == 0.0:
            return True
        if project.affordability.market_pct > 0.0:
            return False

    return None


def build_ed1_input(
    site: Site,
    project: Project,
    overrides: ED1Input | None = None,
) -> ED1Input:
    """Build ED1Input from Site/Project with optional explicit overrides.

    Fields that cannot be derived from existing app data remain None so
    the screener surfaces them as missing inputs.

    If overrides is provided, its non-None fields take precedence over
    any Site/Project derivation.
    """
    zone_info = _classify_zone(site.zone)
    vhfhsz, hillside = _map_fire_hillside(site)

    derived = ED1Input(
        is_100_percent_affordable=_is_100_pct_affordable(project),
        base_zone=site.zone,
        zoning_is_single_family_or_more_restrictive=zone_info["is_single_family"],
        manufacturing_zone_disallows_multifamily=zone_info["mfg_disallows_mf"],
        is_residential_zone=zone_info["is_residential"],
        is_commercial_zone=zone_info["is_commercial"],
        vhfhsz_flag=vhfhsz,
        hillside_area_flag=hillside,
        historic_resource_status=_map_historic_status(site),
        # Fields we cannot derive from current Site/Project:
        # requires_zone_change, requires_variance, requires_general_plan_amendment,
        # residential_pre_bonus_allowed_units, hazardous_site_status,
        # oil_well_site_status, protected_plan_area_historic_check_complete,
        # rso_subject_site, rso_total_units, occupied_units_within_5_years,
        # replacement_unit_trigger, public_subsidy_covenant_exception_flag
    )

    if overrides is not None:
        # Overlay non-None override values
        override_data = overrides.model_dump(exclude_none=True)
        derived_data = derived.model_dump()
        derived_data.update(override_data)
        derived = ED1Input(**derived_data)

    return derived


# ── ED1 → ModuleResult mapping ──────────────────────────────────────────────

_CONFIDENCE_MAP = {
    ED1Confidence.HIGH: ConfidenceLevel.HIGH,
    ED1Confidence.MEDIUM: ConfidenceLevel.MEDIUM,
    ED1Confidence.LOW: ConfidenceLevel.LOW,
}

_STATUS_TO_RUN_STATUS = {
    ED1Status.LIKELY_ELIGIBLE: RunStatus.OK,
    ED1Status.POTENTIALLY_ELIGIBLE: RunStatus.PARTIAL,
    ED1Status.LIKELY_INELIGIBLE: RunStatus.OK,
    ED1Status.INSUFFICIENT_INFORMATION: RunStatus.PARTIAL,
}


def _ed1_to_module_result(
    ed1_result: ED1Result,
    ed1_input: ED1Input,
) -> ModuleResult:
    """Wrap ED1Result into the canonical ModuleResult contract."""
    status = ed1_result.status
    confidence = _CONFIDENCE_MAP[ed1_result.confidence]
    run_status = _STATUS_TO_RUN_STATUS[status]

    # Coverage level
    if status == ED1Status.INSUFFICIENT_INFORMATION:
        coverage = CoverageLevel.THIN
    elif ed1_result.missing_inputs:
        coverage = CoverageLevel.PARTIAL
    else:
        coverage = CoverageLevel.COMPLETE

    # Action posture
    if status == ED1Status.LIKELY_ELIGIBLE:
        posture = ActionPosture.CAN_RELY_WITH_REVIEW
    elif status == ED1Status.LIKELY_INELIGIBLE:
        posture = ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
    elif status == ED1Status.INSUFFICIENT_INFORMATION:
        posture = ActionPosture.MANUAL_INPUT_REQUIRED
    else:
        posture = ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED

    # Build findings from blockers
    findings: list[Finding] = []
    for b in ed1_result.blockers:
        findings.append(Finding(
            code="ed1_blocker",
            message=b,
            severity=Severity.CRITICAL,
            supports_decision=True,
        ))

    # Build issues from missing inputs
    issues: list[Issue] = []
    for m in ed1_result.missing_inputs:
        field_name = m.split(":")[0] if ":" in m else "unknown"
        issues.append(Issue(
            code=f"ed1_missing_{field_name}",
            message=m,
            severity=Severity.MAJOR,
            needs_user_input=True,
        ))

    # Build warnings
    warning_msgs: list[WarningMessage] = []
    for w in ed1_result.warnings:
        warning_msgs.append(WarningMessage(
            code="ed1_warning",
            message=w,
        ))

    # Build assumptions
    assumptions: list[Assumption] = []
    for a in ed1_result.assumptions_used:
        assumptions.append(Assumption(
            code="ed1_assumption",
            message=a,
        ))

    # Module payload — full ED1 result as dict
    payload: dict[str, Any] = ed1_result.model_dump()

    # Blocking flag
    blocking = status == ED1Status.LIKELY_INELIGIBLE and bool(ed1_result.blockers)

    interpretation = Interpretation(
        summary=ed1_result.summary,
        plain_language_result=ed1_result.summary,
        action_posture=posture,
    )

    return ModuleResult(
        module="ed1",
        module_version="v1",
        run_status=run_status,
        coverage_level=coverage,
        confidence=confidence,
        blocking=blocking,
        inputs_summary={
            "is_100_percent_affordable": ed1_input.is_100_percent_affordable,
            "base_zone": ed1_input.base_zone,
            "ed1_status": status.value,
        },
        interpretation=interpretation,
        findings=findings,
        issues=issues,
        warnings=warning_msgs,
        assumptions=assumptions,
        citations=[
            Citation(
                id="ed1_memo_3rd_revised",
                source_type="executive_directive",
                label="Mayor Bass Executive Directive No. 1 (3rd Revised)",
                locator="July 1, 2024",
                notes="Sole source for ED1 v1 screening logic.",
            ),
        ],
        provenance=Provenance(
            source_types=["executive_directive"],
            authoritative_sources_used=[
                "Mayor Bass Executive Directive No. 1 (3rd Revised, July 1, 2024)"
            ],
        ),
        module_payload=payload,
    )


# ── Public entry point ───────────────────────────────────────────────────────

def run_ed1_module(
    site: Site,
    project: Project,
    ed1_overrides: ED1Input | None = None,
) -> ModuleResult:
    """Run ED1 screening and return a ModuleResult.

    Maps Site/Project fields where possible, defers to ed1_overrides
    for anything the caller can supply explicitly, and reports
    everything else as missing.
    """
    ed1_input = build_ed1_input(site, project, overrides=ed1_overrides)
    ed1_result = screen_ed1(ed1_input)
    return _ed1_to_module_result(ed1_result, ed1_input)
