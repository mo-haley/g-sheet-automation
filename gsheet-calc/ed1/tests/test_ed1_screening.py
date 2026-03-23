"""Tests for ED1 screening logic.

Tests exercise the pure screener (screen_ed1) and the orchestrator
adapter (run_ed1_module) to verify:
- correct blocker detection for each memo section
- correct missing-input surfacing when inputs are unknown
- correct status/confidence derivation
- correct obligation / warning / constraint generation
- no false positives or silent assumptions
"""

from __future__ import annotations

import pytest

from ed1.models import (
    ED1Confidence,
    ED1Input,
    ED1Status,
    EnvironmentalSiteStatus,
    HistoricResourceStatus,
)
from ed1.screener import screen_ed1
from ed1.ed1_orchestrator import (
    build_ed1_input,
    run_ed1_module,
    _classify_zone,
)
from models.site import Site
from models.project import Project, AffordabilityPlan


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fully_eligible_input() -> ED1Input:
    """An ED1Input that should pass all screening gates."""
    return ED1Input(
        is_100_percent_affordable=True,
        requires_zone_change=False,
        requires_variance=False,
        requires_general_plan_amendment=False,
        base_zone="R3",
        zoning_is_single_family_or_more_restrictive=False,
        manufacturing_zone_disallows_multifamily=False,
        residential_pre_bonus_allowed_units=10,
        vhfhsz_flag=False,
        hillside_area_flag=False,
        hazardous_site_status=EnvironmentalSiteStatus.NOT_PRESENT,
        oil_well_site_status=EnvironmentalSiteStatus.NOT_PRESENT,
        historic_resource_status=HistoricResourceStatus.NOT_IDENTIFIED,
        protected_plan_area_historic_check_complete=True,
        rso_subject_site=False,
        rso_total_units=None,
        occupied_units_within_5_years=None,
        replacement_unit_trigger=False,
        public_subsidy_covenant_exception_flag=False,
        is_residential_zone=True,
        is_commercial_zone=False,
        is_residential_land_use_designation=True,
    )


def _minimal_site(zone: str = "R3") -> Site:
    return Site(address="123 Test St", zone=zone)


def _affordable_project() -> Project:
    return Project(
        project_name="Test Affordable",
        project_type="100_pct_affordable",
        affordability=AffordabilityPlan(
            eli_pct=0.5, vli_pct=0.3, li_pct=0.2, market_pct=0.0,
        ),
    )


def _market_rate_project() -> Project:
    return Project(
        project_name="Test Market",
        project_type="market_rate",
    )


# ── FULLY ELIGIBLE ──────────────────────────────────────────────────────────

class TestFullyEligible:
    def test_likely_eligible_status(self):
        result = screen_ed1(_fully_eligible_input())
        assert result.status == ED1Status.LIKELY_ELIGIBLE
        assert result.confidence == ED1Confidence.HIGH
        assert len(result.blockers) == 0
        assert len(result.missing_inputs) == 0

    def test_has_procedural_benefits(self):
        result = screen_ed1(_fully_eligible_input())
        assert len(result.procedural_benefits) >= 3

    def test_has_obligations(self):
        result = screen_ed1(_fully_eligible_input())
        assert any("covenant" in o.lower() for o in result.obligations)

    def test_has_incentive_constraints(self):
        result = screen_ed1(_fully_eligible_input())
        assert len(result.incentive_constraints) >= 5

    def test_has_design_warnings(self):
        result = screen_ed1(_fully_eligible_input())
        assert any("parking screening" in w.lower() for w in result.warnings)
        assert any("pedestrian entrance" in w.lower() for w in result.warnings)
        assert any("glazing" in w.lower() for w in result.warnings)

    def test_comparison_populated(self):
        result = screen_ed1(_fully_eligible_input())
        assert result.comparison_to_baseline.review_pathway != ""
        assert "likely viable" in result.comparison_to_baseline.overall_assessment.lower()

    def test_source_basis(self):
        result = screen_ed1(_fully_eligible_input())
        assert "July 1, 2024" in result.source_basis

    def test_screening_disclaimer(self):
        result = screen_ed1(_fully_eligible_input())
        assert "not a legal determination" in result.screening_disclaimer


# ── AFFORDABILITY BLOCKER ───────────────────────────────────────────────────

class TestAffordabilityBlocker:
    def test_not_affordable_blocks(self):
        inp = _fully_eligible_input()
        inp.is_100_percent_affordable = False
        result = screen_ed1(inp)
        assert result.status == ED1Status.LIKELY_INELIGIBLE
        assert any("not 100% affordable" in b.lower() for b in result.blockers)

    def test_unknown_affordability_missing(self):
        inp = _fully_eligible_input()
        inp.is_100_percent_affordable = None
        result = screen_ed1(inp)
        assert any("is_100_percent_affordable" in m for m in result.missing_inputs)


# ── DISCRETIONARY TRIGGERS ──────────────────────────────────────────────────

class TestDiscretionaryTriggers:
    def test_zone_change_blocks(self):
        inp = _fully_eligible_input()
        inp.requires_zone_change = True
        result = screen_ed1(inp)
        assert result.status == ED1Status.LIKELY_INELIGIBLE
        assert any("zoning change" in b.lower() for b in result.blockers)

    def test_variance_blocks(self):
        inp = _fully_eligible_input()
        inp.requires_variance = True
        result = screen_ed1(inp)
        assert any("variance" in b.lower() for b in result.blockers)

    def test_gpa_blocks(self):
        inp = _fully_eligible_input()
        inp.requires_general_plan_amendment = True
        result = screen_ed1(inp)
        assert any("general plan amendment" in b.lower() for b in result.blockers)

    def test_unknown_discretionary_triggers_missing(self):
        inp = _fully_eligible_input()
        inp.requires_zone_change = None
        inp.requires_variance = None
        inp.requires_general_plan_amendment = None
        result = screen_ed1(inp)
        assert any("requires_zone_change" in m for m in result.missing_inputs)
        assert any("requires_variance" in m for m in result.missing_inputs)
        assert any("requires_general_plan_amendment" in m for m in result.missing_inputs)


# ── ZONE CLASSIFICATION ─────────────────────────────────────────────────────

class TestZoneClassification:
    def test_single_family_blocks(self):
        inp = _fully_eligible_input()
        inp.zoning_is_single_family_or_more_restrictive = True
        result = screen_ed1(inp)
        assert any("single-family" in b.lower() for b in result.blockers)

    def test_manufacturing_no_multifamily_blocks(self):
        inp = _fully_eligible_input()
        inp.manufacturing_zone_disallows_multifamily = True
        result = screen_ed1(inp)
        assert any("manufacturing zone" in b.lower() for b in result.blockers)


# ── ENVIRONMENTAL ───────────────────────────────────────────────────────────

class TestEnvironmental:
    def test_hazardous_present_not_cleared_blocks(self):
        inp = _fully_eligible_input()
        inp.hazardous_site_status = EnvironmentalSiteStatus.PRESENT_NOT_CLEARED
        result = screen_ed1(inp)
        assert any("hazardous waste" in b.lower() for b in result.blockers)

    def test_hazardous_cleared_warns(self):
        inp = _fully_eligible_input()
        inp.hazardous_site_status = EnvironmentalSiteStatus.CLEARED
        result = screen_ed1(inp)
        assert result.status == ED1Status.LIKELY_ELIGIBLE
        assert any("hazardous waste" in w.lower() for w in result.warnings)

    def test_hazardous_not_present_clean(self):
        inp = _fully_eligible_input()
        inp.hazardous_site_status = EnvironmentalSiteStatus.NOT_PRESENT
        result = screen_ed1(inp)
        assert not any("hazardous" in b.lower() for b in result.blockers)

    def test_oil_well_present_not_cleared_blocks(self):
        inp = _fully_eligible_input()
        inp.oil_well_site_status = EnvironmentalSiteStatus.PRESENT_NOT_CLEARED
        result = screen_ed1(inp)
        assert any("gas or oil well" in b.lower() for b in result.blockers)

    def test_oil_well_cleared_warns(self):
        inp = _fully_eligible_input()
        inp.oil_well_site_status = EnvironmentalSiteStatus.CLEARED
        result = screen_ed1(inp)
        assert result.status == ED1Status.LIKELY_ELIGIBLE
        assert any("gas/oil well" in w.lower() for w in result.warnings)

    def test_unknown_environmental_missing(self):
        inp = _fully_eligible_input()
        inp.hazardous_site_status = EnvironmentalSiteStatus.UNKNOWN
        inp.oil_well_site_status = EnvironmentalSiteStatus.UNKNOWN
        result = screen_ed1(inp)
        assert any("hazardous_site_status" in m for m in result.missing_inputs)
        assert any("oil_well_site_status" in m for m in result.missing_inputs)


# ── FIRE / HILLSIDE ─────────────────────────────────────────────────────────

class TestFireHillside:
    def test_vhfhsz_and_hillside_blocks(self):
        """§1.D: Blocker is the intersection, not either alone."""
        inp = _fully_eligible_input()
        inp.vhfhsz_flag = True
        inp.hillside_area_flag = True
        result = screen_ed1(inp)
        assert any("very high fire" in b.lower() for b in result.blockers)

    def test_vhfhsz_alone_does_not_block(self):
        inp = _fully_eligible_input()
        inp.vhfhsz_flag = True
        inp.hillside_area_flag = False
        result = screen_ed1(inp)
        assert not any("fire" in b.lower() for b in result.blockers)

    def test_hillside_alone_does_not_block(self):
        inp = _fully_eligible_input()
        inp.vhfhsz_flag = False
        inp.hillside_area_flag = True
        result = screen_ed1(inp)
        assert not any("fire" in b.lower() for b in result.blockers)

    def test_unknown_fire_missing(self):
        inp = _fully_eligible_input()
        inp.vhfhsz_flag = None
        inp.hillside_area_flag = None
        result = screen_ed1(inp)
        assert any("vhfhsz_flag" in m for m in result.missing_inputs)
        assert any("hillside_area_flag" in m for m in result.missing_inputs)


# ── HISTORIC ────────────────────────────────────────────────────────────────

class TestHistoric:
    def test_designated_blocks(self):
        inp = _fully_eligible_input()
        inp.historic_resource_status = HistoricResourceStatus.DESIGNATED_OR_LISTED
        result = screen_ed1(inp)
        assert any("historic resource" in b.lower() for b in result.blockers)

    def test_not_identified_clean(self):
        inp = _fully_eligible_input()
        inp.historic_resource_status = HistoricResourceStatus.NOT_IDENTIFIED
        result = screen_ed1(inp)
        assert not any("historic" in b.lower() for b in result.blockers)

    def test_unknown_historic_missing(self):
        inp = _fully_eligible_input()
        inp.historic_resource_status = HistoricResourceStatus.UNKNOWN
        result = screen_ed1(inp)
        assert any("historic_resource_status" in m for m in result.missing_inputs)

    def test_plan_area_check_incomplete_warns(self):
        inp = _fully_eligible_input()
        inp.protected_plan_area_historic_check_complete = False
        result = screen_ed1(inp)
        assert any("plan area" in w.lower() for w in result.warnings)

    def test_plan_area_check_none_missing(self):
        inp = _fully_eligible_input()
        inp.protected_plan_area_historic_check_complete = None
        result = screen_ed1(inp)
        assert any("protected_plan_area" in m for m in result.missing_inputs)


# ── RSO ─────────────────────────────────────────────────────────────────────

class TestRSO:
    def test_rso_12_plus_occupied_blocks(self):
        inp = _fully_eligible_input()
        inp.rso_subject_site = True
        inp.rso_total_units = 15
        inp.occupied_units_within_5_years = True
        result = screen_ed1(inp)
        assert result.status == ED1Status.LIKELY_INELIGIBLE
        assert any("12 or more" in b.lower() for b in result.blockers)

    def test_rso_12_plus_not_occupied_no_block(self):
        inp = _fully_eligible_input()
        inp.rso_subject_site = True
        inp.rso_total_units = 15
        inp.occupied_units_within_5_years = False
        result = screen_ed1(inp)
        assert not any("12 or more" in b.lower() for b in result.blockers)

    def test_rso_under_12_no_block(self):
        inp = _fully_eligible_input()
        inp.rso_subject_site = True
        inp.rso_total_units = 8
        inp.occupied_units_within_5_years = True
        result = screen_ed1(inp)
        assert not any("12 or more" in b.lower() for b in result.blockers)

    def test_rso_triggers_obligations(self):
        inp = _fully_eligible_input()
        inp.rso_subject_site = True
        inp.rso_total_units = 6
        result = screen_ed1(inp)
        assert any("rso replacement" in o.lower() for o in result.obligations)
        assert any("right of first refusal" in o.lower() for o in result.obligations)
        assert any("security deposit" in o.lower() for o in result.obligations)

    def test_unknown_rso_missing(self):
        inp = _fully_eligible_input()
        inp.rso_subject_site = None
        result = screen_ed1(inp)
        assert any("rso_subject_site" in m for m in result.missing_inputs)

    def test_rso_12_plus_unknown_occupancy_missing(self):
        inp = _fully_eligible_input()
        inp.rso_subject_site = True
        inp.rso_total_units = 14
        inp.occupied_units_within_5_years = None
        result = screen_ed1(inp)
        assert any("occupied_units_within_5_years" in m for m in result.missing_inputs)


# ── RESIDENTIAL CAPACITY ────────────────────────────────────────────────────

class TestResidentialCapacity:
    def test_under_5_units_blocks(self):
        inp = _fully_eligible_input()
        inp.is_residential_zone = True
        inp.residential_pre_bonus_allowed_units = 3
        result = screen_ed1(inp)
        assert any("5 units" in b for b in result.blockers)

    def test_5_plus_units_ok(self):
        inp = _fully_eligible_input()
        inp.is_residential_zone = True
        inp.residential_pre_bonus_allowed_units = 5
        result = screen_ed1(inp)
        assert not any("5 units" in b for b in result.blockers)

    def test_non_residential_zone_skips(self):
        inp = _fully_eligible_input()
        inp.is_residential_zone = False
        inp.residential_pre_bonus_allowed_units = 2
        result = screen_ed1(inp)
        assert not any("5 units" in b for b in result.blockers)

    def test_unknown_capacity_missing(self):
        inp = _fully_eligible_input()
        inp.is_residential_zone = True
        inp.residential_pre_bonus_allowed_units = None
        result = screen_ed1(inp)
        assert any("residential_pre_bonus_allowed_units" in m for m in result.missing_inputs)


# ── COVENANT ────────────────────────────────────────────────────────────────

class TestCovenant:
    def test_default_99_year_covenant(self):
        inp = _fully_eligible_input()
        inp.public_subsidy_covenant_exception_flag = False
        result = screen_ed1(inp)
        assert any("99 years" in o for o in result.obligations)

    def test_subsidy_exception_covenant(self):
        inp = _fully_eligible_input()
        inp.public_subsidy_covenant_exception_flag = True
        result = screen_ed1(inp)
        assert any("55 years" in o for o in result.obligations)

    def test_unknown_subsidy_mentions_both(self):
        inp = _fully_eligible_input()
        inp.public_subsidy_covenant_exception_flag = None
        result = screen_ed1(inp)
        assert any("99 years" in o and "55 years" in o for o in result.obligations)

    def test_adu_conversion_covenant(self):
        result = screen_ed1(_fully_eligible_input())
        assert any("adu" in o.lower() for o in result.obligations)


# ── STATUS DERIVATION ───────────────────────────────────────────────────────

class TestStatusDerivation:
    def test_blockers_with_missing_medium_confidence(self):
        inp = _fully_eligible_input()
        inp.requires_zone_change = True
        inp.hazardous_site_status = None
        result = screen_ed1(inp)
        assert result.status == ED1Status.LIKELY_INELIGIBLE
        assert result.confidence == ED1Confidence.MEDIUM

    def test_blockers_no_missing_high_confidence(self):
        inp = _fully_eligible_input()
        inp.is_100_percent_affordable = False
        result = screen_ed1(inp)
        assert result.status == ED1Status.LIKELY_INELIGIBLE
        assert result.confidence == ED1Confidence.HIGH

    def test_many_critical_missing_insufficient(self):
        inp = ED1Input()  # Everything unknown
        result = screen_ed1(inp)
        assert result.status == ED1Status.INSUFFICIENT_INFORMATION
        assert result.confidence == ED1Confidence.LOW

    def test_some_missing_potentially_eligible(self):
        inp = _fully_eligible_input()
        inp.hazardous_site_status = None  # Non-critical missing
        inp.oil_well_site_status = None
        result = screen_ed1(inp)
        assert result.status == ED1Status.POTENTIALLY_ELIGIBLE

    def test_multiple_blockers_all_counted(self):
        inp = _fully_eligible_input()
        inp.is_100_percent_affordable = False
        inp.requires_zone_change = True
        inp.zoning_is_single_family_or_more_restrictive = True
        result = screen_ed1(inp)
        assert len(result.blockers) >= 3


# ── ZONE CLASSIFIER ────────────────────────────────────────────────────────

class TestZoneClassifier:
    def test_r1_single_family(self):
        info = _classify_zone("R1")
        assert info["is_single_family"] is True
        assert info["is_residential"] is True

    def test_r3_multifamily(self):
        info = _classify_zone("R3")
        assert info["is_single_family"] is False
        assert info["is_residential"] is True

    def test_c2_commercial(self):
        info = _classify_zone("C2")
        assert info["is_commercial"] is True
        assert info["is_single_family"] is False

    def test_m1_manufacturing_unknown(self):
        """M1 is recognized but multifamily status is unknown — needs confirmation."""
        info = _classify_zone("M1")
        assert info["mfg_disallows_mf"] is None
        # M1 is recognized (not all-None), just the mfg flag is unknown
        assert info["is_single_family"] is False

    def test_mr1_allows_multifamily(self):
        """MR1 is manufacturing-residential — explicitly allows multifamily."""
        info = _classify_zone("MR1")
        assert info["mfg_disallows_mf"] is False

    def test_mr2_allows_multifamily(self):
        """MR2 is manufacturing-residential — explicitly allows multifamily."""
        info = _classify_zone("MR2")
        assert info["mfg_disallows_mf"] is False

    def test_unknown_zone_returns_none(self):
        info = _classify_zone("ZXYZ99")
        assert info["is_single_family"] is None
        assert info["is_residential"] is None

    def test_pf_zone_returns_none(self):
        """Public Facilities zone is unrecognized — all flags None."""
        info = _classify_zone("PF-1")
        assert info["is_single_family"] is None
        assert info["is_residential"] is None
        assert info["mfg_disallows_mf"] is None
        assert info["is_commercial"] is None

    def test_none_zone_returns_none(self):
        info = _classify_zone(None)
        assert info["is_single_family"] is None

    def test_height_district_stripped(self):
        info = _classify_zone("R3-1")
        assert info["is_residential"] is True
        assert info["is_single_family"] is False


# ── ORCHESTRATOR ADAPTER ────────────────────────────────────────────────────

class TestOrchestrator:
    def test_build_ed1_input_from_site_project(self):
        site = _minimal_site("R3")
        project = _affordable_project()
        ed1_input = build_ed1_input(site, project)
        assert ed1_input.is_100_percent_affordable is True
        assert ed1_input.base_zone == "R3"
        assert ed1_input.is_residential_zone is True

    def test_build_ed1_input_market_rate(self):
        site = _minimal_site("R3")
        project = _market_rate_project()
        ed1_input = build_ed1_input(site, project)
        assert ed1_input.is_100_percent_affordable is None

    def test_overrides_take_precedence(self):
        site = _minimal_site("R3")
        project = _affordable_project()
        overrides = ED1Input(requires_zone_change=True)
        ed1_input = build_ed1_input(site, project, overrides=overrides)
        assert ed1_input.requires_zone_change is True

    def test_override_does_not_clobber_derived(self):
        """Override one field without resetting other derived values."""
        site = _minimal_site("R3")
        site.hillside_area = True
        project = _affordable_project()
        overrides = ED1Input(requires_zone_change=False)
        ed1_input = build_ed1_input(site, project, overrides=overrides)
        # Override applied
        assert ed1_input.requires_zone_change is False
        # Derived values preserved
        assert ed1_input.is_100_percent_affordable is True
        assert ed1_input.is_residential_zone is True
        assert ed1_input.hillside_area_flag is True
        assert ed1_input.base_zone == "R3"

    def test_run_ed1_module_returns_module_result(self):
        site = _minimal_site("R3")
        project = _affordable_project()
        result = run_ed1_module(site, project)
        assert result.module == "ed1"
        assert result.module_version == "v1"
        assert result.module_payload["status"] in {
            s.value for s in ED1Status
        }

    def test_module_result_has_citation(self):
        site = _minimal_site("R3")
        project = _affordable_project()
        result = run_ed1_module(site, project)
        assert any("ED1" in c.label or "ed1" in c.id for c in result.citations)

    def test_fire_hazard_mapping(self):
        site = _minimal_site("R3")
        site.fire_hazard_zone = "Very High Fire Hazard Severity Zone"
        site.hillside_area = True
        project = _affordable_project()
        ed1_input = build_ed1_input(site, project)
        assert ed1_input.vhfhsz_flag is True
        assert ed1_input.hillside_area_flag is True

    def test_historic_status_mapping(self):
        site = _minimal_site("R3")
        site.historic_status = "City Historic-Cultural Monument"
        project = _affordable_project()
        ed1_input = build_ed1_input(site, project)
        assert ed1_input.historic_resource_status == HistoricResourceStatus.DESIGNATED_OR_LISTED


# ── EMPTY INPUT (WORST CASE) ────────────────────────────────────────────────

class TestEmptyInput:
    def test_empty_input_does_not_crash(self):
        result = screen_ed1(ED1Input())
        assert result.status in {
            ED1Status.INSUFFICIENT_INFORMATION,
            ED1Status.POTENTIALLY_ELIGIBLE,
        }
        assert len(result.missing_inputs) > 0

    def test_empty_input_has_no_blockers(self):
        result = screen_ed1(ED1Input())
        assert len(result.blockers) == 0

    def test_empty_input_still_has_benefits(self):
        result = screen_ed1(ED1Input())
        assert len(result.procedural_benefits) >= 3

    def test_empty_input_still_has_constraints(self):
        """No blockers (all unknown) → full constraints surfaced."""
        result = screen_ed1(ED1Input())
        assert len(result.incentive_constraints) >= 5


# ── BLOCKER GATING (output noise reduction) ─────────────────────────────────

class TestBlockerGating:
    """When blockers exist, design/constraint/benefit lists should be
    condensed reference notes, not full active-apply lists."""

    def test_blocked_project_no_full_design_warnings(self):
        inp = _fully_eligible_input()
        inp.is_100_percent_affordable = False
        result = screen_ed1(inp)
        assert result.status == ED1Status.LIKELY_INELIGIBLE
        # Should NOT have the full "Parking screening required" warning
        assert not any("parking screening required" in w.lower() for w in result.warnings)
        # Should have a condensed reference note
        assert any("would apply if" in w.lower() for w in result.warnings)

    def test_blocked_project_no_full_constraints(self):
        inp = _fully_eligible_input()
        inp.requires_zone_change = True
        result = screen_ed1(inp)
        # Should NOT have 8+ individual constraints
        assert len(result.incentive_constraints) <= 2
        assert any("would apply if" in c.lower() for c in result.incentive_constraints)

    def test_blocked_project_no_full_benefits(self):
        inp = _fully_eligible_input()
        inp.requires_variance = True
        result = screen_ed1(inp)
        assert len(result.procedural_benefits) <= 2
        assert any("would apply if" in b.lower() for b in result.procedural_benefits)

    def test_blocked_project_still_has_covenant(self):
        """Covenant obligation is always present — helps explain ED1 weight."""
        inp = _fully_eligible_input()
        inp.is_100_percent_affordable = False
        result = screen_ed1(inp)
        assert any("covenant" in o.lower() for o in result.obligations)

    def test_eligible_project_has_full_warnings(self):
        result = screen_ed1(_fully_eligible_input())
        assert any("parking screening required" in w.lower() for w in result.warnings)
        assert any("pedestrian entrance required" in w.lower() for w in result.warnings)
        assert len(result.incentive_constraints) >= 5
        assert len(result.procedural_benefits) >= 3


# ── MODULE RESULT BLOCKING FLAG ─────────────────────────────────────────────

class TestModuleResultBlockingFlag:
    def test_likely_ineligible_sets_blocking(self):
        site = _minimal_site("R3")
        project = _affordable_project()
        overrides = ED1Input(
            is_100_percent_affordable=False,
            requires_zone_change=False,
            requires_variance=False,
            requires_general_plan_amendment=False,
            zoning_is_single_family_or_more_restrictive=False,
            manufacturing_zone_disallows_multifamily=False,
            hazardous_site_status=EnvironmentalSiteStatus.NOT_PRESENT,
            oil_well_site_status=EnvironmentalSiteStatus.NOT_PRESENT,
            historic_resource_status=HistoricResourceStatus.NOT_IDENTIFIED,
            protected_plan_area_historic_check_complete=True,
            rso_subject_site=False,
        )
        result = run_ed1_module(site, project, ed1_overrides=overrides)
        assert result.blocking is True

    def test_potentially_eligible_not_blocking(self):
        site = _minimal_site("R3")
        project = _affordable_project()
        result = run_ed1_module(site, project)
        # Without overrides, many inputs are missing → potentially_eligible
        assert result.blocking is False


# ── AFFORDABILITY DERIVATION EDGE CASES ─────────────────────────────────────

class TestAffordabilityDerivation:
    def test_affordable_type_no_plan_returns_none(self):
        """project_type='affordable' but no AffordabilityPlan → unknown."""
        project = Project(project_name="Test", project_type="100_pct_affordable")
        site = _minimal_site("R3")
        ed1_input = build_ed1_input(site, project)
        assert ed1_input.is_100_percent_affordable is None

    def test_market_type_zero_market_pct_returns_true(self):
        """market_pct=0.0 regardless of project_type → 100% affordable."""
        project = Project(
            project_name="Test",
            project_type="market_rate",
            affordability=AffordabilityPlan(
                eli_pct=1.0, market_pct=0.0,
            ),
        )
        site = _minimal_site("R3")
        ed1_input = build_ed1_input(site, project)
        assert ed1_input.is_100_percent_affordable is True

    def test_no_type_no_plan_returns_none(self):
        """No project_type hint, no affordability plan → unknown."""
        project = Project(project_name="Test")
        site = _minimal_site("R3")
        ed1_input = build_ed1_input(site, project)
        assert ed1_input.is_100_percent_affordable is None

    def test_default_affordability_plan_returns_true(self):
        """AffordabilityPlan() defaults all to 0.0 including market_pct.

        This is technically 'no market units' but may represent an empty
        form. The screener correctly returns True — document this posture.
        """
        project = Project(
            project_name="Test",
            affordability=AffordabilityPlan(),
        )
        site = _minimal_site("R3")
        ed1_input = build_ed1_input(site, project)
        assert ed1_input.is_100_percent_affordable is True


# ── COMPARISON PHRASING ─────────────────────────────────────────────────────

class TestComparisonPhrasing:
    def test_entitlement_friction_uses_intended(self):
        """Verify softened phrasing — 'is intended to' not 'eliminates'."""
        result = screen_ed1(_fully_eligible_input())
        assert "is intended to" in result.comparison_to_baseline.entitlement_friction
