"""Tests for ZI PDF text extraction and reference harvesting.

Uses real ZI PDF fixtures:
  - ZI2478.pdf (San Pedro CPIO — has ordinance 185539, CPIO references)
  - ZI2130.pdf (Enterprise Zone — simpler, no ordinance cross-reference)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governing_docs.zi_extractor import (
    ExtractionQuality,
    ReferenceConfidence,
    extract_zi_text,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_ZI_2478 = _FIXTURE_DIR / "ZI2478.pdf"
_ZI_2130 = _FIXTURE_DIR / "ZI2130.pdf"


@pytest.fixture()
def zi2478_result():
    return extract_zi_text(_ZI_2478)


@pytest.fixture()
def zi2130_result():
    return extract_zi_text(_ZI_2130)


# ============================================================
# Basic extraction tests
# ============================================================

class TestBasicExtraction:

    def test_zi2478_quality_good(self, zi2478_result):
        assert zi2478_result.quality == ExtractionQuality.GOOD

    def test_zi2130_quality_good(self, zi2130_result):
        assert zi2130_result.quality == ExtractionQuality.GOOD

    def test_zi2478_page_count(self, zi2478_result):
        assert zi2478_result.page_count == 3

    def test_zi2130_page_count(self, zi2130_result):
        assert zi2130_result.page_count == 1

    def test_zi2478_has_text(self, zi2478_result):
        assert zi2478_result.char_count > 1000

    def test_zi2130_has_text(self, zi2130_result):
        assert zi2130_result.char_count > 200

    def test_zi_code_from_filename(self, zi2478_result):
        assert zi2478_result.zi_code == "ZI2478"

    def test_extraction_error_none(self, zi2478_result):
        assert zi2478_result.extraction_error is None


# ============================================================
# Header parsing
# ============================================================

class TestHeaderParsing:

    def test_zi2478_header_zi_number(self, zi2478_result):
        assert zi2478_result.header_zi_number == "2478"

    def test_zi2478_header_ordinance(self, zi2478_result):
        """ZI-2478 declares ORDINANCE NO. 185539 in its header."""
        assert zi2478_result.header_ordinance_number == "185539"

    def test_zi2478_header_effective_date(self, zi2478_result):
        assert zi2478_result.header_effective_date is not None
        assert "2018" in zi2478_result.header_effective_date

    def test_zi2478_header_council_district(self, zi2478_result):
        assert zi2478_result.header_council_district is not None
        assert "15" in zi2478_result.header_council_district

    def test_zi2478_header_title(self, zi2478_result):
        """Title should contain 'SAN PEDRO CPIO' or similar."""
        assert zi2478_result.header_title is not None
        assert "SAN PEDRO" in zi2478_result.header_title.upper()

    def test_zi2130_header_zi_number(self, zi2130_result):
        assert zi2130_result.header_zi_number == "2130"

    def test_zi2130_no_header_ordinance(self, zi2130_result):
        """ZI-2130 has no ORDINANCE NO. in its header."""
        assert zi2130_result.header_ordinance_number is None


# ============================================================
# Ordinance reference harvesting
# ============================================================

class TestOrdinanceHarvesting:

    def test_zi2478_finds_185539(self, zi2478_result):
        """ZI-2478 should find ordinance 185539."""
        ord_refs = zi2478_result.ordinance_references
        ord_values = [r.value for r in ord_refs]
        assert "185539" in ord_values

    def test_zi2478_185539_is_direct_header(self, zi2478_result):
        """185539 appears in the header — confidence should be direct_header."""
        ord_185539 = [
            r for r in zi2478_result.ordinance_references
            if r.value == "185539"
        ]
        assert len(ord_185539) >= 1
        header_match = [r for r in ord_185539 if r.confidence == ReferenceConfidence.DIRECT_HEADER]
        assert len(header_match) >= 1

    def test_zi2478_has_text_snippet(self, zi2478_result):
        """Ordinance references should have provenance snippets."""
        for ref in zi2478_result.ordinance_references:
            assert ref.text_snippet is not None
            assert len(ref.text_snippet) > 10

    def test_zi2478_has_page_number(self, zi2478_result):
        for ref in zi2478_result.ordinance_references:
            assert ref.page_number is not None
            assert ref.page_number >= 1

    def test_zi2130_no_ordinance_refs(self, zi2130_result):
        """ZI-2130 should have no ordinance references."""
        assert len(zi2130_result.ordinance_references) == 0

    def test_body_mention_separate_from_header(self, zi2478_result):
        """If 185539 appears both in header and body, body mention
        should not create a duplicate direct_header entry."""
        ord_185539 = [
            r for r in zi2478_result.ordinance_references
            if r.value == "185539"
        ]
        header_count = sum(
            1 for r in ord_185539
            if r.confidence == ReferenceConfidence.DIRECT_HEADER
        )
        # Should have exactly 1 direct_header for 185539
        assert header_count == 1


# ============================================================
# CPIO mention harvesting
# ============================================================

class TestCPIOMentions:

    def test_zi2478_has_cpio_mentions(self, zi2478_result):
        """ZI-2478 (San Pedro CPIO) should have CPIO mentions."""
        assert len(zi2478_result.cpio_mentions) > 0

    def test_zi2478_cpio_includes_san_pedro(self, zi2478_result):
        cpio_values = [r.value for r in zi2478_result.cpio_mentions]
        assert any("San Pedro" in v for v in cpio_values) or any("CPIO" in v for v in cpio_values)

    def test_zi2130_no_cpio_mentions(self, zi2130_result):
        assert len(zi2130_result.cpio_mentions) == 0


# ============================================================
# No false reference inflation
# ============================================================

class TestNoFalseInflation:

    def test_zi2130_no_spurious_ordinances(self, zi2130_result):
        """ZI-2130 mentions parking section numbers but they should NOT
        be treated as ordinance numbers."""
        for ref in zi2130_result.references:
            if ref.reference_type == "ordinance":
                # Any ordinance found should be a real 5-7 digit number
                assert ref.value.isdigit()
                assert len(ref.value) >= 5

    def test_zi2478_no_duplicate_refs(self, zi2478_result):
        """Same ordinance number should not appear with same confidence twice."""
        seen = set()
        for ref in zi2478_result.ordinance_references:
            key = (ref.value, ref.confidence)
            assert key not in seen, f"Duplicate: {ref.value} {ref.confidence}"
            seen.add(key)


# ============================================================
# Extraction failure / edge cases
# ============================================================

class TestExtractionFailures:

    def test_missing_file(self):
        result = extract_zi_text("/nonexistent/path/ZI9999.pdf")
        assert result.quality == ExtractionQuality.FAILED
        assert result.extraction_error is not None
        assert "not found" in result.extraction_error.lower()

    def test_invalid_pdf(self, tmp_path):
        """A non-PDF file should fail gracefully."""
        fake_pdf = tmp_path / "ZI0000.pdf"
        fake_pdf.write_text("this is not a PDF")
        result = extract_zi_text(fake_pdf)
        assert result.quality == ExtractionQuality.FAILED
        assert result.extraction_error is not None

    def test_empty_pdf(self, tmp_path):
        """An empty file should fail gracefully."""
        empty = tmp_path / "ZI0001.pdf"
        empty.write_bytes(b"")
        result = extract_zi_text(empty)
        assert result.quality == ExtractionQuality.FAILED


# ============================================================
# Weak extraction
# ============================================================

class TestWeakExtraction:

    def test_weak_quality_threshold(self, tmp_path):
        """A PDF with very little text should be marked as weak.
        We can't easily create such a PDF in tests, so we test the
        threshold indirectly via the quality assessment logic."""
        # This is a structural test — the quality check uses char_count >= 50
        from governing_docs.zi_extractor import _MIN_GOOD_TEXT_LENGTH
        assert _MIN_GOOD_TEXT_LENGTH == 50
