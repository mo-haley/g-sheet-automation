"""Document reference and retrieval status models.

Tracks the lifecycle of authority documents from reference identification
through retrieval. Does NOT interpret document content — that's a future phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DocumentType(str, Enum):
    """Type of authority document."""
    ZI_PDF = "zi_pdf"
    # Zoning Information document, served as PDF from ZIMAS.
    # URL pattern: https://zimas.lacity.org/documents/zoneinfo/ZI{code}.pdf

    ORDINANCE = "ordinance"
    # City ordinance document. No reliable URL pattern known.
    # PDIS page exists but is an SPA — no direct PDF link.

    CPIO_CHECKLIST = "cpio_checklist"
    # CPIO subarea checklist PDF from planning.lacity.gov overlay pages.
    # URLs are GUID-based, must be harvested per-district.

    CPIO_MAP = "cpio_map"
    # CPIO district map PDF from planning.lacity.gov overlay pages.

    COMMUNITY_PLAN = "community_plan"
    # Community plan document.

    OTHER = "other"


class RetrievalStatus(str, Enum):
    """Lifecycle status of a document retrieval attempt."""
    NOT_ATTEMPTED = "not_attempted"
    URL_VERIFIED = "url_verified"       # HEAD 200 confirmed
    URL_PATTERN_ONLY = "url_pattern_only"  # URL from pattern, not verified
    URL_UNKNOWN = "url_unknown"         # No URL derivable
    FETCHED = "fetched"                 # Binary content cached
    FETCH_FAILED = "fetch_failed"       # Fetch attempted, failed
    TEXT_EXTRACTED = "text_extracted"    # Future: raw text extracted
    EXTRACTION_FAILED = "extraction_failed"  # Future: extraction failed


class URLConfidence(str, Enum):
    """How the URL was determined."""
    VERIFIED = "verified"               # HEAD request confirmed 200
    PATTERN_DERIVED = "pattern_derived"  # Constructed from known pattern
    CURATED = "curated"                 # From a manually-maintained source
    UNKNOWN = "unknown"                 # No URL available


@dataclass
class DocumentReference:
    """A reference to an authority document, with retrieval status."""
    document_type: DocumentType
    identifier: str  # ZI code, ordinance number, etc.

    # URL information
    url: str | None = None
    url_confidence: URLConfidence = URLConfidence.UNKNOWN
    url_source: str | None = None  # How the URL was determined

    # Retrieval status
    status: RetrievalStatus = RetrievalStatus.NOT_ATTEMPTED
    cache_key: str | None = None  # Key in RawCache if fetched

    # Retrieval metadata
    content_type: str | None = None
    content_length: int | None = None
    fetch_timestamp: str | None = None
    fetch_error: str | None = None

    # Provenance
    source_authority_item: str | None = None  # Link back to ParcelAuthorityItem
    warnings: list[str] = field(default_factory=list)


@dataclass
class DocumentRetrievalResult:
    """Result of attempting to retrieve documents for a parcel."""
    parcel_id: str | None = None
    references: list[DocumentReference] = field(default_factory=list)

    @property
    def fetched_count(self) -> int:
        return sum(1 for r in self.references if r.status == RetrievalStatus.FETCHED)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.references if r.status == RetrievalStatus.FETCH_FAILED)

    @property
    def pending_count(self) -> int:
        return sum(
            1 for r in self.references
            if r.status in (RetrievalStatus.NOT_ATTEMPTED, RetrievalStatus.URL_PATTERN_ONLY)
        )

    @property
    def unknown_url_count(self) -> int:
        return sum(1 for r in self.references if r.status == RetrievalStatus.URL_UNKNOWN)

    def get_by_type(self, dt: DocumentType) -> list[DocumentReference]:
        return [r for r in self.references if r.document_type == dt]

    def get_by_identifier(self, identifier: str) -> DocumentReference | None:
        for r in self.references:
            if r.identifier == identifier:
                return r
        return None
