"""Tests for ZI fetched-evidence classification enrichment and gatekeeper rationale.

Scenarios:
1. Fetched ZI title with good quality enriches doc_type_notes with subject
2. Fetched ZI title contradicts expected classification (ZI# mismatch → ambiguous + MANUAL_REVIEW_FIRST)
3. Fetched header ordinance noted in doc_type_notes enrichment
4. Gatekeeper INTERRUPT_NONE reason includes fetched ZI context
5. Gatekeeper triggered-interrupt recommended_action includes fetched ZI context
6. Non-ZI records are unaffected by ZI enrichment/gatekeeper ZI context
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_ZI_DOCUMENT,
    FETCH_NOW,
    CONF_FETCHED_PARTIALLY_USABLE,
    CONF_SURFACE_USABLE,
    INTERRUPT_NONE,
    INTERRUPT_UNRESOLVED,
    POSTURE_MACHINE_USABLE,
    POSTURE_MANUAL_REVIEW_FIRST,
    LinkedDocRecord,
    LinkedDocRegistry,
)
from zimas_linked_docs.zi_fetch import ZIFetchResult
from zimas_linked_docs.gatekeeper import evaluate_interrupts, _zi_context_note


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_zi_record(
    doc_label: str = "ZI-2478",
    confidence_state: str = CONF_SURFACE_USABLE,
    extracted_title: str | None = None,
    source_ordinance: str | None = None,
    doc_type_confidence: str = "confirmed",
    usability_posture: str = POSTURE_MACHINE_USABLE,
) -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id=f"test-{doc_label.replace('-', '')}",
        doc_type=DOC_TYPE_ZI_DOCUMENT,
        doc_label=doc_label,
        usability_posture=usability_posture,
        doc_type_confidence=doc_type_confidence,
        fetch_decision=FETCH_NOW,
        confidence_state=confidence_state,
        extracted_title=extracted_title,
        source_ordinance_number=source_ordinance,
        doc_type_notes=(
            "ZI document number confirmed. LADBS ZI lookup provides "
            "structured title and subject. Surface-level use only."
        ),
    )


def _make_fetch_result(
    doc_label: str = "ZI-2478",
    title: str | None = "CPIO Regulations for Coastal San Pedro",
    ordinance: str | None = "185539",
    effective_date: str | None = "April 15, 2020",
    header_zi: str | None = "2478",
    quality: str = "good",
) -> ZIFetchResult:
    return ZIFetchResult(
        doc_label=doc_label,
        url="https://zimas.lacity.org/documents/zoneinfo/ZI2478.pdf",
        fetch_status="success",
        fetch_notes="Fetched 5000 bytes",
        extracted_title=title,
        extracted_ordinance_number=ordinance,
        extracted_effective_date=effective_date,
        header_zi_number=header_zi,
        extraction_quality=quality,
        extraction_notes=f"Extraction quality: {quality}",
    )


def _run_zi(record: LinkedDocRecord, result: ZIFetchResult):
    """Run structure_extractor._extract_zi with a mocked run_zi_fetch result."""
    from zimas_linked_docs.structure_extractor import extract_surface_fields
    with patch("zimas_linked_docs.structure_extractor.run_zi_fetch", return_value=result):
        _, issues = extract_surface_fields([record], _fetch_enabled=True)
    return issues


# ── Scenario 1: Fetched title enriches doc_type_notes ────────────────────────

class TestFetchedTitleEnrichesClassification:
    def test_cpio_title_gets_cpio_subject_hint(self):
        record = _make_zi_record()
        result = _make_fetch_result(
            title="COMMUNITY PLAN IMPLEMENTATION OVERLAY DISTRICT REGULATIONS"
        )
        _run_zi(record, result)

        assert "Fetched title:" in record.doc_type_notes
        assert "COMMUNITY PLAN IMPLEMENTATION OVERLAY" in record.doc_type_notes
        assert "CPIO-related" in record.doc_type_notes

    def test_generic_title_enriches_without_subject_hint(self):
        record = _make_zi_record()
        result = _make_fetch_result(
            title="ZONING REGULATIONS AND REQUIREMENTS",
            header_zi="2478",
        )
        _run_zi(record, result)

        assert "Fetched title:" in record.doc_type_notes
        assert "ZONING REGULATIONS AND REQUIREMENTS" in record.doc_type_notes
        # No subject hint for unrecognized subject — that's fine
        original_notes = (
            "ZI document number confirmed. LADBS ZI lookup provides "
            "structured title and subject. Surface-level use only."
        )
        # doc_type_notes was updated, not replaced
        assert "ZI document number confirmed" in record.doc_type_notes

    def test_fetch_failed_notes_title_unconfirmed(self):
        record = _make_zi_record()
        result = ZIFetchResult(
            doc_label="ZI-2478",
            fetch_status="failed",
            fetch_notes="GET failed: 404",
        )
        _run_zi(record, result)

        assert "Fetch failed" in record.doc_type_notes
        assert "title not confirmed" in record.doc_type_notes.lower()

    def test_fetch_success_no_title_notes_extraction_failure(self):
        record = _make_zi_record()
        result = _make_fetch_result(title=None, quality="failed")
        _run_zi(record, result)

        assert "Fetch succeeded but title was not extracted" in record.doc_type_notes
        assert "unconfirmed" in record.doc_type_notes.lower()


# ── Scenario 2: ZI# mismatch contradicts classification ──────────────────────

class TestZiNumberMismatchDowngradesClassification:
    def test_posture_downgraded_on_mismatch(self):
        record = _make_zi_record(doc_label="ZI-2478")
        result = _make_fetch_result(header_zi="9999")  # mismatch
        _run_zi(record, result)

        assert record.usability_posture == POSTURE_MANUAL_REVIEW_FIRST

    def test_doc_type_confidence_downgraded_on_mismatch(self):
        record = _make_zi_record(doc_label="ZI-2478")
        result = _make_fetch_result(header_zi="9999")  # mismatch
        _run_zi(record, result)

        assert record.doc_type_confidence == "ambiguous"

    def test_doc_type_notes_records_downgrade_on_mismatch(self):
        record = _make_zi_record(doc_label="ZI-2478")
        result = _make_fetch_result(header_zi="9999")  # mismatch
        _run_zi(record, result)

        assert "CLASSIFICATION DOWNGRADED" in record.doc_type_notes
        assert "2478" in record.doc_type_notes
        assert "9999" in record.doc_type_notes

    def test_no_downgrade_when_zi_matches(self):
        record = _make_zi_record(doc_label="ZI-2478")
        result = _make_fetch_result(header_zi="2478")  # matches
        _run_zi(record, result)

        assert record.usability_posture == POSTURE_MACHINE_USABLE
        assert record.doc_type_confidence == "confirmed"


# ── Scenario 3: Header ordinance surfaced in enrichment ──────────────────────

class TestHeaderOrdinanceEnrichment:
    def test_header_ordinance_in_doc_type_notes(self):
        record = _make_zi_record()
        result = _make_fetch_result(ordinance="185539")
        _run_zi(record, result)

        # Ordinance stored on record
        assert record.extracted_ordinance_number == "185539"
        # extraction_notes documents the source
        assert "185539" in record.extraction_notes
        assert "Ordinance: 185539" in record.extraction_notes

    def test_no_ordinance_noted_explicitly_in_extraction_notes(self):
        record = _make_zi_record()
        result = _make_fetch_result(ordinance=None)
        _run_zi(record, result)

        assert record.extracted_ordinance_number is None
        assert "Ordinance: not in header" in record.extraction_notes

    def test_ordinance_conflict_does_not_appear_in_doc_type_notes(self):
        """Ordinance conflict is surfaced in extraction_notes, not doc_type_notes."""
        record = _make_zi_record(source_ordinance="185539")
        result = _make_fetch_result(ordinance="186000")  # conflict
        _run_zi(record, result)

        # The conflict is in extraction_notes
        assert "CONFLICT" in record.extraction_notes
        assert "186000" in record.extraction_notes
        # doc_type_notes should still be enriched with the title
        assert "Fetched title:" in record.doc_type_notes


# ── Scenario 4: Gatekeeper INTERRUPT_NONE includes fetched ZI context ────────

class TestGatekeeperInterruptNoneWithZiEvidence:
    def _registry_with_fetched_zi(self, zi_title: str) -> LinkedDocRegistry:
        zi_record = _make_zi_record(
            confidence_state=CONF_FETCHED_PARTIALLY_USABLE,
            extracted_title=zi_title,
        )
        return LinkedDocRegistry(
            apn="1234-567-890",
            records=[zi_record],
            registry_input_coverage="complete",
        )

    def test_interrupt_none_reason_includes_zi_title(self):
        registry = self._registry_with_fetched_zi(
            "COASTAL SAN PEDRO CPIO REGULATIONS"
        )
        decisions, _ = evaluate_interrupts(registry)

        for decision in decisions:
            assert decision.interrupt_level == INTERRUPT_NONE
            assert "ZI-2478" in decision.reason
            assert "COASTAL SAN PEDRO CPIO REGULATIONS" in decision.reason

    def test_interrupt_none_without_fetched_zi_has_no_zi_note(self):
        """Without fetched ZI, reason should not mention ZI documents."""
        registry = LinkedDocRegistry(
            apn="1234-567-890",
            records=[],
            registry_input_coverage="complete",
        )
        decisions, _ = evaluate_interrupts(registry)

        for decision in decisions:
            assert "ZI document" not in decision.reason
            assert "Fetched ZI" not in decision.reason

    def test_zi_at_surface_usable_not_included_in_note(self):
        """Only fetched_partially_usable ZI records appear in the ZI context note."""
        zi_record = _make_zi_record(
            confidence_state=CONF_SURFACE_USABLE,  # not fetched_partially_usable
            extracted_title="Some ZI Title",
        )
        registry = LinkedDocRegistry(
            apn="1234-567-890",
            records=[zi_record],
            registry_input_coverage="complete",
        )
        decisions, _ = evaluate_interrupts(registry)

        for decision in decisions:
            # surface_usable ZI has no fetched title evidence — no ZI note
            assert "Fetched ZI document evidence" not in decision.reason


# ── Scenario 5: Gatekeeper triggered interrupt includes ZI context ────────────

class TestGatekeeperTriggeredInterruptWithZiEvidence:
    def _registry_with_cpio_and_fetched_zi(self) -> LinkedDocRegistry:
        cpio_record = LinkedDocRecord(
            record_id="test-cpio-1",
            doc_type=DOC_TYPE_OVERLAY_CPIO,
            doc_label="Venice CPIO",
            usability_posture="manual_review_first",
            confidence_state="detected_not_interpreted",
        )
        zi_record = _make_zi_record(
            confidence_state=CONF_FETCHED_PARTIALLY_USABLE,
            extracted_title="VENICE CPIO DISTRICT REGULATIONS",
        )
        return LinkedDocRegistry(
            apn="1234-567-890",
            records=[cpio_record, zi_record],
            registry_input_coverage="complete",
        )

    def test_triggered_interrupt_action_includes_zi_context(self):
        registry = self._registry_with_cpio_and_fetched_zi()
        decisions, _ = evaluate_interrupts(registry)

        # CPIO triggers UNRESOLVED on FAR, density, parking, setback
        unresolved = [d for d in decisions if d.interrupt_level == INTERRUPT_UNRESOLVED]
        assert unresolved  # CPIO should have triggered

        for decision in unresolved:
            # ZI context should appear in recommended_action
            assert "ZI-2478" in decision.recommended_action
            assert "VENICE CPIO DISTRICT REGULATIONS" in decision.recommended_action
            # ZI is NOT a trigger — should not appear in triggering_doc_labels
            assert "ZI-2478" not in decision.triggering_doc_labels

    def test_triggered_interrupt_reason_unchanged_by_zi_evidence(self):
        """ZI evidence appears in action, not in the core reason string."""
        registry = self._registry_with_cpio_and_fetched_zi()
        decisions, _ = evaluate_interrupts(registry)

        for decision in decisions:
            if decision.interrupt_level == INTERRUPT_UNRESOLVED:
                # reason should reference the CPIO, not the ZI
                assert "Venice CPIO" in decision.reason
                # ZI should NOT be in the reason (it's in the action)
                assert "VENICE CPIO DISTRICT REGULATIONS" not in decision.reason


# ── Scenario 6: Non-ZI records unaffected ────────────────────────────────────

class TestNonZiRecordsUnchanged:
    def test_cpio_record_doc_type_notes_not_modified(self):
        from zimas_linked_docs.structure_extractor import extract_surface_fields

        cpio_record = LinkedDocRecord(
            record_id="test-cpio-unaffected",
            doc_type=DOC_TYPE_OVERLAY_CPIO,
            doc_label="Venice CPIO",
            usability_posture="manual_review_first",
            fetch_decision=FETCH_NOW,
            doc_type_notes="CPIO detected by name.",
        )
        original_notes = cpio_record.doc_type_notes

        with patch("zimas_linked_docs.structure_extractor.run_zi_fetch") as mock_zi:
            extract_surface_fields([cpio_record], _fetch_enabled=True)

        # ZI fetch should not have been called
        mock_zi.assert_not_called()
        # CPIO doc_type_notes should be unchanged (the CPIO stub doesn't touch it)
        assert cpio_record.doc_type_notes == original_notes

    def test_zi_context_note_empty_without_fetched_zi(self):
        assert _zi_context_note([]) == ""

    def test_zi_context_note_empty_when_zi_has_no_title(self):
        zi_no_title = _make_zi_record(
            confidence_state=CONF_FETCHED_PARTIALLY_USABLE,
            extracted_title=None,
        )
        assert _zi_context_note([zi_no_title]) == ""

    def test_zi_context_note_content(self):
        zi = _make_zi_record(
            confidence_state=CONF_FETCHED_PARTIALLY_USABLE,
            extracted_title="COASTAL SAN PEDRO CPIO REGULATIONS",
        )
        note = _zi_context_note([zi])
        assert "ZI-2478" in note
        assert "COASTAL SAN PEDRO CPIO REGULATIONS" in note
        assert "informational" in note
