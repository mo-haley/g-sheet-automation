"""Document retrieval for authority references.

Fetches ZI PDFs and other identified documents. Caches binary content
using a file-based cache (separate from JSON RawCache — PDFs are stored
as binary files, not JSON envelopes).

Does NOT interpret document content. Only acquires and caches files.

Verified URL patterns:
  ZI PDFs: https://zimas.lacity.org/documents/zoneinfo/ZI{code}.pdf
    - {code} is digits only (no hyphen), e.g. "2478" not "ZI-2478"
    - Confirmed working for multiple ZI codes

Unverified / not available:
  Ordinance PDFs: no deterministic URL pattern. PDIS page is SPA.
  CPIO checklists: GUID-based URLs, must be harvested per-district.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from governing_docs.document_models import (
    DocumentReference,
    DocumentRetrievalResult,
    DocumentType,
    RetrievalStatus,
    URLConfidence,
)
from governing_docs.models import (
    AuthorityLinkType,
    ControlType,
    ParcelAuthorityItem,
    ParcelProfileData,
)


_ZIMAS_ZI_PDF_BASE = "https://zimas.lacity.org/documents/zoneinfo"
_DEFAULT_USER_AGENT = "KFA-GSheet-Calc/1.0"
_DEFAULT_TIMEOUT = 30
_MIN_REQUEST_INTERVAL_SEC = 2.0


class DocumentFetcher:
    """Fetch and cache authority documents."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        user_agent: str = _DEFAULT_USER_AGENT,
        min_interval: float = _MIN_REQUEST_INTERVAL_SEC,
    ) -> None:
        from config.settings import RAW_CACHE_DIR
        self.cache_dir = cache_dir or (RAW_CACHE_DIR / "documents")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.min_interval = min_interval
        self._last_request_time: float = 0.0

    def build_references(
        self,
        profile: ParcelProfileData | None = None,
        parcel_id: str | None = None,
    ) -> DocumentRetrievalResult:
        """Build document references from profile authority items.

        Does NOT fetch — only identifies URLs and creates reference objects.
        """
        result = DocumentRetrievalResult(parcel_id=parcel_id)

        if not profile:
            return result

        seen_ids: set[str] = set()

        # ZI items → ZI PDFs
        for item in profile.zi_items:
            if item.zi_code:
                code_digits = item.zi_code.replace("ZI-", "").replace("ZI", "")
                ref_id = f"ZI{code_digits}"
                if ref_id in seen_ids:
                    continue
                seen_ids.add(ref_id)

                url = f"{_ZIMAS_ZI_PDF_BASE}/ZI{code_digits}.pdf"
                result.references.append(DocumentReference(
                    document_type=DocumentType.ZI_PDF,
                    identifier=ref_id,
                    url=url,
                    url_confidence=URLConfidence.PATTERN_DERIVED,
                    url_source=f"ZI PDF URL pattern: {_ZIMAS_ZI_PDF_BASE}/ZI{{code}}.pdf",
                    status=RetrievalStatus.URL_PATTERN_ONLY,
                    source_authority_item=item.raw_text,
                ))

        # Ordinances → no URL pattern available
        for item in profile.authority_items:
            if item.link_type == AuthorityLinkType.ORDINANCE and item.ordinance_number:
                ref_id = f"ORD-{item.ordinance_number}"
                if ref_id in seen_ids:
                    continue
                seen_ids.add(ref_id)

                result.references.append(DocumentReference(
                    document_type=DocumentType.ORDINANCE,
                    identifier=ref_id,
                    url=None,
                    url_confidence=URLConfidence.UNKNOWN,
                    url_source="No deterministic URL pattern for ordinance PDFs.",
                    status=RetrievalStatus.URL_UNKNOWN,
                    source_authority_item=item.raw_text,
                    warnings=[
                        "Ordinance PDF URL cannot be constructed. "
                        "PDIS page exists but is an Angular SPA with undiscovered API. "
                        "Manual retrieval or browser-based download required."
                    ],
                ))

        return result

    def verify_url(self, ref: DocumentReference) -> DocumentReference:
        """Verify a pattern-derived URL with a HEAD request.

        Updates the reference status in place and returns it.
        """
        if not ref.url:
            ref.status = RetrievalStatus.URL_UNKNOWN
            return ref

        if ref.status == RetrievalStatus.FETCHED:
            return ref  # Already fetched, no need to verify

        self._rate_limit()
        try:
            resp = requests.head(
                ref.url,
                headers={"User-Agent": self.user_agent},
                timeout=_DEFAULT_TIMEOUT,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                ref.status = RetrievalStatus.URL_VERIFIED
                ref.url_confidence = URLConfidence.VERIFIED
                ref.content_type = resp.headers.get("Content-Type")
                cl = resp.headers.get("Content-Length")
                ref.content_length = int(cl) if cl else None
            else:
                ref.status = RetrievalStatus.FETCH_FAILED
                ref.fetch_error = f"HEAD returned {resp.status_code}"
        except requests.RequestException as e:
            # HEAD timeout is common on ZIMAS — don't mark as failed
            ref.warnings.append(f"HEAD request failed: {e}. Will retry with GET on fetch.")

        return ref

    def fetch(self, ref: DocumentReference) -> DocumentReference:
        """Fetch a document and cache it locally.

        Updates the reference status in place and returns it.
        """
        if not ref.url:
            ref.status = RetrievalStatus.URL_UNKNOWN
            return ref

        # Check cache first
        cache_key = self._cache_key(ref)
        cache_path = self._cache_path(cache_key)
        meta_path = cache_path.with_suffix(".meta.json")

        if cache_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            ref.status = RetrievalStatus.FETCHED
            ref.cache_key = cache_key
            ref.content_type = meta.get("content_type")
            ref.content_length = meta.get("content_length")
            ref.fetch_timestamp = meta.get("fetch_timestamp")
            return ref

        # Fetch
        self._rate_limit()
        try:
            resp = requests.get(
                ref.url,
                headers={"User-Agent": self.user_agent},
                timeout=_DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()

            # Cache binary content
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(resp.content)

            # Cache metadata
            now = datetime.now(timezone.utc).isoformat()
            meta = {
                "url": ref.url,
                "identifier": ref.identifier,
                "document_type": ref.document_type.value,
                "content_type": resp.headers.get("Content-Type"),
                "content_length": len(resp.content),
                "fetch_timestamp": now,
                "status_code": resp.status_code,
            }
            meta_path.write_text(json.dumps(meta, indent=2))

            ref.status = RetrievalStatus.FETCHED
            ref.cache_key = cache_key
            ref.url_confidence = URLConfidence.VERIFIED
            ref.content_type = resp.headers.get("Content-Type")
            ref.content_length = len(resp.content)
            ref.fetch_timestamp = now

        except requests.RequestException as e:
            ref.status = RetrievalStatus.FETCH_FAILED
            ref.fetch_error = str(e)

        return ref

    def fetch_all_zi(self, result: DocumentRetrievalResult) -> DocumentRetrievalResult:
        """Fetch all ZI PDF references in a retrieval result.

        Only fetches ZI documents (verified URL pattern). Skips others.
        """
        for ref in result.references:
            if ref.document_type == DocumentType.ZI_PDF and ref.url:
                self.fetch(ref)
        return result

    def get_cached_path(self, ref: DocumentReference) -> Path | None:
        """Return the local path to a cached document, or None."""
        if ref.status != RetrievalStatus.FETCHED or not ref.cache_key:
            return None
        path = self._cache_path(ref.cache_key)
        return path if path.exists() else None

    def _cache_key(self, ref: DocumentReference) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in ref.identifier)
        return f"{ref.document_type.value}/{safe}"

    def _cache_path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.pdf"

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.time()
