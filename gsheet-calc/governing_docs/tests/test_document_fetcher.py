"""Tests for document reference building and retrieval.

Tests the reference-building logic (pure data, no network),
cache behavior (filesystem), and fetch paths (mocked HTTP).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from governing_docs.document_fetcher import DocumentFetcher
from governing_docs.document_models import (
    DocumentReference,
    DocumentRetrievalResult,
    DocumentType,
    RetrievalStatus,
    URLConfidence,
)
from governing_docs.models import (
    AuthorityLinkType,
    ParcelAuthorityItem,
    ParcelProfileData,
)
from governing_docs.parcel_profile_parser import parse_profile_response

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_SAN_PEDRO_FIXTURE = _FIXTURE_DIR / "san_pedro_profile.html"


@pytest.fixture()
def san_pedro_profile():
    return parse_profile_response(_SAN_PEDRO_FIXTURE.read_text())


@pytest.fixture()
def fetcher(tmp_path):
    return DocumentFetcher(cache_dir=tmp_path / "doc_cache", min_interval=0.0)


# ============================================================
# Reference building (no network)
# ============================================================

class TestBuildReferences:

    def test_zi_references_built(self, fetcher, san_pedro_profile):
        result = fetcher.build_references(san_pedro_profile, parcel_id="test")
        zi_refs = result.get_by_type(DocumentType.ZI_PDF)
        assert len(zi_refs) >= 5  # San Pedro has 7 ZI items

    def test_zi_url_pattern(self, fetcher, san_pedro_profile):
        result = fetcher.build_references(san_pedro_profile, parcel_id="test")
        zi_2478 = result.get_by_identifier("ZI2478")
        assert zi_2478 is not None
        assert zi_2478.url == "https://zimas.lacity.org/documents/zoneinfo/ZI2478.pdf"
        assert zi_2478.url_confidence == URLConfidence.PATTERN_DERIVED

    def test_zi_code_no_hyphen(self, fetcher, san_pedro_profile):
        """ZI code in URL should not have a hyphen."""
        result = fetcher.build_references(san_pedro_profile, parcel_id="test")
        for ref in result.get_by_type(DocumentType.ZI_PDF):
            assert "-" not in ref.identifier
            assert "ZI-" not in ref.url

    def test_zi_status_is_pattern_only(self, fetcher, san_pedro_profile):
        result = fetcher.build_references(san_pedro_profile, parcel_id="test")
        for ref in result.get_by_type(DocumentType.ZI_PDF):
            assert ref.status == RetrievalStatus.URL_PATTERN_ONLY

    def test_ordinance_references_built(self, fetcher, san_pedro_profile):
        result = fetcher.build_references(san_pedro_profile, parcel_id="test")
        ord_refs = result.get_by_type(DocumentType.ORDINANCE)
        assert len(ord_refs) >= 4  # San Pedro has 5 ordinances

    def test_ordinance_url_unknown(self, fetcher, san_pedro_profile):
        """Ordinance references should have URL_UNKNOWN status."""
        result = fetcher.build_references(san_pedro_profile, parcel_id="test")
        for ref in result.get_by_type(DocumentType.ORDINANCE):
            assert ref.url is None
            assert ref.status == RetrievalStatus.URL_UNKNOWN
            assert ref.url_confidence == URLConfidence.UNKNOWN

    def test_ordinance_has_warning(self, fetcher, san_pedro_profile):
        result = fetcher.build_references(san_pedro_profile, parcel_id="test")
        ord_refs = result.get_by_type(DocumentType.ORDINANCE)
        for ref in ord_refs:
            assert any("angular" in w.lower() or "manual" in w.lower() for w in ref.warnings)

    def test_no_duplicates(self, fetcher, san_pedro_profile):
        result = fetcher.build_references(san_pedro_profile, parcel_id="test")
        ids = [r.identifier for r in result.references]
        assert len(ids) == len(set(ids))

    def test_empty_profile(self, fetcher):
        result = fetcher.build_references(None, parcel_id="test")
        assert len(result.references) == 0

    def test_result_counts(self, fetcher, san_pedro_profile):
        result = fetcher.build_references(san_pedro_profile, parcel_id="test")
        assert result.pending_count >= 5  # ZI refs are pending
        assert result.unknown_url_count >= 4  # Ordinances have unknown URLs
        assert result.fetched_count == 0
        assert result.failed_count == 0


# ============================================================
# URL verification (mocked HTTP)
# ============================================================

class TestVerifyURL:

    def test_head_200_verifies(self, fetcher):
        ref = DocumentReference(
            document_type=DocumentType.ZI_PDF,
            identifier="ZI2478",
            url="https://zimas.lacity.org/documents/zoneinfo/ZI2478.pdf",
            status=RetrievalStatus.URL_PATTERN_ONLY,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/pdf", "Content-Length": "70000"}

        with patch("governing_docs.document_fetcher.requests.head", return_value=mock_resp):
            fetcher.verify_url(ref)

        assert ref.status == RetrievalStatus.URL_VERIFIED
        assert ref.url_confidence == URLConfidence.VERIFIED
        assert ref.content_type == "application/pdf"

    def test_head_404_fails(self, fetcher):
        ref = DocumentReference(
            document_type=DocumentType.ZI_PDF,
            identifier="ZI9999",
            url="https://zimas.lacity.org/documents/zoneinfo/ZI9999.pdf",
            status=RetrievalStatus.URL_PATTERN_ONLY,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.headers = {}

        with patch("governing_docs.document_fetcher.requests.head", return_value=mock_resp):
            fetcher.verify_url(ref)

        assert ref.status == RetrievalStatus.FETCH_FAILED
        assert "404" in ref.fetch_error

    def test_head_timeout_warns(self, fetcher):
        """HEAD timeout should warn, not mark as failed."""
        ref = DocumentReference(
            document_type=DocumentType.ZI_PDF,
            identifier="ZI2478",
            url="https://zimas.lacity.org/documents/zoneinfo/ZI2478.pdf",
            status=RetrievalStatus.URL_PATTERN_ONLY,
        )

        import requests as req_mod
        with patch("governing_docs.document_fetcher.requests.head", side_effect=req_mod.Timeout("timed out")):
            fetcher.verify_url(ref)

        # Should warn, not fail (HEAD timeouts are common on ZIMAS)
        assert any("timed out" in w.lower() for w in ref.warnings)
        assert ref.status != RetrievalStatus.FETCH_FAILED

    def test_no_url_stays_unknown(self, fetcher):
        ref = DocumentReference(
            document_type=DocumentType.ORDINANCE,
            identifier="ORD-185539",
            url=None,
            status=RetrievalStatus.URL_UNKNOWN,
        )
        fetcher.verify_url(ref)
        assert ref.status == RetrievalStatus.URL_UNKNOWN


# ============================================================
# Fetch (mocked HTTP + real cache)
# ============================================================

class TestFetch:

    def test_successful_fetch_caches(self, fetcher):
        ref = DocumentReference(
            document_type=DocumentType.ZI_PDF,
            identifier="ZI2130",
            url="https://zimas.lacity.org/documents/zoneinfo/ZI2130.pdf",
            status=RetrievalStatus.URL_PATTERN_ONLY,
        )

        mock_resp = MagicMock()
        mock_resp.content = b"%PDF-1.4 fake pdf content"
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/pdf"}
        mock_resp.raise_for_status = MagicMock()

        with patch("governing_docs.document_fetcher.requests.get", return_value=mock_resp):
            fetcher.fetch(ref)

        assert ref.status == RetrievalStatus.FETCHED
        assert ref.content_length == len(b"%PDF-1.4 fake pdf content")
        assert ref.fetch_timestamp is not None
        assert ref.cache_key is not None

        # Verify cache file exists
        cached_path = fetcher.get_cached_path(ref)
        assert cached_path is not None
        assert cached_path.exists()
        assert cached_path.read_bytes() == b"%PDF-1.4 fake pdf content"

    def test_cached_fetch_skips_network(self, fetcher):
        """Second fetch for same doc should use cache, not network."""
        ref = DocumentReference(
            document_type=DocumentType.ZI_PDF,
            identifier="ZI2130",
            url="https://zimas.lacity.org/documents/zoneinfo/ZI2130.pdf",
        )

        mock_resp = MagicMock()
        mock_resp.content = b"%PDF-1.4 fake"
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/pdf"}
        mock_resp.raise_for_status = MagicMock()

        with patch("governing_docs.document_fetcher.requests.get", return_value=mock_resp) as mock_get:
            fetcher.fetch(ref)
            assert mock_get.call_count == 1

            # Second fetch — should use cache
            ref2 = DocumentReference(
                document_type=DocumentType.ZI_PDF,
                identifier="ZI2130",
                url="https://zimas.lacity.org/documents/zoneinfo/ZI2130.pdf",
            )
            fetcher.fetch(ref2)
            # Should still be 1 call — cached
            assert mock_get.call_count == 1
            assert ref2.status == RetrievalStatus.FETCHED

    def test_fetch_failure(self, fetcher):
        ref = DocumentReference(
            document_type=DocumentType.ZI_PDF,
            identifier="ZI9999",
            url="https://zimas.lacity.org/documents/zoneinfo/ZI9999.pdf",
        )

        import requests as req_mod
        with patch("governing_docs.document_fetcher.requests.get", side_effect=req_mod.HTTPError("404")):
            fetcher.fetch(ref)

        assert ref.status == RetrievalStatus.FETCH_FAILED
        assert ref.fetch_error is not None

    def test_fetch_no_url(self, fetcher):
        ref = DocumentReference(
            document_type=DocumentType.ORDINANCE,
            identifier="ORD-185539",
            url=None,
        )
        fetcher.fetch(ref)
        assert ref.status == RetrievalStatus.URL_UNKNOWN

    def test_get_cached_path_none_when_not_fetched(self, fetcher):
        ref = DocumentReference(
            document_type=DocumentType.ZI_PDF,
            identifier="ZI2478",
            status=RetrievalStatus.URL_PATTERN_ONLY,
        )
        assert fetcher.get_cached_path(ref) is None


# ============================================================
# fetch_all_zi
# ============================================================

class TestFetchAllZI:

    def test_only_fetches_zi_docs(self, fetcher, san_pedro_profile):
        result = fetcher.build_references(san_pedro_profile, parcel_id="test")

        mock_resp = MagicMock()
        mock_resp.content = b"%PDF fake"
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/pdf"}
        mock_resp.raise_for_status = MagicMock()

        with patch("governing_docs.document_fetcher.requests.get", return_value=mock_resp) as mock_get:
            fetcher.fetch_all_zi(result)

        # Should have called GET only for ZI docs, not ordinances
        zi_count = len(result.get_by_type(DocumentType.ZI_PDF))
        assert mock_get.call_count == zi_count

        # All ZI docs should be fetched
        for ref in result.get_by_type(DocumentType.ZI_PDF):
            assert ref.status == RetrievalStatus.FETCHED

        # Ordinances should still be URL_UNKNOWN
        for ref in result.get_by_type(DocumentType.ORDINANCE):
            assert ref.status == RetrievalStatus.URL_UNKNOWN


# ============================================================
# Metadata/provenance
# ============================================================

class TestMetadata:

    def test_metadata_file_created(self, fetcher):
        ref = DocumentReference(
            document_type=DocumentType.ZI_PDF,
            identifier="ZI1022",
            url="https://zimas.lacity.org/documents/zoneinfo/ZI1022.pdf",
        )

        mock_resp = MagicMock()
        mock_resp.content = b"%PDF content"
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/pdf"}
        mock_resp.raise_for_status = MagicMock()

        with patch("governing_docs.document_fetcher.requests.get", return_value=mock_resp):
            fetcher.fetch(ref)

        # Check metadata file
        cache_path = fetcher.get_cached_path(ref)
        meta_path = cache_path.with_suffix(".meta.json")
        assert meta_path.exists()

        meta = json.loads(meta_path.read_text())
        assert meta["identifier"] == "ZI1022"
        assert meta["document_type"] == "zi_pdf"
        assert meta["url"] == "https://zimas.lacity.org/documents/zoneinfo/ZI1022.pdf"
        assert meta["fetch_timestamp"] is not None
