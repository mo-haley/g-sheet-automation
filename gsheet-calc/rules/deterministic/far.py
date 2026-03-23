"""FAR (Floor Area Ratio) determination and calculation engine.

Implements the 10-step FAR decision sequence:
  1. Confirm parcel identity
  2. Parse zoning string
  3. Determine Ch.1 vs Ch.1A floor area definition
  4. Check local/site-specific controls BEFORE baseline
  5. Look up baseline FAR from Table 2
  6. Determine governing FAR source
  7. Determine area basis
  8. Calculate allowable floor area
  9. Calculate proposed FAR
 10. Return outcome state
"""

from __future__ import annotations

import json
from datetime import datetime

from config.settings import DATA_DIR
from ingest.zoning_parser import parse_zoning_string
from models.far_output import (
    AllowableFloorArea,
    AreaBasis,
    BaselineFAR,
    FARIssue,
    FARMetadata,
    FAROutcome,
    FAROutput,
    FloorAreaBreakdownEntry,
    FloorAreaDefinition,
    GoverningFAR,
    IncentiveInfo,
    LocalControls,
    ParcelIdentity,
    ProposedFAR,
    ZoningParse,
)
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.base import BaseRule


def _load_hd_table() -> dict:
    path = DATA_DIR / "height_districts.json"
    return json.loads(path.read_text())


def _load_zone_table() -> dict:
    path = DATA_DIR / "zone_tables.json"
    return json.loads(path.read_text())


def _get_zone_class_key(base_zone: str, hd_data: dict) -> str | None:
    """Map a base zone to its zone_class key for FAR lookup."""
    zone_class_map = hd_data.get("zone_class_map", {})
    return zone_class_map.get(base_zone)


class FARRule(BaseRule):
    """Full FAR determination and calculation following the 10-step sequence."""

    authority_id = "AUTH-FAR"
    code_section = "LAMC 12.21.1"
    topic = "Floor area ratio"

    def evaluate(self, site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
        """Run the complete FAR determination.

        Returns CalcResult list and ReviewIssue list for backward compat.
        Use evaluate_full() to get the structured FAROutput.
        """
        far_output = self.evaluate_full(site, project)
        results, issues = self._convert_to_legacy(far_output, site)
        return results, issues

    def evaluate_full(self, site: Site, project: Project) -> FAROutput:
        """Run full 10-step FAR determination, returning structured FAROutput."""
        output = FAROutput()
        output.metadata.run_timestamp = datetime.now().isoformat()
        output.metadata.zimas_pull_timestamp = site.pull_timestamp

        hd_data = _load_hd_table()
        zone_data = _load_zone_table()

        # Track the lowest confidence seen — downstream inherits at best this level
        min_confidence = "high"

        # ── STEP 1: Confirm Parcel Identity ─────────────────────────────
        output.parcel = self._step1_parcel_identity(site, project, output)
        if output.parcel.identity_confidence == "unresolved":
            min_confidence = "low"
        elif output.parcel.identity_confidence == "provisional":
            min_confidence = _min_conf(min_confidence, "medium")

        # ── STEP 2: Parse Zoning String ─────────────────────────────────
        output.zoning = self._step2_parse_zoning(site, output)
        if output.zoning.parse_confidence == "unresolved":
            min_confidence = "low"
            output.outcome = FAROutcome(
                state="unresolved",
                confidence="low",
                requires_manual_review=True,
                manual_review_reasons=["Zoning string could not be parsed"],
                issues=[f"Zoning parse failed for '{site.zoning_string_raw}'"],
            )
            return output

        if not output.zoning.height_district:
            min_confidence = "low"
            output.outcome = FAROutcome(
                state="unresolved",
                confidence="low",
                requires_manual_review=True,
                manual_review_reasons=["Height district not identified — required for FAR lookup"],
                issues=["Height district missing from zoning string"],
            )
            return output

        # ── STEP 3: Floor Area Definition (Ch.1 vs Ch.1A) ──────────────
        output.floor_area_definition = self._step3_floor_area_def(site, output)

        # ── STEP 4: Local/Site-Specific Controls ────────────────────────
        output.local_controls = self._step4_local_controls(site, output)
        if output.local_controls.override_present:
            min_confidence = _min_conf(min_confidence, "medium")

        # ── STEP 5: Baseline FAR from Table 2 ──────────────────────────
        output.baseline_far = self._step5_baseline_far(
            output.zoning.base_zone,
            output.zoning.height_district,
            output.local_controls,
            hd_data,
            output,
        )
        if output.baseline_far.ratio is None:
            min_confidence = "low"

        # ── STEP 6: Governing FAR Source ────────────────────────────────
        output.governing_far, output.incentive = self._step6_governing_far(
            site, output.baseline_far, output.local_controls, output
        )

        # ── STEP 7: Area Basis ──────────────────────────────────────────
        output.area_basis = self._step7_area_basis(
            site, project, output.governing_far, output.local_controls, output
        )

        # ── STEP 8: Allowable Floor Area ────────────────────────────────
        output.allowable = self._step8_allowable(
            output.baseline_far, output.local_controls, output.incentive,
            output.area_basis, output.governing_far, output
        )

        # ── STEP 9: Proposed FAR ────────────────────────────────────────
        output.proposed = self._step9_proposed(
            project, output.area_basis, output.allowable, output.floor_area_definition, output
        )

        # ── STEP 10: Outcome State ──────────────────────────────────────
        output.outcome = self._step10_outcome(output, min_confidence)

        return output

    # ── STEP 1 ──────────────────────────────────────────────────────────

    def _step1_parcel_identity(
        self, site: Site, project: Project, output: FAROutput
    ) -> ParcelIdentity:
        parcel = ParcelIdentity()
        parcel.address = site.address
        if site.apn:
            parcel.apns = [site.apn]
        parcel.lot_area_sf = site.lot_area_sf
        parcel.survey_area_sf = site.survey_lot_area_sf

        # Calculate dedications
        ded_total = 0.0
        if project.dedication_street_ft or project.corner_cuts_sf:
            ded_total += project.corner_cuts_sf
        parcel.dedications_sf = ded_total if ded_total > 0 else None

        # Multi-parcel check
        parcel.multi_parcel = site.multiple_parcels
        if site.multiple_parcels:
            parcel.lot_tie_confirmed = True if site.lot_tie_assumed else None
            output.issues.append(FARIssue(
                step="STEP_1_parcel_identity",
                field="multi_parcel",
                severity="warning",
                message=f"Multiple APNs ({site.parcel_count} parcels). Confirm lot tie before FAR calculation is final.",
                action_required="Verify lot tie deed recording",
            ))

        # Area mismatch check
        if site.lot_area_sf and site.survey_lot_area_sf:
            diff_pct = abs(site.lot_area_sf - site.survey_lot_area_sf) / site.lot_area_sf * 100
            if diff_pct > 2.0:
                parcel.identity_confidence = "provisional"
                output.issues.append(FARIssue(
                    step="STEP_1_parcel_identity",
                    field="lot_area_sf",
                    severity="warning",
                    message=(
                        f"ZIMAS lot area ({site.lot_area_sf:.0f} sf) differs from survey "
                        f"({site.survey_lot_area_sf:.0f} sf) by {diff_pct:.1f}%. Flagged for manual review."
                    ),
                    action_required="Confirm which area to use for FAR calculation",
                ))

        # Null checks
        if parcel.lot_area_sf is None and parcel.survey_area_sf is None:
            parcel.identity_confidence = "unresolved"
            output.issues.append(FARIssue(
                step="STEP_1_parcel_identity",
                field="lot_area_sf",
                severity="error",
                message="No lot area available (neither ZIMAS nor survey).",
                action_required="Provide lot area from survey or assessor records",
            ))
        elif parcel.identity_confidence != "provisional":
            parcel.identity_confidence = "confirmed"

        return parcel

    # ── STEP 2 ──────────────────────────────────────────────────────────

    def _step2_parse_zoning(self, site: Site, output: FAROutput) -> ZoningParse:
        zp = ZoningParse()

        # If site already has pre-parsed fields, use them
        if site.zone and site.height_district:
            zp.raw_string = site.zoning_string_raw
            zp.base_zone = site.zone
            zp.height_district = self._normalize_hd(site.height_district)
            zp.zone_class = _zone_to_class(site.zone)
            zp.has_D_limitation = len(site.d_limitations) > 0
            zp.D_ordinance_number = site.d_limitations[0] if site.d_limitations else None
            zp.has_Q_condition = len(site.q_conditions) > 0
            zp.Q_ordinance_number = site.q_conditions[0] if site.q_conditions else None
            zp.supplemental_districts = list(site.overlay_zones)
            zp.parse_confidence = "confirmed"
            return zp

        # Parse from raw string
        if not site.zoning_string_raw:
            zp.parse_confidence = "unresolved"
            output.issues.append(FARIssue(
                step="STEP_2_parse_zoning",
                field="zoning_string_raw",
                severity="error",
                message="No zoning string available to parse.",
            ))
            return zp

        parsed = parse_zoning_string(
            site.zoning_string_raw,
            q_ordinances=site.q_conditions or None,
            d_ordinances=site.d_limitations or None,
        )

        zp.raw_string = parsed.raw_string
        zp.base_zone = parsed.base_zone
        zp.zone_class = parsed.zone_class
        zp.height_district = self._normalize_hd(parsed.height_district) if parsed.height_district else None
        zp.has_D_limitation = parsed.has_D_limitation
        zp.D_ordinance_number = parsed.D_ordinance_number
        zp.has_Q_condition = parsed.has_Q_condition
        zp.Q_ordinance_number = parsed.Q_ordinance_number
        zp.has_T_classification = parsed.has_T_classification
        zp.supplemental_districts = parsed.supplemental_districts
        zp.parse_confidence = parsed.parse_confidence

        for issue_msg in parsed.parse_issues:
            output.issues.append(FARIssue(
                step="STEP_2_parse_zoning",
                field="zoning_string_raw",
                severity="warning",
                message=issue_msg,
            ))

        return zp

    def _normalize_hd(self, hd: str | None) -> str | None:
        """Normalize height district string: '1-VL' -> '1VL', '1-L' -> '1L'."""
        if not hd:
            return None
        return hd.replace("-", "").upper()

    # ── STEP 3 ──────────────────────────────────────────────────────────

    def _step3_floor_area_def(self, site: Site, output: FAROutput) -> FloorAreaDefinition:
        fad = FloorAreaDefinition()

        # Check for CPIO-specific definition
        if site.overlay_zones and any("CPIO" in oz.upper() for oz in site.overlay_zones):
            fad.chapter = "cpio_specific"
            fad.source_citation = "CPIO — verify specific ordinance for floor area definition"
            fad.confidence = "low"
            fad.note = "CPIO present; floor area definition may differ from LAMC 12.03"
            output.issues.append(FARIssue(
                step="STEP_3_floor_area_def",
                field="chapter",
                severity="warning",
                message="CPIO present — floor area definition may be CPIO-specific, not LAMC 12.03.",
                action_required="Read CPIO ordinance to determine floor area definition",
            ))
            return fad

        # Use zone code chapter from site if available
        if site.zone_code_chapter == "chapter_1":
            fad.chapter = "ch1"
            fad.source_citation = "LAMC 12.03"
            fad.confidence = "high"
        elif site.zone_code_chapter == "chapter_1a":
            fad.chapter = "ch1a"
            fad.source_citation = "2020 LABC Ch.2"
            fad.confidence = "high"
        else:
            fad.chapter = "unresolved"
            fad.source_citation = ""
            fad.confidence = "low"
            fad.note = "Cannot determine Chapter 1 vs 1A applicability"
            output.issues.append(FARIssue(
                step="STEP_3_floor_area_def",
                field="chapter",
                severity="warning",
                message="Chapter applicability unknown. Floor area definition uncertain.",
                action_required="Determine whether project is Chapter 1 (LAMC 12.03) or Chapter 1A (2020 LABC Ch.2)",
            ))

        return fad

    # ── STEP 4 ──────────────────────────────────────────────────────────

    def _step4_local_controls(self, site: Site, output: FAROutput) -> LocalControls:
        lc = LocalControls()

        # 1. Specific Plan
        if site.specific_plan:
            lc.specific_plan = site.specific_plan
            lc.specific_plan_document_status = "not_available"
            lc.override_present = True
            output.issues.append(FARIssue(
                step="STEP_4_local_controls",
                field="specific_plan",
                severity="warning",
                message=f"Specific Plan '{site.specific_plan}' present. May have its own FAR rules.",
                action_required=f"Download and review {site.specific_plan} for FAR provisions",
            ))

        # 2. CPIO / supplemental overlays
        cpio_overlays = [oz for oz in site.overlay_zones if "CPIO" in oz.upper()]
        if cpio_overlays:
            lc.cpio = cpio_overlays[0]
            lc.cpio_subarea = site.specific_plan_subarea
            lc.cpio_document_status = "not_available"
            lc.override_present = True
            output.issues.append(FARIssue(
                step="STEP_4_local_controls",
                field="cpio_far",
                severity="warning",
                message=f"CPIO '{lc.cpio}' found. CPIO may set its own FAR maximum that overrides height-district FAR.",
                action_required=f"Download and review CPIO ordinance for FAR provisions",
            ))

        # 3. D Limitation
        if site.d_limitations:
            lc.d_limitation = True
            lc.d_ordinance = site.d_limitations[0]
            lc.d_document_status = "not_available"
            lc.override_present = True
            output.issues.append(FARIssue(
                step="STEP_4_local_controls",
                field="d_far_cap",
                severity="warning",
                message=f"D limitation present (Ord. {lc.d_ordinance}). May restrict FAR below baseline.",
                action_required=f"Look up D ordinance {lc.d_ordinance} to determine FAR restriction",
            ))

        # 4. Q Condition
        if site.q_conditions:
            lc.q_condition = True
            lc.q_ordinance = site.q_conditions[0]
            lc.q_affects_far = None  # Unknown until ordinance is read
            output.issues.append(FARIssue(
                step="STEP_4_local_controls",
                field="q_affects_far",
                severity="info",
                message=f"Q condition present (Ord. {lc.q_ordinance}). May affect FAR indirectly.",
                action_required=f"Review Q ordinance {lc.q_ordinance} for use restrictions that may affect FAR",
            ))

        # 5. Community Plan
        if site.community_plan_area:
            lc.community_plan = site.community_plan_area

        return lc

    # ── STEP 5 ──────────────────────────────────────────────────────────

    def _step5_baseline_far(
        self,
        base_zone: str | None,
        height_district: str | None,
        local_controls: LocalControls,
        hd_data: dict,
        output: FAROutput,
    ) -> BaselineFAR:
        bl = BaselineFAR()

        if not base_zone or not height_district:
            bl.note = "Cannot look up baseline FAR: missing base zone or height district"
            output.issues.append(FARIssue(
                step="STEP_5_baseline_far",
                field="ratio",
                severity="error",
                message=bl.note,
            ))
            return bl

        # Get zone class key for lookup
        zone_class_key = _get_zone_class_key(base_zone, hd_data)
        if not zone_class_key:
            bl.note = f"Base zone '{base_zone}' not found in zone_class_map"
            output.issues.append(FARIssue(
                step="STEP_5_baseline_far",
                field="ratio",
                severity="error",
                message=bl.note,
                action_required="Manual FAR lookup required for this zone",
            ))
            return bl

        hd_info = hd_data.get("height_districts", {}).get(height_district, {})
        if not hd_info:
            bl.note = f"Height district '{height_district}' not found in lookup table"
            output.issues.append(FARIssue(
                step="STEP_5_baseline_far",
                field="ratio",
                severity="error",
                message=bl.note,
                action_required="Manual FAR lookup required for this height district",
            ))
            return bl

        far_by_class = hd_info.get("far_by_zone_class", {})
        ratio = far_by_class.get(zone_class_key)
        if ratio is None:
            bl.note = f"No FAR defined for zone class '{zone_class_key}' in HD {height_district}"
            output.issues.append(FARIssue(
                step="STEP_5_baseline_far",
                field="ratio",
                severity="error",
                message=bl.note,
            ))
            return bl

        bl.ratio = ratio
        bl.zone_row_used = zone_class_key
        bl.height_district_column_used = height_district
        bl.source = f"Table 2: {zone_class_key} in HD{height_district} -> {ratio}:1"

        # Mark provisional if local overrides present
        if local_controls.override_present:
            bl.is_provisional = True
            bl.note = "Baseline FAR is provisional — local control(s) present that may override"

        return bl

    # ── STEP 6 ──────────────────────────────────────────────────────────

    def _step6_governing_far(
        self,
        site: Site,
        baseline: BaselineFAR,
        local_controls: LocalControls,
        output: FAROutput,
    ) -> tuple[GoverningFAR, IncentiveInfo]:
        gov = GoverningFAR()
        inc = IncentiveInfo()

        chain: list[str] = []
        if baseline.ratio is not None:
            chain.append(f"{output.zoning.base_zone} base zone")
            chain.append(f"HD{output.zoning.height_district} -> {baseline.ratio}:1 baseline")

        # Check for local modifications
        local_far: float | None = None
        local_source = ""

        if local_controls.cpio_far is not None:
            local_far = local_controls.cpio_far
            local_source = f"CPIO {local_controls.cpio} Subarea {local_controls.cpio_subarea or '?'}"
            chain.append(f"{local_source} -> {local_far}:1")

        if local_controls.specific_plan_far is not None:
            local_far = local_controls.specific_plan_far
            local_source = f"Specific Plan {local_controls.specific_plan}"
            chain.append(f"{local_source} -> {local_far}:1")

        if local_controls.d_far_cap is not None:
            # D limitation restricts, so take the lower of baseline/d_cap
            if local_far is None or local_controls.d_far_cap < local_far:
                local_far = local_controls.d_far_cap
                local_source = f"D limitation Ord. {local_controls.d_ordinance}"
                chain.append(f"{local_source} -> restricts to {local_far}:1")

        # Check for incentive modifications
        incentive_far: float | None = None
        if site.toc_tier and site.toc_tier > 0:
            inc.pathway = "toc"
            inc.document_status = "provisional"
            # TOC FAR bonuses are advisory — flag but don't compute here
            chain.append(f"TOC Tier {site.toc_tier} — incentive FAR available (advisory)")

        # Determine governing state
        if local_controls.override_present and local_far is None:
            # Override present but not yet quantified
            unresolved_reasons = []
            if local_controls.specific_plan and local_controls.specific_plan_document_status != "downloaded_and_parsed":
                unresolved_reasons.append(f"Specific Plan '{local_controls.specific_plan}' not parsed")
            if local_controls.cpio and local_controls.cpio_document_status != "downloaded_and_parsed":
                unresolved_reasons.append(f"CPIO '{local_controls.cpio}' not parsed")
            if local_controls.d_limitation and local_controls.d_document_status != "downloaded_and_parsed":
                unresolved_reasons.append(f"D ordinance {local_controls.d_ordinance} not parsed")

            if unresolved_reasons:
                gov.state = "unresolved"
                gov.confidence = "low"
                gov.applicable_ratio = baseline.ratio  # Best guess, but provisional
                gov.source_citation = baseline.source + " (provisional — local overrides not verified)"
                gov.issues = unresolved_reasons
            else:
                gov.state = "baseline"
                gov.applicable_ratio = baseline.ratio
                gov.source_citation = baseline.source
                gov.confidence = "high"
        elif local_far is not None:
            gov.state = "locally_modified"
            gov.applicable_ratio = local_far
            gov.source_citation = local_source
            gov.confidence = "high"
        elif inc.modified_far is not None:
            gov.state = "incentive_modified"
            gov.applicable_ratio = inc.modified_far
            gov.source_citation = f"{inc.pathway}: {inc.ordinance_or_case or 'TBD'}"
            gov.confidence = "medium"
        elif baseline.ratio is not None:
            gov.state = "baseline"
            gov.applicable_ratio = baseline.ratio
            gov.source_citation = baseline.source
            gov.confidence = "high" if not baseline.is_provisional else "medium"
        else:
            gov.state = "unresolved"
            gov.confidence = "low"

        gov.authority_chain = chain
        return gov, inc

    # ── STEP 7 ──────────────────────────────────────────────────────────

    def _step7_area_basis(
        self,
        site: Site,
        project: Project,
        governing_far: GoverningFAR,
        local_controls: LocalControls,
        output: FAROutput,
    ) -> AreaBasis:
        ab = AreaBasis()
        ab.lot_area_sf = site.lot_area_sf or site.survey_lot_area_sf

        # Calculate dedications
        ded = project.corner_cuts_sf
        ab.dedications_sf = ded if ded > 0 else None

        # Buildable area = lot - dedications (simplified)
        if ab.lot_area_sf is not None:
            ab.buildable_area_sf = ab.lot_area_sf - (ab.dedications_sf or 0)
        else:
            ab.buildable_area_sf = None

        # Determine which area to use
        if local_controls.cpio_far is not None:
            # CPIO often specifies "lot area x multiplier"
            ab.type = "lot_area"
            ab.value_sf = ab.lot_area_sf
            ab.source = f"CPIO specification — lot area basis"
            ab.confidence = "medium"
        elif local_controls.specific_plan_far is not None:
            ab.type = "lot_area"
            ab.value_sf = ab.lot_area_sf
            ab.source = "Specific plan — assumed lot area basis (verify)"
            ab.confidence = "low"
        elif ab.dedications_sf and ab.dedications_sf > 0:
            ab.type = "net_post_dedication"
            ab.value_sf = ab.buildable_area_sf
            ab.source = "LAMC 12.21.1 buildable area (lot minus dedications)"
            ab.confidence = "high"
        else:
            # Standard LAMC: buildable area = lot area when no dedications
            ab.type = "buildable_area"
            ab.value_sf = ab.lot_area_sf
            ab.source = "LAMC 12.21.1 buildable area (no dedications; equals lot area)"
            ab.confidence = "high"

        if ab.value_sf is None:
            ab.confidence = "low"
            output.issues.append(FARIssue(
                step="STEP_7_area_basis",
                field="value_sf",
                severity="error",
                message="Area basis cannot be determined — no lot area available.",
            ))

        return ab

    # ── STEP 8 ──────────────────────────────────────────────────────────

    def _step8_allowable(
        self,
        baseline: BaselineFAR,
        local_controls: LocalControls,
        incentive: IncentiveInfo,
        area_basis: AreaBasis,
        governing_far: GoverningFAR,
        output: FAROutput,
    ) -> AllowableFloorArea:
        aw = AllowableFloorArea()
        area = area_basis.value_sf

        # Track A — Baseline
        if baseline.ratio is not None and area is not None:
            aw.baseline_far_ratio = baseline.ratio
            aw.baseline_floor_area_sf = baseline.ratio * area

        # Track B — Locally Modified
        local_ratio = local_controls.cpio_far or local_controls.specific_plan_far or local_controls.d_far_cap
        if local_ratio is not None and area is not None:
            aw.locally_modified_far_ratio = local_ratio
            aw.locally_modified_floor_area_sf = local_ratio * area

        # Track C — Incentive Modified
        if incentive.modified_far is not None and area is not None:
            aw.incentive_far_ratio = incentive.modified_far
            aw.incentive_floor_area_sf = incentive.modified_far * area

        # Governing
        if governing_far.applicable_ratio is not None and area is not None:
            aw.governing_floor_area_sf = governing_far.applicable_ratio * area
            aw.governing_source = governing_far.source_citation

        return aw

    # ── STEP 9 ──────────────────────────────────────────────────────────

    def _step9_proposed(
        self,
        project: Project,
        area_basis: AreaBasis,
        allowable: AllowableFloorArea,
        floor_area_def: FloorAreaDefinition,
        output: FAROutput,
    ) -> ProposedFAR:
        prop = ProposedFAR()
        prop.floor_area_definition_used = floor_area_def.source_citation

        # ── Check definition alignment ──────────────────────────────
        if project.floor_area_definition_used and floor_area_def.source_citation:
            proj_def = project.floor_area_definition_used.strip().lower()
            gov_def = floor_area_def.source_citation.strip().lower()
            if proj_def and gov_def:
                prop.definition_aligned = proj_def == gov_def
                if not prop.definition_aligned:
                    prop.numerator_issues.append(
                        f"Definition mismatch: project uses '{project.floor_area_definition_used}' "
                        f"but governing authority uses '{floor_area_def.source_citation}'."
                    )
                    output.issues.append(FARIssue(
                        step="STEP_9_proposed_far",
                        field="floor_area_definition_used",
                        severity="warning",
                        message=(
                            f"Floor area definition mismatch: project counted area uses "
                            f"'{project.floor_area_definition_used}' but governing authority "
                            f"requires '{floor_area_def.source_citation}'. "
                            f"Proposed FAR may not be comparable to allowable FAR."
                        ),
                        action_required="Verify that counted floor area uses the same definition as the governing authority",
                    ))

        # ── Path A: explicit total provided by architect ────────────
        if project.counted_floor_area_sf is not None:
            prop.numerator_source = "explicit_total"
            prop.counted_floor_area_sf = project.counted_floor_area_sf
            prop.numerator_confidence = "high"

        # ── Path B: per-floor breakdown provided ────────────────────
        elif project.floor_area_entries:
            prop.numerator_source = "per_floor_entries"
            total_gross = 0.0
            total_counted = 0.0
            total_excluded = 0.0

            for entry in project.floor_area_entries:
                total_gross += entry.gross_area_sf
                total_counted += entry.counted_area_sf
                total_excluded += entry.excluded_area_sf

                prop.per_floor_breakdown.append(FloorAreaBreakdownEntry(
                    floor_level=entry.floor_level,
                    label=entry.label,
                    gross_area_sf=entry.gross_area_sf,
                    counted_area_sf=entry.counted_area_sf,
                    excluded_area_sf=entry.excluded_area_sf,
                    exclusion_reason=entry.exclusion_reason,
                ))

            prop.gross_floor_area_sf = total_gross
            prop.counted_floor_area_sf = total_counted
            prop.excluded_floor_area_sf = total_excluded
            prop.numerator_confidence = "high"

            # Build exclusion breakdown (aggregate by reason)
            exclusion_map: dict[str, float] = {}
            for entry in project.floor_area_entries:
                if entry.excluded_area_sf > 0:
                    reason = entry.exclusion_reason or "unspecified"
                    exclusion_map[reason] = exclusion_map.get(reason, 0) + entry.excluded_area_sf

            for reason, area in exclusion_map.items():
                prop.exclusion_breakdown.append(FloorAreaBreakdownEntry(
                    floor_level="(all)",
                    label=reason,
                    excluded_area_sf=area,
                    exclusion_reason=reason,
                ))

            # Sanity check: gross should ≈ counted + excluded
            if total_gross > 0:
                residual = abs(total_gross - total_counted - total_excluded)
                if residual > 1.0:
                    prop.numerator_issues.append(
                        f"Gross ({total_gross:.0f}) != counted ({total_counted:.0f}) + "
                        f"excluded ({total_excluded:.0f}). Residual: {residual:.0f} SF."
                    )
                    prop.numerator_confidence = "medium"

        # ── Path C: no counted floor area data ──────────────────────
        else:
            prop.numerator_source = "unresolved"
            prop.numerator_confidence = "low"
            missing_reasons = []
            if not project.counted_floor_area_sf and not project.floor_area_entries:
                missing_reasons.append("No counted floor area provided (neither total nor per-floor breakdown)")
            if project.occupancy_areas:
                missing_reasons.append(
                    "occupancy_areas present but NOT used for FAR numerator — "
                    "occupancy areas (building code) ≠ counted floor area (zoning code)"
                )
            prop.numerator_issues = missing_reasons

            output.issues.append(FARIssue(
                step="STEP_9_proposed_far",
                field="counted_floor_area_sf",
                severity="warning",
                message=(
                    "Proposed FAR cannot be computed: no counted floor area provided. "
                    "Provide either project.counted_floor_area_sf or project.floor_area_entries."
                ),
                action_required=(
                    "Enter counted floor area from architect's FAR plan. "
                    "Do not use occupancy areas — they use a different definition."
                ),
            ))
            return prop

        # ── Compute proposed FAR ────────────────────────────────────
        if prop.counted_floor_area_sf is not None and area_basis.value_sf:
            prop.area_basis_used_sf = area_basis.value_sf
            prop.far_ratio = prop.counted_floor_area_sf / area_basis.value_sf

            if allowable.governing_floor_area_sf is not None:
                prop.compliant = prop.counted_floor_area_sf <= allowable.governing_floor_area_sf
                prop.margin_sf = allowable.governing_floor_area_sf - prop.counted_floor_area_sf

        return prop

    # ── STEP 10 ─────────────────────────────────────────────────────────

    def _step10_outcome(self, output: FAROutput, min_confidence: str) -> FAROutcome:
        oc = FAROutcome()
        gov = output.governing_far

        # Collect all issue messages
        oc.issues = [i.message for i in output.issues]

        # Apply confidence cascade
        oc.confidence = _min_conf(min_confidence, gov.confidence)

        # Determine state
        if gov.state == "baseline" and not output.baseline_far.is_provisional:
            oc.state = "baseline_confirmed"
        elif gov.state == "baseline" and output.baseline_far.is_provisional:
            oc.state = "baseline_with_override_risk"
        elif gov.state == "locally_modified":
            oc.state = "locally_modified_confirmed"
        elif gov.state == "incentive_modified":
            oc.state = "incentive_modified_confirmed"
        elif gov.state == "unresolved":
            oc.state = "unresolved"
        else:
            oc.state = "unresolved"

        # Manual review determination
        review_reasons: list[str] = []
        if output.local_controls.override_present:
            if output.local_controls.specific_plan and output.local_controls.specific_plan_document_status != "downloaded_and_parsed":
                review_reasons.append(f"Specific Plan '{output.local_controls.specific_plan}' not reviewed")
            if output.local_controls.cpio and output.local_controls.cpio_document_status != "downloaded_and_parsed":
                review_reasons.append(f"CPIO '{output.local_controls.cpio}' not reviewed")
            if output.local_controls.d_limitation and output.local_controls.d_document_status != "downloaded_and_parsed":
                review_reasons.append(f"D ordinance not reviewed")
        if output.parcel.multi_parcel:
            review_reasons.append("Multiple parcels — confirm lot tie")
        if output.parcel.identity_confidence == "provisional":
            review_reasons.append("Lot area discrepancy between ZIMAS and survey")

        oc.requires_manual_review = len(review_reasons) > 0 or oc.confidence != "high"
        oc.manual_review_reasons = review_reasons

        return oc

    # ── Legacy Conversion ───────────────────────────────────────────────

    def _convert_to_legacy(
        self, far_output: FAROutput, site: Site
    ) -> tuple[list[CalcResult], list[ReviewIssue]]:
        """Convert FAROutput to the CalcResult/ReviewIssue format used by the rest of the system."""
        results: list[CalcResult] = []
        issues: list[ReviewIssue] = []

        gov = far_output.governing_far
        bl = far_output.baseline_far
        aw = far_output.allowable
        ab = far_output.area_basis

        # max_far result
        if gov.applicable_ratio is not None:
            results.append(self._make_result(
                "max_far",
                gov.applicable_ratio,
                unit="ratio",
                formula=gov.source_citation,
                confidence=gov.confidence,
                inputs_used={
                    "base_zone": far_output.zoning.base_zone,
                    "height_district": far_output.zoning.height_district,
                    "governing_state": gov.state,
                },
                intermediate_steps=gov.authority_chain,
                review_notes=(
                    [f"Baseline FAR: {bl.ratio}:1 ({bl.source})"] if bl.ratio else []
                ) + (
                    [f"Outcome: {far_output.outcome.state}"]
                ),
            ))

        # max_floor_area result
        if aw.governing_floor_area_sf is not None:
            steps = []
            if bl.ratio is not None and aw.baseline_floor_area_sf is not None:
                steps.append(f"Track A (baseline): {bl.ratio}:1 x {ab.value_sf:.0f} sf = {aw.baseline_floor_area_sf:.0f} sf")
            if aw.locally_modified_far_ratio is not None and aw.locally_modified_floor_area_sf is not None:
                steps.append(f"Track B (local): {aw.locally_modified_far_ratio}:1 x {ab.value_sf:.0f} sf = {aw.locally_modified_floor_area_sf:.0f} sf")
            if aw.incentive_far_ratio is not None and aw.incentive_floor_area_sf is not None:
                steps.append(f"Track C (incentive): {aw.incentive_far_ratio}:1 x {ab.value_sf:.0f} sf = {aw.incentive_floor_area_sf:.0f} sf")
            steps.append(f"Governing: {gov.applicable_ratio}:1 -> {aw.governing_floor_area_sf:.0f} sf")

            results.append(self._make_result(
                "max_floor_area",
                aw.governing_floor_area_sf,
                unit="sf",
                formula=f"{gov.applicable_ratio} x {ab.value_sf:.0f}",
                confidence=gov.confidence,
                inputs_used={
                    "far_ratio": gov.applicable_ratio,
                    "area_basis": ab.value_sf,
                    "area_basis_type": ab.type,
                    "base_zone": far_output.zoning.base_zone,
                    "height_district": far_output.zoning.height_district,
                },
                intermediate_steps=steps,
                review_notes=[f"Incentive FAR bonuses NOT included. See advisory screens."]
                if far_output.incentive.pathway else [],
            ))

        # baseline_far result (always output separately)
        if bl.ratio is not None:
            results.append(self._make_result(
                "baseline_far",
                bl.ratio,
                unit="ratio",
                formula=bl.source,
                confidence="high" if not bl.is_provisional else "medium",
                inputs_used={
                    "zone_row": bl.zone_row_used,
                    "hd_column": bl.height_district_column_used,
                },
                review_notes=[bl.note] if bl.note else [],
            ))

        # Convert FARIssues to ReviewIssues
        for fi in far_output.issues:
            severity_map = {"error": "critical", "warning": "high", "info": "medium"}
            issues.append(ReviewIssue(
                id=f"CALC-FAR-{fi.step}",
                category="far",
                severity=severity_map.get(fi.severity, "medium"),
                title=fi.message[:80],
                description=fi.message,
                affected_fields=[fi.field],
                suggested_review_role="zoning consultant",
                blocking=fi.severity == "error",
            ))

        return results, issues


# ── Helpers ─────────────────────────────────────────────────────────────

_CONF_ORDER = {"high": 2, "medium": 1, "low": 0}


def _min_conf(a: str, b: str) -> str:
    """Return the lower of two confidence levels."""
    va = _CONF_ORDER.get(a, 0)
    vb = _CONF_ORDER.get(b, 0)
    target = min(va, vb)
    for label, val in _CONF_ORDER.items():
        if val == target:
            return label
    return "low"


def _zone_to_class(zone: str) -> str:
    """Map a base zone to its broad class."""
    if not zone:
        return "other"
    z = zone.upper()
    if z.startswith("R") or z.startswith("RAS") or z.startswith("RD"):
        return "residential"
    if z.startswith("C"):
        return "commercial"
    if z.startswith("M") or z == "CM":
        return "manufacturing"
    return "other"
