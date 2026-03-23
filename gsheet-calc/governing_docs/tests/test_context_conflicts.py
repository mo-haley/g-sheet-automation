"""Tests for context field source priority and conflict handling.

Tests that:
1. Direct profile fields outrank linker/inferred values
2. ZI document header outranks linker but not profile direct
3. Conflicts preserve both values with alternatives
4. Same value from different sources upgrades source without conflict
5. Lower-priority source never silently overwrites higher-priority
6. Realistic multi-source conflict scenarios
"""

from __future__ import annotations

import pytest

from governing_docs.document_context import (
    ContextField,
    ContextFieldAlternative,
    ContextFieldConfidence,
    ContextFieldSource,
    DocumentContext,
    _set_context_field,
    _SOURCE_PRIORITY,
    build_document_context,
)
from governing_docs.models import (
    AuthorityLink,
    AuthorityLinkType,
    ControlLinkResult,
    ControlType,
    DiscoverySourceType,
    LinkConfidence,
    ParcelAuthorityItem,
    ParcelProfileData,
    SiteControl,
)


# ============================================================
# Source priority ranking
# ============================================================

class TestSourcePriority:

    def test_profile_direct_is_highest(self):
        assert _SOURCE_PRIORITY[ContextFieldSource.PARCEL_PROFILE_DIRECT] == 1

    def test_zi_header_ranks_second(self):
        p = _SOURCE_PRIORITY
        assert p[ContextFieldSource.ZI_DOCUMENT_HEADER] < p[ContextFieldSource.LINKER_INFERENCE]

    def test_linker_ranks_below_profile(self):
        p = _SOURCE_PRIORITY
        assert p[ContextFieldSource.LINKER_INFERENCE] > p[ContextFieldSource.PARCEL_PROFILE_DIRECT]

    def test_manual_entry_is_lowest(self):
        p = _SOURCE_PRIORITY
        assert p[ContextFieldSource.MANUAL_ENTRY] == max(p.values())


# ============================================================
# _set_context_field priority enforcement
# ============================================================

class TestSetContextField:

    def test_first_set_succeeds(self):
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", "Regional Commercial",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)
        assert ctx.subarea.value == "Regional Commercial"
        assert ctx.subarea.source == ContextFieldSource.PARCEL_PROFILE_DIRECT

    def test_none_value_skipped(self):
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", None,
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)
        assert ctx.subarea is None

    def test_same_value_upgrades_source(self):
        """Same value from higher-priority source should upgrade source."""
        ctx = DocumentContext()
        _set_context_field(ctx, "ordinance_number", "185539",
                           ContextFieldSource.LINKER_INFERENCE,
                           ContextFieldConfidence.MEDIUM)
        assert ctx.ordinance_number.source == ContextFieldSource.LINKER_INFERENCE

        _set_context_field(ctx, "ordinance_number", "185539",
                           ContextFieldSource.ZI_DOCUMENT_HEADER,
                           ContextFieldConfidence.HIGH)
        assert ctx.ordinance_number.value == "185539"
        assert ctx.ordinance_number.source == ContextFieldSource.ZI_DOCUMENT_HEADER
        assert len(ctx.conflicts) == 0  # Same value, no conflict

    def test_same_value_no_downgrade(self):
        """Same value from lower-priority source should NOT downgrade source."""
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", "Regional Commercial",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)
        _set_context_field(ctx, "subarea", "Regional Commercial",
                           ContextFieldSource.LINKER_INFERENCE,
                           ContextFieldConfidence.MEDIUM)
        assert ctx.subarea.source == ContextFieldSource.PARCEL_PROFILE_DIRECT
        assert ctx.subarea.confidence == ContextFieldConfidence.HIGH

    def test_higher_priority_wins(self):
        """Higher-priority source should become primary when values differ."""
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", "Inferred Subarea",
                           ContextFieldSource.LINKER_INFERENCE,
                           ContextFieldConfidence.MEDIUM)
        _set_context_field(ctx, "subarea", "Regional Commercial",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)

        assert ctx.subarea.value == "Regional Commercial"
        assert ctx.subarea.source == ContextFieldSource.PARCEL_PROFILE_DIRECT

    def test_higher_priority_preserves_alternative(self):
        """Demoted value should be preserved as an alternative."""
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", "Inferred Subarea",
                           ContextFieldSource.LINKER_INFERENCE,
                           ContextFieldConfidence.MEDIUM)
        _set_context_field(ctx, "subarea", "Regional Commercial",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)

        assert len(ctx.subarea.alternatives) == 1
        alt = ctx.subarea.alternatives[0]
        assert alt.value == "Inferred Subarea"
        assert alt.source == ContextFieldSource.LINKER_INFERENCE
        assert "lower-priority" in alt.reason_not_primary.lower()

    def test_lower_priority_becomes_alternative(self):
        """Lower-priority source should not replace existing, just add alternative."""
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", "Regional Commercial",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)
        _set_context_field(ctx, "subarea", "Central Commercial E",
                           ContextFieldSource.LINKER_INFERENCE,
                           ContextFieldConfidence.MEDIUM)

        assert ctx.subarea.value == "Regional Commercial"
        assert ctx.subarea.source == ContextFieldSource.PARCEL_PROFILE_DIRECT
        assert len(ctx.subarea.alternatives) == 1
        assert ctx.subarea.alternatives[0].value == "Central Commercial E"

    def test_conflict_recorded(self):
        """Any value disagreement should record a conflict."""
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", "A",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)
        _set_context_field(ctx, "subarea", "B",
                           ContextFieldSource.LINKER_INFERENCE,
                           ContextFieldConfidence.MEDIUM)

        assert len(ctx.conflicts) == 1
        assert "A" in ctx.conflicts[0]
        assert "B" in ctx.conflicts[0]
        assert "primary" in ctx.conflicts[0].lower()


# ============================================================
# Realistic conflict scenarios
# ============================================================

class TestRealisticConflicts:

    def test_profile_subarea_vs_linker_subarea(self):
        """Profile says 'Regional Commercial', linker says 'Central Commercial E'.
        Profile direct should win."""
        # Simulate: control has subarea from fixture
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
            subarea="Central Commercial E",  # From fixture/manual entry
        )
        # Linker overlay link also has a subarea
        link_result = ControlLinkResult(
            control=control,
            links=[AuthorityLink(
                control_type=ControlType.CPIO,
                confidence=LinkConfidence.DETERMINISTIC,
                overlay_name="San Pedro",
                subarea="Central Commercial E",
            )],
        )
        # Profile has a different subarea (the real one)
        profile = ParcelProfileData(source_method="test")
        cpio_item = ParcelAuthorityItem(
            raw_text="San Pedro CPIO Subarea Regional Commercial",
            link_type=AuthorityLinkType.OVERLAY_DISTRICT,
            mapped_control_type=ControlType.CPIO,
            overlay_name="San Pedro",
            overlay_abbreviation="CPIO",
            subarea="Regional Commercial",
        )
        profile.authority_items = [cpio_item]

        ctx = build_document_context(
            control=control,
            link_result=link_result,
            profile=profile,
        )

        # Profile direct should win
        assert ctx.subarea.value == "Regional Commercial"
        assert ctx.subarea.source == ContextFieldSource.PARCEL_PROFILE_DIRECT
        # "Central Commercial E" should be preserved as alternative
        assert any(
            alt.value == "Central Commercial E"
            for alt in ctx.subarea.alternatives
        )
        # Conflict should be recorded
        assert len(ctx.conflicts) > 0

    def test_zi_header_ordinance_vs_linker_ordinance(self):
        """ZI doc header says 185539, linker said 185541. ZI header should win."""
        from governing_docs.zi_extractor import ZIExtractionResult, ExtractionQuality

        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        link_result = ControlLinkResult(
            control=control,
            links=[AuthorityLink(
                control_type=ControlType.CPIO,
                confidence=LinkConfidence.PROBABLE,
                ordinance_number="185541",
                zi_code="ZI-2478",
            )],
        )
        profile = ParcelProfileData(source_method="test")
        zi_item = ParcelAuthorityItem(
            raw_text="ZI-2478 CPIO",
            link_type=AuthorityLinkType.ZONING_INFORMATION,
            zi_code="ZI-2478",
            mapped_control_type=ControlType.CPIO,
        )
        profile.zi_items = [zi_item]

        zi_ext = ZIExtractionResult(
            zi_code="ZI2478",
            source_path="/fake",
            quality=ExtractionQuality.GOOD,
            header_ordinance_number="185539",
        )

        ctx = build_document_context(
            control=control,
            link_result=link_result,
            profile=profile,
            zi_extractions=[zi_ext],
        )

        # ZI header (priority 2) should win over linker (priority 5)
        assert ctx.ordinance_number.value == "185539"
        assert ctx.ordinance_number.source == ContextFieldSource.ZI_DOCUMENT_HEADER
        # 185541 should be preserved as alternative
        assert any(alt.value == "185541" for alt in ctx.ordinance_number.alternatives)

    def test_multiple_conflicts_all_recorded(self):
        """When both subarea and ordinance conflict, both are recorded."""
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", "A",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)
        _set_context_field(ctx, "subarea", "B",
                           ContextFieldSource.LINKER_INFERENCE,
                           ContextFieldConfidence.MEDIUM)
        _set_context_field(ctx, "ordinance_number", "111",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)
        _set_context_field(ctx, "ordinance_number", "222",
                           ContextFieldSource.LINKER_INFERENCE,
                           ContextFieldConfidence.MEDIUM)

        assert len(ctx.conflicts) == 2
        assert any("subarea" in c for c in ctx.conflicts)
        assert any("ordinance_number" in c for c in ctx.conflicts)

    def test_three_way_conflict_preserves_all(self):
        """Three sources disagree — primary wins, both others preserved."""
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", "Manual",
                           ContextFieldSource.MANUAL_ENTRY,
                           ContextFieldConfidence.LOW)
        _set_context_field(ctx, "subarea", "Linker",
                           ContextFieldSource.LINKER_INFERENCE,
                           ContextFieldConfidence.MEDIUM)
        _set_context_field(ctx, "subarea", "Profile",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)

        assert ctx.subarea.value == "Profile"
        assert len(ctx.subarea.alternatives) == 2
        alt_values = {a.value for a in ctx.subarea.alternatives}
        assert alt_values == {"Manual", "Linker"}


# ============================================================
# Warning generation
# ============================================================

class TestConflictWarnings:

    def test_conflict_identifies_field_name(self):
        ctx = DocumentContext()
        _set_context_field(ctx, "overlay_name", "Old",
                           ContextFieldSource.MANUAL_ENTRY,
                           ContextFieldConfidence.LOW)
        _set_context_field(ctx, "overlay_name", "New",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)

        assert any("overlay_name" in c for c in ctx.conflicts)

    def test_conflict_identifies_primary_and_alternative(self):
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", "Low",
                           ContextFieldSource.COMMUNITY_PLAN_INFERENCE,
                           ContextFieldConfidence.LOW)
        _set_context_field(ctx, "subarea", "High",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)

        conflict = ctx.conflicts[0]
        assert "primary" in conflict.lower()
        assert "parcel_profile_direct" in conflict
        assert "community_plan_inference" in conflict

    def test_alternative_has_reason(self):
        ctx = DocumentContext()
        _set_context_field(ctx, "subarea", "Profile",
                           ContextFieldSource.PARCEL_PROFILE_DIRECT,
                           ContextFieldConfidence.HIGH)
        _set_context_field(ctx, "subarea", "Linker",
                           ContextFieldSource.LINKER_INFERENCE,
                           ContextFieldConfidence.MEDIUM)

        alt = ctx.subarea.alternatives[0]
        assert alt.reason_not_primary is not None
        assert "lower-priority" in alt.reason_not_primary.lower()
