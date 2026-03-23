"""Focused tests for ZI document fetch and extraction.

Six scenarios:
1. URL building from valid and invalid ZI labels
2. URL verification success (mock HEAD 200)
3. URL verification timeout — non-blocking (GET proceeds regardless)
4. Successful fetch + cache (mock GET returns PDF bytes)
5. Fetch failure (HTTP error → fetch_status="failed")
6. No accidental activation for non-ZI doc types

End-to-end: mock HTTP, real pdfplumber, real ZI2478.pdf fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zimas_linked_docs.zi_fetch import (
    ZIFetchResult,
    build_zi_url,
    extract_zi_header,
    extract_zi_number,
    fetch_zi_pdf,
    run_zi_fetch,
    verify_zi_url,
)


_ZI_PDF_BASE = "https://zimas.lacity.org/documents/zoneinfo"
_FIXTURE_DIR = (
    Path(__file__).parent.parent.parent / "governing_docs" / "tests" / "fixtures"
)
_ZI2478_PDF = _FIXTURE_DIR / "ZI2478.pdf"
_ZI2130_PDF = _FIXTURE_DIR / "ZI2130.pdf"


# ── Scenario 1: URL building ──────────────────────────────────────────────────

class TestBuildZiUrl:
    def test_standard_hyphenated_label(self):
        assert build_zi_url("ZI-2478") == f"{_ZI_PDF_BASE}/ZI2478.pdf"

    def test_no_hyphen_label(self):
        assert build_zi_url("ZI2478") == f"{_ZI_PDF_BASE}/ZI2478.pdf"

    def test_lowercase(self):
        assert build_zi_url("zi-2478") == f"{_ZI_PDF_BASE}/ZI2478.pdf"

    def test_whitespace_stripped(self):
        assert build_zi_url("  ZI-2478  ") == f"{_ZI_PDF_BASE}/ZI2478.pdf"

    def test_cpio_returns_none(self):
        assert build_zi_url("Venice CPIO") is None

    def test_empty_returns_none(self):
        assert build_zi_url("") is None

    def test_specific_plan_returns_none(self):
        assert build_zi_url("Venice Specific Plan") is None

    def test_ordinance_returns_none(self):
        assert build_zi_url("O-186481") is None

    def test_extract_zi_number_hyphenated(self):
        assert extract_zi_number("ZI-2478") == "2478"

    def test_extract_zi_number_no_hyphen(self):
        assert extract_zi_number("ZI2478") == "2478"

    def test_extract_zi_number_non_zi(self):
        assert extract_zi_number("overlay") is None

    def test_extract_zi_number_short_digits(self):
        # 3-digit ZI codes are valid
        assert extract_zi_number("ZI-123") == "123"

    def test_extract_zi_number_too_many_digits(self):
        # 6+ digits not a valid ZI label
        assert extract_zi_number("ZI-123456") is None


# ── Scenario 2: URL verification success ─────────────────────────────────────

class TestVerifyZiUrlSuccess:
    def test_head_200_returns_verified(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.head", return_value=mock_resp):
            with patch("zimas_linked_docs.zi_fetch._rate_limit"):
                verified, notes = verify_zi_url(f"{_ZI_PDF_BASE}/ZI2478.pdf")
        assert verified is True
        assert "200" in notes

    def test_head_404_returns_not_verified(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("requests.head", return_value=mock_resp):
            with patch("zimas_linked_docs.zi_fetch._rate_limit"):
                verified, notes = verify_zi_url(f"{_ZI_PDF_BASE}/ZI9999.pdf")
        assert verified is False
        assert "404" in notes


# ── Scenario 3: HEAD timeout is non-blocking ──────────────────────────────────

class TestVerifyZiUrlNonBlocking:
    def test_head_timeout_not_verified(self):
        """HEAD timeout should return verified=False but not raise."""
        import requests as req_lib
        with patch("requests.head", side_effect=req_lib.Timeout("timeout")):
            with patch("zimas_linked_docs.zi_fetch._rate_limit"):
                verified, notes = verify_zi_url(f"{_ZI_PDF_BASE}/ZI2478.pdf")
        assert verified is False
        assert notes  # Some explanation in notes

    def test_run_zi_fetch_proceeds_after_head_failure(self, tmp_path):
        """fetch_status=success even when HEAD verification failed."""
        import requests as req_lib

        pdf_bytes = _ZI2478_PDF.read_bytes()
        mock_get_resp = MagicMock()
        mock_get_resp.content = pdf_bytes
        mock_get_resp.headers = {"Content-Type": "application/pdf"}
        mock_get_resp.status_code = 200
        mock_get_resp.raise_for_status = lambda: None

        with patch("requests.head", side_effect=req_lib.Timeout("timeout")):
            with patch("requests.get", return_value=mock_get_resp):
                with patch("zimas_linked_docs.zi_fetch._rate_limit"):
                    result = run_zi_fetch("ZI-2478", cache_dir=tmp_path)

        assert result.fetch_status == "success"
        assert result.url_verified is False
        assert result.url_verify_notes  # Records the HEAD failure


# ── Scenario 4: Successful fetch + cache ─────────────────────────────────────

class TestFetchZiPdf:
    def test_successful_fetch_creates_cache_files(self, tmp_path):
        pdf_bytes = b"%PDF-1.4 test content" + b"A" * 100
        mock_resp = MagicMock()
        mock_resp.content = pdf_bytes
        mock_resp.headers = {"Content-Type": "application/pdf"}
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None

        with patch("requests.get", return_value=mock_resp):
            with patch("zimas_linked_docs.zi_fetch._rate_limit"):
                cache_path, status, notes = fetch_zi_pdf(
                    f"{_ZI_PDF_BASE}/ZI2478.pdf", tmp_path
                )

        assert status == "success"
        assert cache_path is not None
        assert cache_path.exists()
        assert cache_path.read_bytes() == pdf_bytes

        meta_path = cache_path.with_suffix(".meta.json")
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["url"] == f"{_ZI_PDF_BASE}/ZI2478.pdf"
        assert meta["content_length"] == len(pdf_bytes)

    def test_cache_hit_skips_network(self, tmp_path):
        """Second request for the same ZI should not make an HTTP call."""
        doc_dir = tmp_path / "zi_document"
        doc_dir.mkdir(parents=True)
        cache_path = doc_dir / "ZI2478.pdf"
        cache_path.write_bytes(b"cached content")
        meta_path = cache_path.with_suffix(".meta.json")
        meta_path.write_text(json.dumps({"url": "...", "content_type": "application/pdf"}))

        with patch("requests.get") as mock_get:
            result_path, status, notes = fetch_zi_pdf(
                f"{_ZI_PDF_BASE}/ZI2478.pdf", tmp_path
            )

        mock_get.assert_not_called()
        assert status == "cached"
        assert result_path == cache_path

    def test_run_zi_fetch_normalises_cached_to_success(self, tmp_path):
        """Cache hit should yield fetch_status='success' for confidence.py."""
        # Pre-populate cache
        doc_dir = tmp_path / "zi_document"
        doc_dir.mkdir(parents=True)
        pdf_path = doc_dir / "ZI2478.pdf"
        pdf_path.write_bytes(_ZI2478_PDF.read_bytes())
        meta_path = pdf_path.with_suffix(".meta.json")
        meta_path.write_text(json.dumps({"url": "...", "content_type": "application/pdf"}))

        with patch("requests.head"):
            with patch("zimas_linked_docs.zi_fetch._rate_limit"):
                result = run_zi_fetch("ZI-2478", cache_dir=tmp_path)

        assert result.fetch_status == "success"
        assert result.extracted_title is not None  # Real PDF extracted


# ── End-to-end: mock HTTP, real pdfplumber, real fixture ─────────────────────

class TestExtractZiHeader:
    def test_zi2478_header_extraction(self):
        """ZI2478.pdf fixture: ordinance 185539, title about San Pedro CPIO."""
        pytest.importorskip("pdfplumber")
        title, ordinance, date, quality = extract_zi_header(_ZI2478_PDF)
        assert quality == "good"
        assert ordinance == "185539"
        assert title is not None
        assert len(title) > 5

    def test_zi2130_header_extraction(self):
        """ZI2130.pdf fixture: no ordinance in header (Enterprise Zone)."""
        pytest.importorskip("pdfplumber")
        title, ordinance, date, quality = extract_zi_header(_ZI2130_PDF)
        assert quality == "good"
        assert title is not None

    def test_missing_file_returns_failed(self, tmp_path):
        title, ordinance, date, quality = extract_zi_header(tmp_path / "missing.pdf")
        assert quality == "failed"
        assert title is None


class TestRunZiFetchEndToEnd:
    def test_fetch_zi2478_with_mock_http(self, tmp_path):
        """Full path: mock GET returns real ZI2478 bytes, extraction produces title."""
        pytest.importorskip("pdfplumber")
        pdf_bytes = _ZI2478_PDF.read_bytes()
        mock_resp = MagicMock()
        mock_resp.content = pdf_bytes
        mock_resp.headers = {"Content-Type": "application/pdf"}
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None

        with patch("requests.head") as mock_head:
            mock_head.return_value.status_code = 200
            with patch("requests.get", return_value=mock_resp):
                with patch("zimas_linked_docs.zi_fetch._rate_limit"):
                    result = run_zi_fetch("ZI-2478", cache_dir=tmp_path)

        assert result.fetch_status == "success"
        assert result.url == f"{_ZI_PDF_BASE}/ZI2478.pdf"
        assert result.url_verified is True
        assert result.extracted_title is not None
        assert result.extracted_ordinance_number == "185539"
        assert result.extraction_quality == "good"
        assert result.cached_path is not None
        assert result.cached_path.exists()


# ── Scenario 5: Fetch failure ─────────────────────────────────────────────────

class TestFetchFailure:
    def test_http_error_returns_failed(self, tmp_path):
        import requests as req_lib
        with patch("requests.get", side_effect=req_lib.HTTPError("404 Not Found")):
            with patch("zimas_linked_docs.zi_fetch._rate_limit"):
                cache_path, status, notes = fetch_zi_pdf(
                    f"{_ZI_PDF_BASE}/ZI9999.pdf", tmp_path
                )

        assert status == "failed"
        assert cache_path is None
        assert "GET failed" in notes

    def test_run_zi_fetch_propagates_failed_status(self, tmp_path):
        import requests as req_lib
        with patch("requests.head"):
            with patch("requests.get", side_effect=req_lib.HTTPError("404")):
                with patch("zimas_linked_docs.zi_fetch._rate_limit"):
                    result = run_zi_fetch("ZI-9999", cache_dir=tmp_path)

        assert result.fetch_status == "failed"
        assert result.extracted_title is None
        assert result.extracted_ordinance_number is None

    def test_invalid_label_fails_before_network(self, tmp_path):
        """Invalid doc_label should fail immediately without any HTTP call."""
        with patch("requests.get") as mock_get:
            with patch("requests.head") as mock_head:
                result = run_zi_fetch("CPIO-Venice", cache_dir=tmp_path)

        assert result.fetch_status == "failed"
        assert result.url is None
        mock_get.assert_not_called()
        mock_head.assert_not_called()


# ── Scenario 6: No accidental activation for non-ZI doc types ────────────────

class TestNoAccidentalActivation:
    def test_cpio_record_not_routed_to_zi_fetch(self):
        """CPIO fetch_now records go to _extract_cpio stub, not _extract_zi."""
        from zimas_linked_docs.models import (
            DOC_TYPE_OVERLAY_CPIO,
            FETCH_NOW,
            LinkedDocRecord,
        )
        from zimas_linked_docs.structure_extractor import (
            extract_surface_fields,
            run_zi_fetch as imported_run_zi_fetch,
        )

        record = LinkedDocRecord(
            record_id="test-cpio-1",
            doc_type=DOC_TYPE_OVERLAY_CPIO,
            doc_label="Venice CPIO",
            usability_posture="manual_review_first",
            fetch_decision=FETCH_NOW,
        )

        with patch("zimas_linked_docs.structure_extractor.run_zi_fetch") as mock_fetch:
            _, _ = extract_surface_fields([record], _fetch_enabled=True)

        mock_fetch.assert_not_called()
        # CPIO stub marks it as skipped
        assert record.fetch_status == "skipped"

    def test_specific_plan_record_is_never_fetch_now(self):
        """Specific plan records have posture=confidence_interrupter_only → FETCH_NEVER."""
        from zimas_linked_docs.models import (
            DOC_TYPE_SPECIFIC_PLAN,
            FETCH_NEVER,
            POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
            LinkedDocRecord,
        )
        from zimas_linked_docs.fetch_policy import assign_fetch_decisions

        record = LinkedDocRecord(
            record_id="test-sp-1",
            doc_type=DOC_TYPE_SPECIFIC_PLAN,
            doc_label="Venice Specific Plan",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        )

        assign_fetch_decisions([record])
        assert record.fetch_decision == FETCH_NEVER

    def test_build_zi_url_rejects_all_non_zi_labels(self):
        non_zi = [
            "Venice CPIO",
            "Venice Specific Plan",
            "O-186481",
            "Q-condition",
            "CPC-2021-1234",
            "ZI",           # no digits
            "ZI-",          # no digits after hyphen
        ]
        for label in non_zi:
            assert build_zi_url(label) is None, f"Expected None for {label!r}"
