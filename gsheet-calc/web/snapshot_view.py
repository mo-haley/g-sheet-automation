"""Address-only snapshot view model.

Transforms AppResult + Site into the structured view needed by the
address-only feasibility snapshot template.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from models.project import Project
from models.result_common import (
    ActionPosture,
    AppResult,
    CoverageLevel,
    ConfidenceLevel,
    ModuleResult,
    RunStatus,
)
from models.site import Site


# ---------------------------------------------------------------------------
# UI enums
# ---------------------------------------------------------------------------

class CoverageLabel(str, Enum):
    HIGH = "High"
    MODERATE = "Moderate"
    THIN = "Thin"
    INTERRUPTED = "Interrupted"


class ModuleStatus(str, Enum):
    CONFIRMED = "Confirmed from site data"
    PRELIMINARY = "Preliminary"
    THIN = "Thin"
    UNRESOLVED = "Unresolved"
    AUTHORITY_CHECK = "Authority check required"


class Relevance(str, Enum):
    YES = "Yes"
    POSSIBLE = "Possible"
    NO = "No"
    UNCLEAR = "Unclear"


class Sensitivity(str, Enum):
    LOW = "Low"
    MODERATE = "Moderate"
    HIGH = "High"


# ---------------------------------------------------------------------------
# View-model components
# ---------------------------------------------------------------------------

class SiteSummary(BaseModel):
    address: str
    run_type: str = "Address-only screening"
    coverage_label: CoverageLabel
    summary_sentence: str
    authority_flags: list[str] = Field(default_factory=list)


class Signal(BaseModel):
    text: str
    tone: str = "neutral"  # opportunity / risk / uncertainty / neutral


class ObservedField(BaseModel):
    label: str
    value: str


class ModuleCoverage(BaseModel):
    module_name: str
    status: ModuleStatus
    note: str


class ScenarioRow(BaseModel):
    scenario: str
    why_shown: str
    appears_relevant: Relevance
    likely_effect: str
    key_unknowns: str
    next_input_needed: str


class ModuleCard(BaseModel):
    module_name: str
    status: ModuleStatus
    current_read: str
    covers_now: list[str] = Field(default_factory=list)
    depends_on_inputs: list[str] = Field(default_factory=list)
    sensitivity: Sensitivity


class ED1ScreeningSection(BaseModel):
    """View model for the ED1 screening card."""
    status_label: str
    status_tone: str  # eligible / potentially / ineligible / insufficient
    confidence_label: str
    summary: str
    why_this_result: str
    blockers: list[str] = Field(default_factory=list)
    missing_confirmations: list[str] = Field(default_factory=list)
    obligations: list[str] = Field(default_factory=list)
    constraints_summary: list[str] = Field(default_factory=list)
    procedural_benefits: list[str] = Field(default_factory=list)
    baseline_comparison: dict[str, str] = Field(default_factory=dict)
    disclaimer: str = ""
    source_basis: str = ""


class Caveat(BaseModel):
    text: str
    consequence: str


class MissingInput(BaseModel):
    name: str
    why_it_matters: str
    affects_modules: list[str] = Field(default_factory=list)


class MissingInputGroup(BaseModel):
    category: str
    items: list[MissingInput] = Field(default_factory=list)


class SourceEntry(BaseModel):
    source_type: str
    informed: str
    detail: str
    limitation: str = ""


class ProjectSummary(BaseModel):
    """Project inputs for full-analysis mode (not present in address-only)."""
    project_name: str = ""
    total_units: int = 0
    unit_mix_display: str = ""
    commercial_display: str = ""
    affordability_display: str = ""
    policy_path_label: str = ""
    parking_strategy: str = ""


class AB1287Section(BaseModel):
    """AB 1287 stacking bonus view model (populated when state_db lane was evaluated)."""
    eligible: bool = False
    total_units: int | None = None
    stack_bonus_pct: float | None = None
    incentives_available: int | None = None
    ineligibility_reason: str | None = None


class SnapshotViewModel(BaseModel):
    """Complete view model for feasibility snapshot (address-only or full analysis)."""

    site_summary: SiteSummary
    project_summary: ProjectSummary | None = None
    signals: list[Signal] = Field(default_factory=list)
    observed_fields: list[ObservedField] = Field(default_factory=list)
    module_coverage: list[ModuleCoverage] = Field(default_factory=list)
    scenarios: list[ScenarioRow] = Field(default_factory=list)
    ed1_screening: ED1ScreeningSection | None = None
    ab1287: AB1287Section | None = None
    module_cards: list[ModuleCard] = Field(default_factory=list)
    caveats: list[Caveat] = Field(default_factory=list)
    best_next_inputs: list[MissingInput] = Field(default_factory=list)
    missing_inputs: list[MissingInputGroup] = Field(default_factory=list)
    sources: list[SourceEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal enum mapping helpers
# ---------------------------------------------------------------------------

_COVERAGE_TO_LABEL: dict[CoverageLevel, CoverageLabel] = {
    CoverageLevel.COMPLETE: CoverageLabel.HIGH,
    CoverageLevel.PARTIAL: CoverageLabel.MODERATE,
    CoverageLevel.THIN: CoverageLabel.THIN,
    CoverageLevel.UNCERTAIN: CoverageLabel.THIN,
    CoverageLevel.NONE: CoverageLabel.THIN,
}

_MODULE_DISPLAY_NAMES: dict[str, str] = {
    "zimas_linked_docs": "Governing / Linked Docs",
    "far": "Floor Area Ratio",
    "density": "Density",
    "parking": "Parking",
    "setback": "Setback",
    "ed1": "ED1 Screening",
}

# Modules that get their own dedicated view section rather than
# appearing as a generic module card.
_DEDICATED_SECTION_MODULES: frozenset[str] = frozenset({"ed1"})


def _module_status(mr: ModuleResult) -> ModuleStatus:
    if mr.run_status == RunStatus.ERROR:
        return ModuleStatus.UNRESOLVED
    if mr.interpretation.action_posture == ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED:
        return ModuleStatus.AUTHORITY_CHECK
    if mr.coverage_level == CoverageLevel.COMPLETE and mr.confidence in (
        ConfidenceLevel.HIGH,
        ConfidenceLevel.MEDIUM,
    ):
        return ModuleStatus.CONFIRMED
    if mr.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN, CoverageLevel.NONE):
        return ModuleStatus.THIN
    return ModuleStatus.PRELIMINARY


def _module_sensitivity(mr: ModuleResult) -> Sensitivity:
    if mr.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN, CoverageLevel.NONE):
        return Sensitivity.HIGH
    if mr.coverage_level == CoverageLevel.PARTIAL or mr.confidence in (
        ConfidenceLevel.LOW,
        ConfidenceLevel.UNRESOLVED,
    ):
        return Sensitivity.MODERATE
    return Sensitivity.LOW


def _module_display(name: str) -> str:
    return _MODULE_DISPLAY_NAMES.get(name, name.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def _by_name(results: list[ModuleResult], name: str) -> ModuleResult | None:
    for mr in results:
        if mr.module == name:
            return mr
    return None


def _derive_coverage_label(app: AppResult) -> CoverageLabel:
    """Pick the top-level coverage label from aggregate module state.

    Interrupted is reserved for cases where the overall run cannot reliably
    support early screening — majority of modules blocked/errored, or every
    module is thin-or-worse.  A single module needing authority confirmation
    does not force Interrupted; that is surfaced via authority_flags instead.
    """
    results = app.module_results
    total = len(results)
    if total == 0:
        return CoverageLabel.THIN

    levels = [mr.coverage_level for mr in results]
    blocked_or_error = sum(
        1 for mr in results
        if mr.run_status in (RunStatus.BLOCKED, RunStatus.ERROR)
    )
    thin_or_worse = sum(
        1 for lv in levels
        if lv in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN, CoverageLevel.NONE)
    )

    # Interrupted: majority blocked/errored, or every module thin-or-worse
    if blocked_or_error > total / 2 or thin_or_worse == total:
        return CoverageLabel.INTERRUPTED

    if all(lv == CoverageLevel.COMPLETE for lv in levels):
        return CoverageLabel.HIGH

    if thin_or_worse >= 3:
        return CoverageLabel.THIN

    return CoverageLabel.MODERATE


def _collect_authority_flags(app: AppResult) -> list[str]:
    """Return user-facing notes for modules that require authority confirmation."""
    flags: list[str] = []
    for mr in app.module_results:
        if mr.interpretation.action_posture == ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED:
            flags.append(
                f"{_module_display(mr.module)}: authority confirmation required "
                f"before relying on this module's output"
            )
    return flags


def _build_summary_sentence(site: Site, app: AppResult, coverage: CoverageLabel) -> str:
    zone = site.zone or "an unresolved zone"
    parts = []

    if coverage == CoverageLabel.HIGH:
        parts.append(
            f"Preliminary screening suggests this site may support evaluation "
            f"under {zone}, with most address-derived inputs available."
        )
    elif coverage == CoverageLabel.MODERATE:
        parts.append(
            f"Preliminary screening under {zone} provides a useful first pass, "
            f"but some important gaps remain before conclusions can firm up."
        )
    elif coverage == CoverageLabel.THIN:
        parts.append(
            f"Limited site-derived output is available under {zone}. "
            f"Several modules depend on additional project and geometry inputs."
        )
    else:
        parts.append(
            f"One or more modules could not cleanly resolve governing logic under {zone}. "
            f"Authority confirmation is required before relying on these results."
        )

    unresolved = app.summary.unresolved
    if unresolved:
        module_names = ", ".join(_module_display(m) for m in unresolved)
        parts.append(f"Unresolved modules: {module_names}.")

    return " ".join(parts)


def _build_signals(site: Site, app: AppResult) -> list[Signal]:
    """Build ranked signals using slot-based selection.

    Slots (filled in order, at most one per slot):
      1. strongest opportunity
      2. strongest uncertainty
      3. strongest constraint / risk
      4. strongest comparison insight
      5. strongest next-step insight

    This prevents repetitive low-information signals and ensures a balanced
    mix of opportunity + risk + uncertainty.
    """
    density = _by_name(app.module_results, "density")
    parking = _by_name(app.module_results, "parking")
    setback = _by_name(app.module_results, "setback")
    zimas = _by_name(app.module_results, "zimas_linked_docs")
    has_overlays = bool(
        site.overlay_zones or site.q_conditions
        or site.d_limitations or site.specific_plan
    )

    # -- Candidate pools per slot (ordered by strength, first match wins) --

    # Slot 1: Opportunity
    opportunity: Signal | None = None
    if not has_overlays and all(
        mr.coverage_level == CoverageLevel.COMPLETE for mr in app.module_results
    ):
        opportunity = Signal(
            text="Site looks straightforward under base zoning.",
            tone="opportunity",
        )
    elif site.toc_tier or site.ab2097_area:
        detail = f"TOC Tier {site.toc_tier}" if site.toc_tier else "AB 2097 area"
        opportunity = Signal(
            text=f"Parking reductions may materially affect feasibility ({detail} detected).",
            tone="opportunity",
        )
    elif density and density.coverage_level == CoverageLevel.COMPLETE:
        opportunity = Signal(
            text="Density path appears identifiable under base zoning.",
            tone="opportunity",
        )

    # Slot 2: Uncertainty
    uncertainty: Signal | None = None
    thin_modules = [
        mr for mr in app.module_results
        if mr.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN)
    ]
    if density and density.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN):
        uncertainty = Signal(
            text="Density result is thin; governing density factor could not be resolved from site data alone.",
            tone="uncertainty",
        )
    elif setback and setback.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN):
        uncertainty = Signal(
            text="Setback result is thin due to missing lot-edge geometry.",
            tone="uncertainty",
        )
    elif len(thin_modules) >= 2:
        names = " and ".join(_module_display(mr.module) for mr in thin_modules[:2])
        uncertainty = Signal(
            text=f"{names} have thin coverage from address data alone.",
            tone="uncertainty",
        )

    # Slot 3: Constraint / risk
    constraint: Signal | None = None
    if zimas and zimas.interpretation.action_posture == ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED:
        constraint = Signal(
            text="Governing-document linkage requires authority confirmation before relying on results.",
            tone="risk",
        )
    elif site.specific_plan:
        constraint = Signal(
            text=f"Specific plan ({site.specific_plan}) applies; dedication check and subarea rules likely required.",
            tone="risk",
        )
    elif has_overlays:
        overlay_parts = []
        if site.q_conditions:
            overlay_parts.append(f"{len(site.q_conditions)} Q condition(s)")
        if site.d_limitations:
            overlay_parts.append(f"{len(site.d_limitations)} D limitation(s)")
        if site.overlay_zones:
            overlay_parts.append(f"{len(site.overlay_zones)} overlay(s)")
        constraint = Signal(
            text=f"Policy context detected ({', '.join(overlay_parts)}) that may change base assumptions.",
            tone="risk",
        )

    # Slot 4: Comparison insight
    comparison: Signal | None = None
    if density:
        candidates = density.module_payload.get("candidate_routes") or []
        if len(candidates) > 1:
            comparison = Signal(
                text=f"{len(candidates)} entitlement paths appear relevant; scenario comparison recommended.",
                tone="neutral",
            )
    if not comparison and site.toc_tier and density and density.coverage_level != CoverageLevel.UNCERTAIN:
        comparison = Signal(
            text="Base zoning vs transit-area bonus comparison is the primary feasibility question for this site.",
            tone="neutral",
        )

    # Slot 5: Next-step insight
    next_step: Signal | None = None
    if any(mr.requires_manual_input() for mr in app.module_results):
        manual_modules = [
            _module_display(mr.module)
            for mr in app.module_results if mr.requires_manual_input()
        ]
        next_step = Signal(
            text=f"Entering project program would most improve {' and '.join(manual_modules[:2])}.",
            tone="neutral",
        )
    elif thin_modules:
        next_step = Signal(
            text="Lot-edge geometry and unit count are the highest-leverage missing inputs.",
            tone="neutral",
        )

    # Assemble in slot order, skip empty slots
    return [s for s in (opportunity, uncertainty, constraint, comparison, next_step) if s is not None]


def _build_observed_fields(site: Site) -> list[ObservedField]:
    fields: list[ObservedField] = []

    def _add(label: str, val: Any) -> None:
        if val is not None and val != "" and val != []:
            if isinstance(val, list):
                fields.append(ObservedField(label=label, value=", ".join(str(v) for v in val)))
            elif isinstance(val, bool):
                fields.append(ObservedField(label=label, value="Yes" if val else "No"))
            elif isinstance(val, float):
                fields.append(ObservedField(label=label, value=f"{val:,.0f}"))
            else:
                fields.append(ObservedField(label=label, value=str(val)))

    _add("Address", site.address)
    _add("APN", site.apn)
    _add("Jurisdiction", "City of Los Angeles")
    _add("Zone", site.zone)
    if site.zoning_string_raw and site.zoning_string_raw != site.zone:
        _add("Zoning string (raw)", site.zoning_string_raw)
    _add("Height district", site.height_district)
    _add("Community plan", site.community_plan_area)
    _add("General plan land use", site.general_plan_land_use)
    _add("Specific plan", site.specific_plan)
    if site.specific_plan_subarea:
        _add("Specific plan subarea", site.specific_plan_subarea)
    _add("Lot area (sf)", site.lot_area_sf)
    if site.lot_area_sf and site.parcel_count > 1:
        _add("Parcel sub-lots", site.parcel_count)
    if site.site_basis_note:
        _add("Site basis", site.site_basis_note)
    _add("Overlays", site.overlay_zones)
    _add("Q conditions", site.q_conditions)
    _add("D limitations", site.d_limitations)
    _add("TOC tier", site.toc_tier)
    _add("AB 2097 area", site.ab2097_area)
    if site.nearest_transit_stop_distance_ft:
        _add("Nearest transit (ft)", site.nearest_transit_stop_distance_ft)
    _add("Hillside area", site.hillside_area)
    _add("Coastal zone", site.coastal_zone)
    _add("Fire hazard zone", site.fire_hazard_zone)
    _add("Historic status", site.historic_status)

    return fields


def _build_module_coverage(app: AppResult) -> list[ModuleCoverage]:
    rows = []
    for mr in app.module_results:
        status = _module_status(mr)
        rows.append(ModuleCoverage(
            module_name=_module_display(mr.module),
            status=status,
            note=mr.interpretation.summary,
        ))
    return rows


def _build_scenarios(site: Site, app: AppResult) -> list[ScenarioRow]:
    """Build scenario comparison rows.

    Rules:
    - Base zoning always appears.
    - Every non-baseline row requires a concrete site-derived trigger
      (detected field, module output, or parcel attribute).
    - No filler rows.  If a scenario has no site trigger, it is omitted.
    - "why_shown" traces back to the specific detected data point.
    - AB 1287 appears as a row when state_db is a candidate lane.
    """
    rows: list[ScenarioRow] = []
    density = _by_name(app.module_results, "density")

    # Extract unit counts from candidate_routes for display
    base_units: int | None = None
    toc_units: int | None = None
    state_db_units: int | None = None
    state_db_unlimited: bool = False
    if density:
        for c in (density.module_payload.get("candidate_routes") or []):
            if not isinstance(c, dict):
                continue
            lane = c.get("lane", "")
            if lane == "none":
                base_units = c.get("units")
            elif lane == "toc":
                toc_units = c.get("units")
            elif lane == "state_db":
                state_db_units = c.get("units")
                state_db_unlimited = bool(c.get("unlimited"))

    # 1. Base zoning — always shown
    if base_units is not None:
        base_effect = f"Baseline: {base_units} units under base zoning"
    elif density and density.coverage_level == CoverageLevel.COMPLETE:
        base_effect = "Density and parking resolvable under base zoning"
    else:
        base_effect = "Baseline evaluation path"
    rows.append(ScenarioRow(
        scenario="Base zoning only",
        why_shown="Default evaluation path for any site",
        appears_relevant=Relevance.YES,
        likely_effect=base_effect,
        key_unknowns="Project program not entered; yield is indicative only",
        next_input_needed="Unit count and unit mix",
    ))

    # 2. Transit / parking reduction — only if AB 2097 or transit distance detected
    #    (TOC parking is folded into the TOC row below to avoid redundancy)
    if site.ab2097_area:
        rows.append(ScenarioRow(
            scenario="Transit parking-reduction (AB 2097)",
            why_shown=f"Site detected in AB 2097 area",
            appears_relevant=Relevance.YES,
            likely_effect="Parking minimums may not apply for residential uses",
            key_unknowns="Whether residential use is primary; unit count",
            next_input_needed="Confirm residential use and unit count",
        ))
    elif site.nearest_transit_stop_distance_ft and site.nearest_transit_stop_distance_ft <= 2640:
        rows.append(ScenarioRow(
            scenario="Transit parking-reduction screening",
            why_shown=f"Transit stop detected at {site.nearest_transit_stop_distance_ft:,.0f} ft",
            appears_relevant=Relevance.POSSIBLE,
            likely_effect="May qualify for reduced parking if within threshold",
            key_unknowns="Transit type, distance confirmation, unit mix",
            next_input_needed="Confirm transit type and distance",
        ))

    # 3. TOC incentive — only if toc_tier detected
    if site.toc_tier:
        if toc_units is not None and base_units is not None:
            toc_effect = f"Baseline: {base_units} units → TOC: {toc_units} units (+{toc_units - base_units})"
        elif toc_units is not None:
            toc_effect = f"TOC path: {toc_units} units possible"
        else:
            toc_effect = "Could materially change density and parking assumptions"
        rows.append(ScenarioRow(
            scenario="Transit Oriented Communities (TOC)",
            why_shown=f"ZIMAS reports TOC Tier {site.toc_tier} on this parcel",
            appears_relevant=Relevance.YES,
            likely_effect=toc_effect,
            key_unknowns="Affordability commitment level, project program",
            next_input_needed="Affordability strategy (ELI/VLI/LI percentages)",
        ))

    # 4. State Density Bonus — only if density module found state_db as candidate
    if density:
        candidates = density.module_payload.get("candidate_routes") or []
        has_state_db = any(
            isinstance(c, dict) and c.get("lane") == "state_db"
            for c in candidates
        ) if candidates else False
        if has_state_db:
            if state_db_unlimited:
                # 100% affordable project — no numerical cap under §65915(f)(1)
                state_db_effect = "No numerical limit on density for 100% affordable projects (Gov. Code §65915(f)(1))"
            elif state_db_units is not None and base_units is not None:
                diff = state_db_units - base_units
                state_db_effect = (
                    f"Baseline: {base_units} units → State DB: {state_db_units} units (+{diff}). "
                    "May increase allowable density above base zoning based on affordability commitment."
                )
            elif state_db_units is not None:
                state_db_effect = (
                    f"State Density Bonus path: {state_db_units} units possible. "
                    "May increase allowable density above base zoning based on affordability commitment."
                )
            else:
                state_db_effect = (
                    "May increase allowable density above base zoning based on affordability commitment."
                )
            rows.append(ScenarioRow(
                scenario="State Density Bonus Law (Gov. Code §65915)",
                why_shown="Density module identified state_db as a candidate lane",
                appears_relevant=Relevance.POSSIBLE,
                likely_effect=state_db_effect,
                key_unknowns="Affordability commitment, prevailing wage status",
                next_input_needed="Affordability percentages and wage commitment",
            ))

            # 4b. AB 1287 stacking — shown when state_db is a candidate lane.
            # Use computed data from decision-grade mode if available; otherwise show screening text.
            state_db_payload = (density.module_payload.get("full_output") or {}).get("state_db_density")
            if state_db_payload:
                ab1287_eligible = state_db_payload.get("ab1287_eligible", False)
                ab1287_total_units = state_db_payload.get("ab1287_total_units")
                ab1287_stack_units = state_db_payload.get("ab1287_stack_units")
                ab1287_pct = state_db_payload.get("ab1287_stack_bonus_pct")
                ab1287_incentives = state_db_payload.get("ab1287_incentives_available")
                ineligibility_reason = state_db_payload.get("ineligibility_reason")

                if not ab1287_eligible:
                    reason = ineligibility_reason or "project does not meet threshold affordability"
                    ab_effect = f"AB 1287 stacking not available — {reason}"
                elif ab1287_total_units is not None:
                    parts = []
                    if ab1287_pct:
                        pct_str = f"+{ab1287_pct:.1f}%"
                        if ab1287_stack_units is not None:
                            parts.append(
                                f"AB 1287 stackable: {pct_str} additional density "
                                f"({ab1287_stack_units} additional units)."
                            )
                        else:
                            parts.append(f"AB 1287 stackable: {pct_str} additional density.")
                    parts.append(f"Total with stacking: {ab1287_total_units} units.")
                    if ab1287_incentives is not None:
                        parts.append(f"{ab1287_incentives} incentive(s) available.")
                    ab_effect = " ".join(parts)
                elif ab1287_pct:
                    ab_effect = (
                        f"AB 1287 stackable: +{ab1287_pct:.1f}% additional density available. "
                        "Enter unit count for unit calculation."
                    )
                else:
                    ab_effect = (
                        "AB 1287 stacking eligible; enter unit count and affordability details "
                        "for unit calculation."
                    )
            else:
                ab_effect = "Additional 20-50% density bonus may stack on top of primary State DB bonus for projects meeting threshold affordability"
            rows.append(ScenarioRow(
                scenario="AB 1287 Stacking Bonus",
                why_shown="State Density Bonus is a candidate lane — AB 1287 stacking may apply",
                appears_relevant=Relevance.POSSIBLE,
                likely_effect=ab_effect,
                key_unknowns="Affordability commitment and unit count required",
                next_input_needed="Affordability percentages",
            ))

    return rows


_ED1_STATUS_LABELS = {
    "likely_eligible": ("Likely Eligible", "eligible"),
    "potentially_eligible": ("Potentially Eligible", "potentially"),
    "likely_ineligible": ("Likely Ineligible", "ineligible"),
    "insufficient_information": ("Insufficient Information", "insufficient"),
}

_ED1_CONFIDENCE_LABELS = {
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}


def _build_ed1_section(app: AppResult) -> ED1ScreeningSection | None:
    """Build the ED1 screening view section from the ed1 ModuleResult.

    Returns None if ED1 was not run (no ed1 module in results).
    """
    ed1_mr = _by_name(app.module_results, "ed1")
    if ed1_mr is None:
        return None

    payload = ed1_mr.module_payload
    if not payload:
        return None

    status_val = payload.get("status", "insufficient_information")
    confidence_val = payload.get("confidence", "low")
    label, tone = _ED1_STATUS_LABELS.get(status_val, ("Unknown", "insufficient"))
    confidence_label = _ED1_CONFIDENCE_LABELS.get(confidence_val, confidence_val)

    blockers = payload.get("blockers", [])
    missing = payload.get("missing_inputs", [])

    # Build "why this result" explanation
    if blockers:
        why = (
            f"{len(blockers)} blocker(s) identified that would prevent ED1 eligibility."
        )
    elif missing:
        why = (
            f"No blockers found, but {len(missing)} required confirmation(s) "
            f"are still outstanding."
        )
    else:
        why = "All core eligibility conditions appear to be met based on available inputs."

    # Baseline comparison
    comparison_raw = payload.get("comparison_to_baseline", {})
    comparison = {}
    for key in ("review_pathway", "entitlement_friction", "procedural_speed",
                "major_obligations", "overall_assessment"):
        val = comparison_raw.get(key, "")
        if val:
            comparison[key] = val

    return ED1ScreeningSection(
        status_label=label,
        status_tone=tone,
        confidence_label=confidence_label,
        summary=payload.get("summary", ""),
        why_this_result=why,
        blockers=blockers,
        missing_confirmations=missing,
        obligations=payload.get("obligations", []),
        constraints_summary=payload.get("incentive_constraints", []),
        procedural_benefits=payload.get("procedural_benefits", []),
        baseline_comparison=comparison,
        disclaimer=payload.get("screening_disclaimer", ""),
        source_basis=payload.get("source_basis", ""),
    )


def _build_ab1287_section(app: AppResult) -> "AB1287Section | None":
    """Extract AB 1287 stacking fields from the density module's state_db output."""
    density_mr = _by_name(app.module_results, "density")
    if density_mr is None:
        return None
    state_db = (density_mr.module_payload.get("full_output") or {}).get("state_db_density")
    if not state_db:
        return None
    return AB1287Section(
        eligible=state_db.get("ab1287_eligible", False),
        total_units=state_db.get("ab1287_total_units"),
        stack_bonus_pct=state_db.get("ab1287_stack_bonus_pct"),
        incentives_available=state_db.get("ab1287_incentives_available"),
        ineligibility_reason=state_db.get("ineligibility_reason"),
    )


def _build_module_cards(app: AppResult) -> list[ModuleCard]:
    cards: list[ModuleCard] = []

    for mr in app.module_results:
        if mr.module in _DEDICATED_SECTION_MODULES:
            continue

        status = _module_status(mr)
        sens = _module_sensitivity(mr)

        covers: list[str] = []
        depends: list[str] = []
        parking_provisional = False

        # Pull from findings for "covers" and from issues/warnings for "depends"
        for f in mr.findings:
            if f.supports_decision:
                covers.append(f.message)

        for iss in mr.issues:
            if iss.needs_user_input:
                depends.append(iss.message)
            elif iss.needs_authority_confirmation:
                depends.append(iss.message)

        for w in mr.warnings:
            depends.append(w.message)

        # Module-specific enrichment
        if mr.module == "density":
            if not covers:
                covers.append("Governing density family/path identification")
            if not depends:
                depends.append("Unit count and affordability strategy for final yield")

        elif mr.module == "parking":
            if not covers:
                covers.append("Parking code family and transit-reduction eligibility")
            if not depends:
                depends.append("Unit mix, bedroom count, commercial area, affordable status")
            # Provisional whenever unit mix isn't confirmed (status != CONFIRMED)
            parking_provisional = (status != ModuleStatus.CONFIRMED)

        elif mr.module == "setback":
            has_edges = bool(mr.module_payload.get("edges"))
            if not covers:
                if has_edges:
                    covers.append("Zone-family rules and provisional per-edge yard values (edge roles unconfirmed)")
                else:
                    covers.append("Zone-family setback rule identification")
            if not depends:
                if has_edges:
                    depends.append("Edge role confirmation (front/rear/side), alley conditions, frontage conditions")
                else:
                    depends.append("Lot-edge geometry, alley conditions, frontage conditions")

        elif mr.module == "zimas_linked_docs":
            if not covers:
                covers.append("Governing document detection and linkage")
            if not depends:
                depends.append("Ordinance/source retrieval completeness")

        elif mr.module == "far":
            if not covers:
                covers.append("FAR ratio identification from zone table")
            if not depends:
                depends.append("Counted floor area for compliance check")

        # Fix D: provisional prefix when parking unit mix not yet entered
        # Fix C: replace "STATE DB" with "State Density Bonus"
        current_read = mr.interpretation.plain_language_result
        if parking_provisional:
            current_read = "[Provisional — unit mix not entered] " + current_read
        current_read = current_read.replace("STATE DB", "State Density Bonus")

        cards.append(ModuleCard(
            module_name=_module_display(mr.module),
            status=status,
            current_read=current_read,
            covers_now=covers[:5],
            depends_on_inputs=depends[:5],
            sensitivity=sens,
        ))

    return cards


def _build_caveats(
    site: Site,
    app: AppResult,
    project: Project | None = None,
    policy_path_label: str = "",
) -> list[Caveat]:
    caveats: list[Caveat] = []

    setback = _by_name(app.module_results, "setback")
    density = _by_name(app.module_results, "density")
    parking = _by_name(app.module_results, "parking")
    zimas = _by_name(app.module_results, "zimas_linked_docs")

    if setback and setback.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN):
        caveats.append(Caveat(
            text="Missing lot-edge geometry affects setback confidence",
            consequence="Setback values cannot be computed per-edge; only zone-family rules are identified",
        ))

    has_units = project is not None and project.total_units > 0
    has_policy = bool(policy_path_label) and policy_path_label != "Base Zoning Only"
    has_affordability = project is not None and project.affordability is not None

    if not has_units:
        caveats.append(Caveat(
            text="Project program not yet entered",
            consequence="Parking and density remain provisional — final values require unit count, mix, and floor areas",
        ))

    if not has_policy and not has_affordability:
        # Tailor consequence to which lanes are actually candidate routes for this site
        has_toc = bool(site.toc_tier)
        has_state_db_candidate = False
        if density:
            _candidates = density.module_payload.get("candidate_routes") or []
            has_state_db_candidate = any(
                isinstance(c, dict) and c.get("lane") == "state_db"
                for c in _candidates
            )
        if has_toc and has_state_db_candidate:
            _consequence = (
                "TOC and State Density Bonus comparisons are indicative only — "
                "affordability commitment not entered"
            )
        elif has_state_db_candidate:
            _consequence = (
                "State Density Bonus comparison unavailable — no affordability strategy entered"
            )
        elif has_toc:
            _consequence = (
                "TOC scenario comparison is indicative only — affordability commitment not entered"
            )
        else:
            _consequence = "Scenario comparisons are indicative only — no incentive path selected"
        caveats.append(Caveat(
            text="Affordability/incentive path not selected",
            consequence=_consequence,
        ))

    if site.specific_plan or site.overlay_zones:
        caveats.append(Caveat(
            text="Dedication or street-width confirmation may affect site assumptions",
            consequence="Net lot area and effective density area could change with dedication inputs",
        ))

    if zimas and zimas.interpretation.action_posture in (
        ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED,
        ActionPosture.INSUFFICIENT_FOR_PERMIT_USE,
    ):
        caveats.append(Caveat(
            text="Authority/source linkage is partial in governing documents module",
            consequence="Some governing references may be undetected or unresolved",
        ))

    # Pull any blocking issues
    for mr in app.module_results:
        for iss in mr.issues:
            if iss.blocking and len(caveats) < 7:
                caveats.append(Caveat(
                    text=iss.message,
                    consequence=f"Blocking issue in {_module_display(mr.module)}",
                ))

    return caveats[:7]


def _build_best_next_inputs(site: Site, app: AppResult) -> list[MissingInput]:
    """Pick the top 2-3 missing inputs most likely to improve result quality.

    Selection is based on which modules are thinnest and which inputs would
    move the most modules from thin/uncertain to preliminary/confirmed.
    """
    ranked: list[MissingInput] = []

    density = _by_name(app.module_results, "density")
    setback = _by_name(app.module_results, "setback")
    parking = _by_name(app.module_results, "parking")

    # Unit count is almost always the highest-leverage input in address-only
    # mode: it unlocks parking calc and density compliance check.
    if density or parking:
        ranked.append(MissingInput(
            name="Target unit count and unit mix",
            why_it_matters="Unlocks parking calculation and density compliance; highest-leverage single input",
            affects_modules=["Density", "Parking"],
        ))

    # Lot-edge geometry if setback is thin
    if setback and setback.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN):
        ranked.append(MissingInput(
            name="Lot-edge geometry",
            why_it_matters="Moves setback from thin to preliminary; enables per-edge yard values",
            affects_modules=["Setback"],
        ))

    # Affordability strategy if TOC/incentive paths are relevant
    if site.toc_tier:
        ranked.append(MissingInput(
            name="Affordability strategy",
            why_it_matters=f"Determines whether TOC Tier {site.toc_tier} bonuses apply to density and parking",
            affects_modules=["Density", "Parking"],
        ))
    elif density:
        candidates = density.module_payload.get("candidate_routes") or []
        if len(candidates) > 1:
            ranked.append(MissingInput(
                name="Entitlement path selection",
                why_it_matters="Multiple density lanes detected; selecting one firms up density and parking",
                affects_modules=["Density", "Parking"],
            ))

    return ranked[:3]


def _build_missing_inputs() -> list[MissingInputGroup]:
    return [
        MissingInputGroup(
            category="Site Geometry",
            items=[
                MissingInput(
                    name="Parcel edge geometry",
                    why_it_matters="Needed to compute per-edge setback values",
                    affects_modules=["Setback"],
                ),
                MissingInput(
                    name="Frontage conditions",
                    why_it_matters="Determines front-yard setback rule and any prevailing-setback trigger",
                    affects_modules=["Setback"],
                ),
                MissingInput(
                    name="Alley adjacency confirmation",
                    why_it_matters="Affects rear-yard setback and parking access assumptions",
                    affects_modules=["Setback", "Parking"],
                ),
                MissingInput(
                    name="Dedication / current street width confirmation",
                    why_it_matters="Affects net lot area and effective density area",
                    affects_modules=["Density", "FAR", "Setback"],
                ),
                MissingInput(
                    name="Lot type (interior/corner/through/flag)",
                    why_it_matters="Determines which edge rules apply",
                    affects_modules=["Setback"],
                ),
            ],
        ),
        MissingInputGroup(
            category="Project Program",
            items=[
                MissingInput(
                    name="Residential vs mixed-use",
                    why_it_matters="Changes applicable parking and open-space rules",
                    affects_modules=["Parking", "Density"],
                ),
                MissingInput(
                    name="Target unit count",
                    why_it_matters="Required for parking calculation and density compliance check",
                    affects_modules=["Parking", "Density"],
                ),
                MissingInput(
                    name="Unit mix / bedroom count",
                    why_it_matters="Needed to refine parking assumptions",
                    affects_modules=["Parking"],
                ),
                MissingInput(
                    name="Commercial area",
                    why_it_matters="Adds commercial parking and loading requirements",
                    affects_modules=["Parking", "FAR"],
                ),
            ],
        ),
        MissingInputGroup(
            category="Entitlement Strategy",
            items=[
                MissingInput(
                    name="By-right vs incentive path",
                    why_it_matters="Determines which density and parking standards govern",
                    affects_modules=["Density", "Parking"],
                ),
                MissingInput(
                    name="Affordability strategy",
                    why_it_matters="Triggers TOC, State Density Bonus Law, or ED1 (100% affordable) path",
                    affects_modules=["Density", "Parking"],
                ),
                MissingInput(
                    name="TOC or State Density Bonus intent",
                    why_it_matters="Materially changes allowable density and parking minimums",
                    affects_modules=["Density", "Parking"],
                ),
            ],
        ),
        MissingInputGroup(
            category="Building Assumptions",
            items=[
                MissingInput(
                    name="Story count / height target",
                    why_it_matters="Affects height-district compliance and floor area distribution",
                    affects_modules=["FAR"],
                ),
                MissingInput(
                    name="Counted floor area",
                    why_it_matters="Required for FAR compliance check",
                    affects_modules=["FAR"],
                ),
                MissingInput(
                    name="Subterranean parking assumed or not",
                    why_it_matters="Changes parking area calculations and building envelope",
                    affects_modules=["Parking", "FAR"],
                ),
            ],
        ),
    ]


def _build_sources(site: Site, app: AppResult) -> list[SourceEntry]:
    sources: list[SourceEntry] = []

    # Parcel / site
    zimas_detail = f"Pulled {site.pull_timestamp}" if site.pull_timestamp else "Pull timestamp not recorded"
    if site.diag_zoning_ambiguous:
        zimas_detail += f"; AMBIGUOUS: {len(site.diag_all_zone_strings)} zone strings returned"
    sources.append(SourceEntry(
        source_type="ZIMAS parcel lookup",
        informed="Address, APN, lot area, zoning, overlays, TOC tier, AB 2097 status, transit flags",
        detail=zimas_detail,
        limitation="Parcel geometry not included; lot-edge dimensions not derivable",
    ))

    # Zoning
    if site.zone:
        raw_note = (
            f" (raw string: {site.zoning_string_raw})"
            if site.zoning_string_raw and site.zoning_string_raw != site.zone
            else ""
        )
        sources.append(SourceEntry(
            source_type="Zone string parse",
            informed=f"Base zone ({site.zone}), height district, chapter applicability",
            detail=f"Parsed from ZIMAS identify response{raw_note}",
            limitation="Parse confidence depends on zone-string format; non-standard strings may be imprecise",
        ))

    # Governing docs module
    zimas = _by_name(app.module_results, "zimas_linked_docs")
    if zimas:
        prov = zimas.provenance
        auth_sources = ", ".join(prov.authoritative_sources_used) if prov.authoritative_sources_used else "None recorded"
        coverage = zimas.coverage_level.value
        sources.append(SourceEntry(
            source_type="Governing-doc registry",
            informed="D/Q/CPIO/SP linkage, ordinance detection, authority interrupts",
            detail=f"Sources: {auth_sources}; coverage: {coverage}",
            limitation=prov.notes or "No specific retrieval limitations noted",
        ))

    # Per-module provenance (non-zimas)
    for mr in app.module_results:
        if mr.module == "zimas_linked_docs":
            continue
        if mr.provenance.authoritative_sources_used or mr.provenance.notes:
            auth = ", ".join(mr.provenance.authoritative_sources_used) if mr.provenance.authoritative_sources_used else ""
            sources.append(SourceEntry(
                source_type=f"{_module_display(mr.module)} module",
                informed=mr.interpretation.summary[:80] if mr.interpretation.summary else "",
                detail=auth or "Internal rule tables",
                limitation=mr.provenance.notes or "",
            ))

    return sources


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _build_project_summary(
    project: Project, policy_path_label: str = "",
) -> ProjectSummary:
    """Build a ProjectSummary view component from a Project model."""
    unit_parts = []
    for u in project.unit_mix:
        unit_parts.append(f"{u.count} {u.label}")
    unit_mix_display = ", ".join(unit_parts) if unit_parts else "None entered"

    com_parts = []
    for o in project.occupancy_areas:
        com_parts.append(f"{o.use_description} — {o.area_sf:,.0f} sf")
    commercial_display = "; ".join(com_parts) if com_parts else "None"

    aff_display = "None"
    if project.affordability:
        a = project.affordability
        pcts = []
        if a.eli_pct: pcts.append(f"ELI {a.eli_pct}%")
        if a.vli_pct: pcts.append(f"VLI {a.vli_pct}%")
        if a.li_pct: pcts.append(f"LI {a.li_pct}%")
        if a.moderate_pct: pcts.append(f"Moderate {a.moderate_pct}%")
        aff_display = ", ".join(pcts) if pcts else "None"

    return ProjectSummary(
        project_name=project.project_name or "",
        total_units=project.total_units,
        unit_mix_display=unit_mix_display,
        commercial_display=commercial_display,
        affordability_display=aff_display,
        policy_path_label=policy_path_label,
    )


def build_snapshot_view(
    site: Site,
    app: AppResult,
    project: Project | None = None,
    policy_path_label: str = "",
) -> SnapshotViewModel:
    """Build the feasibility snapshot view model.

    When project is provided, the result is a full-analysis snapshot that
    includes project inputs (unit mix, affordability, policy path). When
    project is None, the result is the address-only screening snapshot.
    """
    coverage = _derive_coverage_label(app)
    authority_flags = _collect_authority_flags(app)
    summary_sentence = _build_summary_sentence(site, app, coverage)

    if project is not None:
        run_type = "Full feasibility analysis"
    elif site.site_basis == "multi_parcel_user":
        run_type = "Multi-parcel screening (user-specified APNs)"
    else:
        run_type = "Address-only screening"

    project_summary = None
    if project is not None:
        project_summary = _build_project_summary(project, policy_path_label)

    return SnapshotViewModel(
        site_summary=SiteSummary(
            address=site.address,
            run_type=run_type,
            coverage_label=coverage,
            summary_sentence=summary_sentence,
            authority_flags=authority_flags,
        ),
        project_summary=project_summary,
        signals=_build_signals(site, app),
        observed_fields=_build_observed_fields(site),
        module_coverage=_build_module_coverage(app),
        scenarios=_build_scenarios(site, app),
        ed1_screening=_build_ed1_section(app),
        ab1287=_build_ab1287_section(app),
        module_cards=_build_module_cards(app),
        caveats=_build_caveats(site, app, project=project, policy_path_label=policy_path_label),
        best_next_inputs=_build_best_next_inputs(site, app),
        missing_inputs=_build_missing_inputs(),
        sources=_build_sources(site, app),
    )
