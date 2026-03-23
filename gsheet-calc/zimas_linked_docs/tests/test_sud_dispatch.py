"""Tests for supplemental-district subtype dispatch (Pass C).

Verifies that:
1. CDO (Coastal Development Overlay) is classified as overlay_cdo — by code and by name
2. HA (Hillside Area) is classified as overlay_ha — by code and by name
3. PO/POD (Pedestrian Oriented District) is classified as overlay_po — by code and by name
4. Unknown/generic SUD codes remain conservative (overlay_supplemental or unknown_artifact)
5. Interrupt scope is correct per subtype
6. No regression on CPIO, Q, D, specific-plan handling

NOTE: subtype recognition improves routing only. These districts have not been
interpreted. None of the new doc types produce INTERRUPT_UNRESOLVED (blocking).
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_OVERLAY_CDO,
    DOC_TYPE_OVERLAY_HA,
    DOC_TYPE_OVERLAY_PO,
    DOC_TYPE_OVERLAY_SUPPLEMENTAL,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_UNKNOWN_ARTIFACT,
    INTERRUPT_PROVISIONAL,
    INTERRUPT_NONE,
    LinkedDocRecord,
    LinkedDocRegistry,
    ZimasLinkedDocInput,
    POSTURE_MANUAL_REVIEW_FIRST,
)
from zimas_linked_docs.doc_classifier import (
    classify_candidates,
    _is_cdo,
    _is_ha,
    _is_po,
    _is_supplemental,
)
from zimas_linked_docs.gatekeeper import evaluate_interrupts
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline
from zimas_linked_docs.models import LinkedDocCandidate, PATTERN_OVERLAY_NAME_FIELD, PATTERN_ZONE_STRING_PARSE


# ── Helpers ───────────────────────────────────────────────────────────────────

def _overlay_candidate(raw: str, source_field: str = "overlay_zones") -> LinkedDocCandidate:
    return LinkedDocCandidate(
        candidate_id=f"test-{raw.lower().replace(' ', '-')}",
        source_field=source_field,
        raw_value=raw,
        detected_pattern=PATTERN_OVERLAY_NAME_FIELD,
    )


def _zone_parse_candidate(raw: str) -> LinkedDocCandidate:
    return LinkedDocCandidate(
        candidate_id=f"test-zsp-{raw.lower().replace(' ', '-')}",
        source_field="zone_string_parse:supplemental_district",
        raw_value=raw,
        detected_pattern=PATTERN_ZONE_STRING_PARSE,
    )


def _inp(**kwargs) -> ZimasLinkedDocInput:
    return ZimasLinkedDocInput(apn="1234-567-890", **kwargs)


def _registry_with_record(record: LinkedDocRecord) -> LinkedDocRegistry:
    return LinkedDocRegistry(apn="1234-567-890", records=[record])


# ── Section 1: Detection helpers — unit tests ─────────────────────────────────

class TestSudDetectionHelpers:
    def test_is_cdo_exact_code(self):
        assert _is_cdo("CDO")
        assert _is_cdo("cdo")

    def test_is_cdo_full_name(self):
        assert _is_cdo("Coastal Development Overlay")
        assert _is_cdo("COASTAL DEVELOPMENT OVERLAY")

    def test_is_cdo_partial_name(self):
        assert _is_cdo("Coastal Development")

    def test_is_cdo_false_for_others(self):
        assert not _is_cdo("HA")
        assert not _is_cdo("PO")
        assert not _is_cdo("San Pedro CPIO")

    def test_is_ha_exact_code(self):
        assert _is_ha("HA")
        assert _is_ha("ha")

    def test_is_ha_full_name(self):
        assert _is_ha("Hillside Area")
        assert _is_ha("HILLSIDE AREA")

    def test_is_ha_false_for_others(self):
        assert not _is_ha("CDO")
        assert not _is_ha("PO")
        assert not _is_ha("HOA")         # HOA contains HA but is not HA

    def test_is_ha_not_substring_false_positive(self):
        """'HA' check is exact-code or 'HILLSIDE AREA' substring — 'SHA' should not match."""
        assert not _is_ha("SHA")
        assert not _is_ha("HASTY")

    def test_is_po_exact_code(self):
        assert _is_po("PO")
        assert _is_po("POD")

    def test_is_po_full_name(self):
        assert _is_po("Pedestrian Oriented")
        assert _is_po("Pedestrian Oriented District")

    def test_is_po_false_for_others(self):
        assert not _is_po("CDO")
        assert not _is_po("HA")
        assert not _is_po("POP")

    def test_is_supplemental_unchanged(self):
        """Generic supplemental check still works as before."""
        assert _is_supplemental("SUD")
        assert _is_supplemental("Supplemental Use District")
        assert _is_supplemental("Supplemental District")
        assert not _is_supplemental("CDO")   # CDO is not a generic SUD name
        assert not _is_supplemental("HA")


# ── Section 2: Classifier dispatch via overlay_zones ─────────────────────────

class TestClassifierOverlayZones:
    def test_cdo_code_classifies_as_overlay_cdo(self):
        records, _ = classify_candidates([_overlay_candidate("CDO")])
        assert records[0].doc_type == DOC_TYPE_OVERLAY_CDO

    def test_cdo_full_name_classifies_as_overlay_cdo(self):
        records, _ = classify_candidates([_overlay_candidate("Coastal Development Overlay")])
        assert records[0].doc_type == DOC_TYPE_OVERLAY_CDO

    def test_ha_code_classifies_as_overlay_ha(self):
        records, _ = classify_candidates([_overlay_candidate("HA")])
        assert records[0].doc_type == DOC_TYPE_OVERLAY_HA

    def test_ha_full_name_classifies_as_overlay_ha(self):
        records, _ = classify_candidates([_overlay_candidate("Hillside Area")])
        assert records[0].doc_type == DOC_TYPE_OVERLAY_HA

    def test_po_code_classifies_as_overlay_po(self):
        records, _ = classify_candidates([_overlay_candidate("PO")])
        assert records[0].doc_type == DOC_TYPE_OVERLAY_PO

    def test_pod_code_classifies_as_overlay_po(self):
        records, _ = classify_candidates([_overlay_candidate("POD")])
        assert records[0].doc_type == DOC_TYPE_OVERLAY_PO

    def test_po_full_name_classifies_as_overlay_po(self):
        records, _ = classify_candidates([_overlay_candidate("Pedestrian Oriented District")])
        assert records[0].doc_type == DOC_TYPE_OVERLAY_PO

    def test_cdo_usability_posture_is_manual_review_first(self):
        records, _ = classify_candidates([_overlay_candidate("CDO")])
        assert records[0].usability_posture == POSTURE_MANUAL_REVIEW_FIRST

    def test_ha_doc_type_notes_mention_subtype(self):
        records, _ = classify_candidates([_overlay_candidate("HA")])
        assert "hillside" in records[0].doc_type_notes.lower()
        assert "not been interpreted" in records[0].doc_type_notes.lower()

    def test_unknown_code_falls_to_unknown_artifact(self):
        """An unrecognised overlay code (not CDO/HA/PO/CPIO/SUD/SP) → UNKNOWN_ARTIFACT."""
        records, _ = classify_candidates([_overlay_candidate("XYZ")])
        assert records[0].doc_type == DOC_TYPE_UNKNOWN_ARTIFACT


# ── Section 3: Classifier dispatch via zone string parse ─────────────────────

class TestClassifierZoneStringParse:
    def test_cdo_from_zone_parse_classifies_as_overlay_cdo(self):
        records, _ = classify_candidates([_zone_parse_candidate("CDO")])
        assert records[0].doc_type == DOC_TYPE_OVERLAY_CDO

    def test_ha_from_zone_parse_classifies_as_overlay_ha(self):
        records, _ = classify_candidates([_zone_parse_candidate("HA")])
        assert records[0].doc_type == DOC_TYPE_OVERLAY_HA

    def test_po_from_zone_parse_classifies_as_overlay_po(self):
        records, _ = classify_candidates([_zone_parse_candidate("PO")])
        assert records[0].doc_type == DOC_TYPE_OVERLAY_PO

    def test_unknown_from_zone_parse_falls_to_unknown_artifact(self):
        records, _ = classify_candidates([_zone_parse_candidate("XYZ")])
        assert records[0].doc_type == DOC_TYPE_UNKNOWN_ARTIFACT


# ── Section 4: Gatekeeper interrupt scope by subtype ─────────────────────────

class TestGatekeeperSubtypeInterrupts:
    def _decisions(self, doc_type: str) -> dict[str, str]:
        record = LinkedDocRecord(
            record_id="test-sud-001",
            doc_type=doc_type,
            doc_label=doc_type.upper(),
            usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
        )
        decisions, _ = evaluate_interrupts(
            _registry_with_record(record),
            topics=["FAR", "density", "height", "parking", "setback"],
        )
        return {d.topic: d.interrupt_level for d in decisions}

    def test_cdo_interrupts_all_five_topics(self):
        levels = self._decisions(DOC_TYPE_OVERLAY_CDO)
        assert levels["FAR"] == INTERRUPT_PROVISIONAL
        assert levels["density"] == INTERRUPT_PROVISIONAL
        assert levels["height"] == INTERRUPT_PROVISIONAL
        assert levels["setback"] == INTERRUPT_PROVISIONAL
        assert levels["parking"] == INTERRUPT_PROVISIONAL

    def test_ha_interrupts_all_five_topics(self):
        levels = self._decisions(DOC_TYPE_OVERLAY_HA)
        assert levels["FAR"] == INTERRUPT_PROVISIONAL
        assert levels["density"] == INTERRUPT_PROVISIONAL
        assert levels["height"] == INTERRUPT_PROVISIONAL
        assert levels["setback"] == INTERRUPT_PROVISIONAL
        assert levels["parking"] == INTERRUPT_PROVISIONAL

    def test_po_interrupts_parking_and_setback_only(self):
        levels = self._decisions(DOC_TYPE_OVERLAY_PO)
        assert levels["parking"] == INTERRUPT_PROVISIONAL
        assert levels["setback"] == INTERRUPT_PROVISIONAL

    def test_po_does_not_interrupt_far_density_height(self):
        levels = self._decisions(DOC_TYPE_OVERLAY_PO)
        assert levels["FAR"] == INTERRUPT_NONE
        assert levels["density"] == INTERRUPT_NONE
        assert levels["height"] == INTERRUPT_NONE

    def test_generic_sud_interrupts_parking_density_only(self):
        """Generic SUD behavior unchanged."""
        levels = self._decisions(DOC_TYPE_OVERLAY_SUPPLEMENTAL)
        assert levels["parking"] == INTERRUPT_PROVISIONAL
        assert levels["density"] == INTERRUPT_PROVISIONAL
        assert levels["FAR"] == INTERRUPT_NONE
        assert levels["height"] == INTERRUPT_NONE
        assert levels["setback"] == INTERRUPT_NONE

    def test_cdo_is_not_blocking(self):
        """PROVISIONAL is not blocking — only UNRESOLVED/REFUSE are."""
        record = LinkedDocRecord(
            record_id="test-cdo",
            doc_type=DOC_TYPE_OVERLAY_CDO,
            doc_label="CDO",
            usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
        )
        decisions, _ = evaluate_interrupts(_registry_with_record(record), topics=["FAR"])
        assert not any(d.blocking for d in decisions)


# ── Section 5: End-to-end pipeline ───────────────────────────────────────────

class TestPipelineEndToEnd:
    def test_cdo_in_overlay_zones_classified_and_interrupts(self):
        inp = _inp(overlay_zones=["CDO"])
        output = run_zimas_linked_doc_pipeline(inp)
        cdo_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CDO]
        assert cdo_records, "CDO should be classified as overlay_cdo"
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_PROVISIONAL

    def test_ha_in_overlay_zones_classified_and_interrupts_height(self):
        inp = _inp(overlay_zones=["HA"])
        output = run_zimas_linked_doc_pipeline(inp)
        ha_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_HA]
        assert ha_records
        height_decision = next(d for d in output.interrupt_decisions if d.topic == "height")
        assert height_decision.interrupt_level == INTERRUPT_PROVISIONAL

    def test_po_does_not_interrupt_far_in_pipeline(self):
        inp = _inp(overlay_zones=["PO"])
        output = run_zimas_linked_doc_pipeline(inp)
        po_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_PO]
        assert po_records
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_NONE

    def test_cdo_supplemental_districts_from_zone_parse(self):
        inp = _inp(supplemental_districts_from_parse=["CDO"])
        output = run_zimas_linked_doc_pipeline(inp)
        cdo_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CDO]
        assert cdo_records, "CDO from zone string parse should classify as overlay_cdo"


# ── Section 6: No regression on other doc types ───────────────────────────────

class TestNoRegressionOtherDocTypes:
    def test_cpio_still_classifies_correctly(self):
        inp = _inp(overlay_zones=["San Pedro CPIO"])
        output = run_zimas_linked_doc_pipeline(inp)
        cpio_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CPIO]
        assert cpio_records

    def test_q_condition_still_interrupts_provisionally(self):
        inp = _inp(q_conditions=["Q"])
        output = run_zimas_linked_doc_pipeline(inp)
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_PROVISIONAL

    def test_cdo_and_cpio_coexist(self):
        """A parcel can have both CDO and a CPIO without collision."""
        inp = _inp(overlay_zones=["CDO", "San Pedro CPIO"])
        output = run_zimas_linked_doc_pipeline(inp)
        types = {r.doc_type for r in output.registry.records}
        assert DOC_TYPE_OVERLAY_CDO in types
        assert DOC_TYPE_OVERLAY_CPIO in types
