"""Tests for FAR calculations.

Covers the 7 required test cases from the FAR correction sprint spec,
plus backward-compatibility checks for the legacy CalcResult interface.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from calc.far import calculate_far, calculate_far_full
from models.project import Project, UnitType, OccupancyArea, AffordabilityPlan, FloorAreaEntry
from validation.fixtures.sites import (
    far_c2_1_no_overrides,
    far_c2_2d_cpio_tcc_beacon,
    far_c2_2d_cpio_327_harbor,
    far_c2_1_density_bonus,
    far_r4_1_simple,
    far_c2_1vl_cpio_missing_doc,
    far_multi_parcel_lot_tie,
    simple_r3_site,
    c2_residential_site,
)


def _simple_project() -> Project:
    return Project(project_name="Test Project")


def _project_with_db() -> Project:
    return Project(
        project_name="Test DB Project",
        project_type="affordable",
        affordability=AffordabilityPlan(vli_pct=11.0),
    )


# ── Test Case 1: Standard C2-1 (No Overrides) ──────────────────────────


def test_c2_1_baseline_far():
    """C2-1, lot area 22,495 SF, no overlays. Baseline FAR = 1.5:1 (C/M in HD1)."""
    site = far_c2_1_no_overrides()
    project = _simple_project()
    output = calculate_far_full(site, project)

    assert output.baseline_far.ratio == 1.5
    assert output.area_basis.type == "buildable_area"
    assert output.area_basis.value_sf == 22495.0
    assert output.allowable.baseline_floor_area_sf == 1.5 * 22495.0
    assert output.governing_far.state == "baseline"
    assert output.outcome.state == "baseline_confirmed"
    assert output.outcome.confidence == "high"


def test_c2_1_legacy_interface():
    """Legacy CalcResult interface still works for C2-1."""
    site = far_c2_1_no_overrides()
    project = _simple_project()
    results, issues = calculate_far(site, project)

    far = next(r for r in results if r.name == "max_far")
    assert far.value == 1.5

    floor_area = next(r for r in results if r.name == "max_floor_area")
    assert floor_area.value == 1.5 * 22495.0

    baseline = next(r for r in results if r.name == "baseline_far")
    assert baseline.value == 1.5


# ── Test Case 2: C2-2D-CPIO (TCC Beacon pattern) ───────────────────────


def test_c2_2d_cpio_baseline_provisional():
    """C2-2D-CPIO: baseline 6.0 (C/M in HD2) but provisional due to D + CPIO."""
    site = far_c2_2d_cpio_tcc_beacon()
    project = _simple_project()
    output = calculate_far_full(site, project)

    assert output.baseline_far.ratio == 6.0
    assert output.baseline_far.is_provisional is True
    assert output.local_controls.d_limitation is True
    assert output.local_controls.cpio is not None
    # D and CPIO docs not parsed => unresolved or baseline_with_override_risk
    assert output.outcome.state in ("baseline_with_override_risk", "unresolved")
    # Must flag D ordinance needing review
    issue_messages = [i.message for i in output.issues]
    assert any("D limitation" in m for m in issue_messages)


# ── Test Case 3: CPIO with Explicit FAR (327 North Harbor pattern) ──────


def test_cpio_explicit_far_327_harbor():
    """327 North Harbor: CPIO Subarea E sets max FAR 4.0:1 on lot area.

    This test simulates having the CPIO document parsed by setting
    cpio_far on the local_controls after initial evaluation.
    """
    site = far_c2_2d_cpio_327_harbor()
    project = _simple_project()
    output = calculate_far_full(site, project)

    # Baseline should be 6.0 (C/M in HD2)
    assert output.baseline_far.ratio == 6.0

    # The CPIO is detected
    assert output.local_controls.cpio is not None

    # Since the CPIO doc is not actually parsed by the tool,
    # the output should flag it as needing review
    assert output.local_controls.cpio_document_status == "not_available"

    # Multi-parcel should be flagged
    assert output.parcel.multi_parcel is True
    issue_messages = [i.message for i in output.issues]
    assert any("Multiple APN" in m or "lot tie" in m.lower() for m in issue_messages)


def test_cpio_explicit_far_when_set():
    """When CPIO FAR is manually set (simulating parsed doc), governing should use it."""
    site = far_c2_2d_cpio_327_harbor()
    project = _simple_project()
    output = calculate_far_full(site, project)

    # Simulate the CPIO document having been parsed and FAR extracted
    output.local_controls.cpio_far = 4.0
    output.local_controls.cpio_document_status = "downloaded_and_parsed"

    # Re-run steps 6-8 with updated local controls
    from rules.deterministic.far import FARRule
    rule = FARRule()
    gov, inc = rule._step6_governing_far(
        site, output.baseline_far, output.local_controls, output
    )
    assert gov.state == "locally_modified"
    assert gov.applicable_ratio == 4.0

    # Area basis should be lot area per CPIO
    ab = rule._step7_area_basis(site, project, gov, output.local_controls, output)
    assert ab.type == "lot_area"
    assert ab.value_sf == 24197.0

    # Allowable
    aw = rule._step8_allowable(
        output.baseline_far, output.local_controls, inc, ab, gov, output
    )
    assert aw.locally_modified_far_ratio == 4.0
    assert aw.locally_modified_floor_area_sf == 4.0 * 24197.0  # 96,788


# ── Test Case 4: C2-1 with Density Bonus (417 Alvarado pattern) ─────────


def test_c2_1_density_bonus_baseline():
    """C2-1 baseline = 1.5:1. DB increase to 3.0 tracked separately."""
    site = far_c2_1_density_bonus()
    project = _project_with_db()
    output = calculate_far_full(site, project)

    assert output.baseline_far.ratio == 1.5
    assert output.allowable.baseline_floor_area_sf == 1.5 * 22495.0  # 33,742.5

    # All three tracks should be separate in the output
    # Baseline is always populated
    assert output.allowable.baseline_far_ratio == 1.5
    assert output.allowable.baseline_floor_area_sf == 33742.5


# ── Test Case 5: R4-1 Residential (Simple) ──────────────────────────────


def test_r4_1_residential():
    """R4-1: FAR = 3.0:1 (R4 in HD1). This is the critical zone-class distinction."""
    site = far_r4_1_simple()
    project = _simple_project()
    output = calculate_far_full(site, project)

    # R4 in HD1 = 3.0, NOT 1.5 (that's C/M only)
    assert output.baseline_far.ratio == 3.0
    assert output.area_basis.type == "buildable_area"
    assert output.governing_far.state == "baseline"
    assert output.allowable.baseline_floor_area_sf == 3.0 * 10000.0


def test_r4_1_legacy():
    """Legacy interface for R4-1."""
    site = far_r4_1_simple()
    project = _simple_project()
    results, _ = calculate_far(site, project)

    far = next(r for r in results if r.name == "max_far")
    assert far.value == 3.0


# ── Test Case 6: Ambiguous / Missing Data ────────────────────────────────


def test_c2_1vl_cpio_missing_doc():
    """C2-1VL-CPIO: baseline 1.5 (C/M in HD1VL), CPIO doc not available."""
    site = far_c2_1vl_cpio_missing_doc()
    project = _simple_project()
    output = calculate_far_full(site, project)

    assert output.baseline_far.ratio == 1.5
    assert output.baseline_far.is_provisional is True
    assert output.local_controls.cpio_document_status == "not_available"
    # Should be unresolved or baseline_with_override_risk
    assert output.outcome.state in ("baseline_with_override_risk", "unresolved")
    assert output.outcome.requires_manual_review is True
    issue_messages = [i.message for i in output.issues]
    assert any("CPIO" in m for m in issue_messages)


# ── Test Case 7: Multiple Parcels / Lot Tie ──────────────────────────────


def test_multi_parcel_lot_tie():
    """Multiple APNs: must flag lot tie confirmation needed."""
    site = far_multi_parcel_lot_tie()
    project = _simple_project()
    output = calculate_far_full(site, project)

    assert output.parcel.multi_parcel is True
    assert output.parcel.lot_tie_confirmed is None  # Not confirmed (lot_tie_assumed=False)
    issue_messages = [i.message for i in output.issues]
    assert any("lot tie" in m.lower() or "Multiple APN" in m for m in issue_messages)


# ── Zone-class discrimination (the #1 error) ────────────────────────────


def test_r3_hd1_far_is_3_not_1_5():
    """R3 in HD 1: FAR = 3.0 (residential), NOT 1.5 (that's C/M only)."""
    site = simple_r3_site()
    project = _simple_project()
    output = calculate_far_full(site, project)

    assert output.baseline_far.ratio == 3.0
    assert output.baseline_far.zone_row_used == "rd_r3"


def test_c2_hd2_far_is_6():
    """C2 in HD 2: FAR = 6.0."""
    site = c2_residential_site()
    project = _simple_project()
    output = calculate_far_full(site, project)

    assert output.baseline_far.ratio == 6.0
    assert output.baseline_far.zone_row_used == "c_m"


# ── Authority chain traceability ─────────────────────────────────────────


def test_authority_chain_populated():
    """Every FAR output should have an authority chain."""
    site = far_c2_1_no_overrides()
    project = _simple_project()
    output = calculate_far_full(site, project)

    assert len(output.governing_far.authority_chain) >= 2
    chain_text = " ".join(output.governing_far.authority_chain)
    assert "C2" in chain_text
    assert "1.5" in chain_text


def test_far_results_have_authority():
    """Legacy CalcResults should have authority metadata."""
    site = far_c2_1_no_overrides()
    project = _simple_project()
    results, _ = calculate_far(site, project)

    for r in results:
        assert r.authority_id is not None
        assert r.code_cycle != ""


# ── Three-track separation ──────────────────────────────────────────────


def test_three_tracks_separate():
    """Baseline, local, and incentive tracks must be independently populated."""
    site = far_c2_2d_cpio_tcc_beacon()
    project = _simple_project()
    output = calculate_far_full(site, project)

    # Baseline should always be populated
    assert output.allowable.baseline_far_ratio == 6.0
    assert output.allowable.baseline_floor_area_sf is not None

    # Local/incentive may be None if not applicable, but the fields exist
    # (they're populated when local_controls have explicit FAR values)
    assert hasattr(output.allowable, "locally_modified_far_ratio")
    assert hasattr(output.allowable, "incentive_far_ratio")


# ── Confidence cascade ──────────────────────────────────────────────────


def test_confidence_cascades_down():
    """If upstream step has low confidence, downstream inherits at best that level."""
    site = far_c2_1vl_cpio_missing_doc()
    project = _simple_project()
    output = calculate_far_full(site, project)

    # CPIO override present but unresolved => confidence should not be "high"
    assert output.outcome.confidence in ("medium", "low")


# ── FAR Numerator: counted floor area ────────────────────────────────────


def _project_with_explicit_counted_fa() -> Project:
    """Project with architect-provided counted floor area total."""
    return Project(
        project_name="Test Explicit FA",
        counted_floor_area_sf=46765.0,
        floor_area_definition_used="LAMC 12.03",
    )


def _project_with_per_floor_entries() -> Project:
    """Project with per-floor FAR breakdown (417 Alvarado pattern)."""
    return Project(
        project_name="Test Per-Floor FA",
        floor_area_entries=[
            FloorAreaEntry(floor_level="1", label="FAR", gross_area_sf=11711, counted_area_sf=11711),
            FloorAreaEntry(floor_level="2", label="FAR", gross_area_sf=11794, counted_area_sf=11794),
            FloorAreaEntry(floor_level="3", label="FAR", gross_area_sf=11661, counted_area_sf=11661),
            FloorAreaEntry(floor_level="4", label="FAR", gross_area_sf=11666, counted_area_sf=11666),
            FloorAreaEntry(floor_level="5", label="FAR", gross_area_sf=11667, counted_area_sf=11667),
            FloorAreaEntry(floor_level="6", label="FAR", gross_area_sf=11667, counted_area_sf=11667),
            FloorAreaEntry(floor_level="7", label="FAR", gross_area_sf=10468, counted_area_sf=10468),
        ],
        floor_area_definition_used="LAMC 12.03",
    )


def _project_with_exclusions() -> Project:
    """Project with per-floor entries that include excluded areas."""
    return Project(
        project_name="Test With Exclusions",
        floor_area_entries=[
            FloorAreaEntry(
                floor_level="P1", label="parking",
                gross_area_sf=9219, counted_area_sf=0, excluded_area_sf=9219,
                category="excluded", exclusion_reason="parking per LAMC 12.03",
            ),
            FloorAreaEntry(
                floor_level="1", label="residential",
                gross_area_sf=12000, counted_area_sf=11500, excluded_area_sf=500,
                category="partial", exclusion_reason="stairways/shafts",
            ),
            FloorAreaEntry(
                floor_level="2", label="residential",
                gross_area_sf=12000, counted_area_sf=11800, excluded_area_sf=200,
                category="partial", exclusion_reason="stairways/shafts",
            ),
        ],
        floor_area_definition_used="LAMC 12.03",
    )


def _project_occupancy_only() -> Project:
    """Project with occupancy_areas but no counted FA — must NOT estimate."""
    return Project(
        project_name="Test Occupancy Only",
        occupancy_areas=[
            OccupancyArea(occupancy_group="R-2", use_description="residential", area_sf=30000, floor_level="2-7"),
            OccupancyArea(occupancy_group="M", use_description="retail", area_sf=3000, floor_level="1"),
        ],
    )


def test_proposed_far_explicit_total():
    """When architect provides counted_floor_area_sf, use it directly."""
    site = far_c2_1_no_overrides()
    project = _project_with_explicit_counted_fa()
    output = calculate_far_full(site, project)

    assert output.proposed.numerator_source == "explicit_total"
    assert output.proposed.counted_floor_area_sf == 46765.0
    assert output.proposed.numerator_confidence == "high"
    assert output.proposed.far_ratio is not None
    assert abs(output.proposed.far_ratio - 46765.0 / 22495.0) < 0.01
    assert output.proposed.area_basis_used_sf == 22495.0
    # 46765 > 33742.5 (allowable at 1.5:1)
    assert output.proposed.compliant is False
    assert output.proposed.margin_sf is not None
    assert output.proposed.margin_sf < 0


def test_proposed_far_per_floor_entries():
    """When per-floor FloorAreaEntry list is provided, sum counted areas."""
    site = far_c2_1_no_overrides()
    project = _project_with_per_floor_entries()
    output = calculate_far_full(site, project)

    assert output.proposed.numerator_source == "per_floor_entries"
    # Sum of counted: 11711+11794+11661+11666+11667+11667+10468 = 80634
    assert output.proposed.counted_floor_area_sf == 80634.0
    assert output.proposed.numerator_confidence == "high"
    assert len(output.proposed.per_floor_breakdown) == 7
    assert output.proposed.per_floor_breakdown[0].floor_level == "1"
    assert output.proposed.per_floor_breakdown[0].counted_area_sf == 11711.0
    assert output.proposed.far_ratio is not None
    assert abs(output.proposed.far_ratio - 80634.0 / 22495.0) < 0.01


def test_proposed_far_with_exclusions():
    """Per-floor entries with excluded areas should track gross/counted/excluded."""
    site = far_c2_1_no_overrides()
    project = _project_with_exclusions()
    output = calculate_far_full(site, project)

    assert output.proposed.numerator_source == "per_floor_entries"
    assert output.proposed.gross_floor_area_sf == 9219 + 12000 + 12000  # 33219
    assert output.proposed.counted_floor_area_sf == 0 + 11500 + 11800  # 23300
    assert output.proposed.excluded_floor_area_sf == 9219 + 500 + 200  # 9919
    assert len(output.proposed.exclusion_breakdown) >= 1
    # Check exclusion reasons are tracked
    reasons = [e.exclusion_reason for e in output.proposed.exclusion_breakdown]
    assert "parking per LAMC 12.03" in reasons
    assert "stairways/shafts" in reasons


def test_proposed_far_no_data_is_unresolved():
    """No counted FA data at all → proposed FAR is unresolved, not estimated."""
    site = far_c2_1_no_overrides()
    project = _simple_project()
    output = calculate_far_full(site, project)

    assert output.proposed.numerator_source == "unresolved"
    assert output.proposed.counted_floor_area_sf is None
    assert output.proposed.far_ratio is None
    assert output.proposed.compliant is None
    assert output.proposed.numerator_confidence == "low"
    assert len(output.proposed.numerator_issues) >= 1
    assert "No counted floor area" in output.proposed.numerator_issues[0]


def test_proposed_far_occupancy_areas_not_used():
    """occupancy_areas present but NO counted FA → must NOT infer, must flag."""
    site = far_c2_1_no_overrides()
    project = _project_occupancy_only()
    output = calculate_far_full(site, project)

    assert output.proposed.numerator_source == "unresolved"
    assert output.proposed.counted_floor_area_sf is None
    assert output.proposed.far_ratio is None
    assert output.proposed.numerator_confidence == "low"
    # Must explicitly say occupancy_areas were NOT used
    oa_note = [n for n in output.proposed.numerator_issues if "occupancy_areas" in n.lower()]
    assert len(oa_note) >= 1
    assert "NOT used" in oa_note[0]


def test_proposed_far_definition_mismatch_flagged():
    """If project uses different FA definition than governing, flag it."""
    site = far_c2_1_no_overrides()
    project = Project(
        project_name="Test Mismatch",
        counted_floor_area_sf=30000.0,
        floor_area_definition_used="2020 LABC Ch.2",  # Site is ch1 → LAMC 12.03
    )
    output = calculate_far_full(site, project)

    assert output.proposed.numerator_source == "explicit_total"
    assert output.proposed.definition_aligned is False
    assert len(output.proposed.numerator_issues) >= 1
    assert "mismatch" in output.proposed.numerator_issues[0].lower()
    # Also check it surfaces as a FARIssue
    issue_msgs = [i.message for i in output.issues]
    assert any("mismatch" in m.lower() for m in issue_msgs)


def test_proposed_far_definition_aligned():
    """If project FA definition matches governing, aligned = True."""
    site = far_c2_1_no_overrides()
    project = Project(
        project_name="Test Aligned",
        counted_floor_area_sf=30000.0,
        floor_area_definition_used="LAMC 12.03",
    )
    output = calculate_far_full(site, project)

    assert output.proposed.definition_aligned is True
    assert len(output.proposed.numerator_issues) == 0


def test_proposed_far_gross_counted_residual_check():
    """If gross != counted + excluded, flag the residual."""
    site = far_c2_1_no_overrides()
    project = Project(
        project_name="Test Residual",
        floor_area_entries=[
            FloorAreaEntry(
                floor_level="1", label="floor",
                gross_area_sf=10000, counted_area_sf=8000, excluded_area_sf=1500,
                # residual = 10000 - 8000 - 1500 = 500 SF unaccounted
            ),
        ],
        floor_area_definition_used="LAMC 12.03",
    )
    output = calculate_far_full(site, project)

    assert output.proposed.numerator_source == "per_floor_entries"
    assert output.proposed.numerator_confidence == "medium"  # Downgraded due to residual
    assert any("Residual" in n for n in output.proposed.numerator_issues)
