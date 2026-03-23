"""ZI document fetch and minimal header extraction for zimas_linked_docs.

Isolated reimplementation of the ZI fetch/cache/extraction logic originally
in governing_docs. Cannot import from governing_docs due to module isolation
constraint (zimas_linked_docs must be import-independent of all other modules).

Verified URL pattern: https://zimas.lacity.org/documents/zoneinfo/ZI{digits}.pdf
Source: governing_docs/document_fetcher.py, confirmed working for ZI2478, ZI2130.

Regex patterns: replicated from governing_docs/zi_extractor.py.

Conservative posture:
  - HEAD verification failure is non-blocking (ZIMAS HEAD is unreliable)
  - GET failure → fetch_status = "failed"
  - Successful GET (or cache hit) + text extraction → fetch_status = "success"
  - No rule interpretation: only header metadata extracted (title, ordinance, date)
"""

from __future__ import annotations

import json
import logging
import re
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


_ZI_PDF_BASE = "https://zimas.lacity.org/documents/zoneinfo"
_DEFAULT_TIMEOUT = 30
_MIN_REQUEST_INTERVAL_SEC = 2.0
_MIN_GOOD_TEXT_LENGTH = 50
_USER_AGENT = "KFA-GSheet-Calc/1.0"

# Module-level rate-limit state (mirrors governing_docs/document_fetcher.py pattern)
_last_request_time: float = 0.0

# Extract digit portion from ZI labels: "ZI-2478", "ZI2478", "ZI-02478"
_RE_ZI_LABEL = re.compile(r"^ZI-?(\d{3,5})$", re.IGNORECASE)

# Header extraction patterns — replicated from governing_docs/zi_extractor.py
_HEADER_ORD = re.compile(
    r"^ORDINANCE\s+NO\.?\s*([\d,]+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_HEADER_ZI = re.compile(
    r"^(?:ZI\s+NO\.|ZONING\s+INFORMATION\s+NO\.)\s*([\d]+)",
    re.MULTILINE | re.IGNORECASE,
)
_HEADER_DATE = re.compile(
    r"EFFECTIVE\s+DATE:\s*(.+?)$",
    re.MULTILINE | re.IGNORECASE,
)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ZIFetchResult:
    """Result of a ZI document fetch-and-extract attempt."""
    doc_label: str              # e.g. "ZI-2478"
    url: str | None = None

    url_verified: bool = False  # HEAD returned 200
    url_verify_notes: str = ""

    fetch_status: str = "not_attempted"  # success / failed / not_attempted
    fetch_notes: str = ""
    cached_path: Path | None = None

    extracted_title: str | None = None
    extracted_ordinance_number: str | None = None
    extracted_effective_date: str | None = None
    # ZI number as it appears in the PDF header (digits only, e.g. "2478").
    # Used to cross-check against the expected ZI number from doc_label.
    # None if the header ZI line was not found in the PDF.
    header_zi_number: str | None = None
    extraction_quality: str = "not_attempted"  # good / weak / failed / not_attempted
    extraction_notes: str = ""


# ── URL utilities ─────────────────────────────────────────────────────────────

def extract_zi_number(doc_label: str) -> str | None:
    """Extract the numeric ZI code from a doc_label string.

    Returns digit string (e.g. "2478") or None if not a valid ZI label.
    Accepts: "ZI-2478", "ZI2478", "zi-2478"
    """
    m = _RE_ZI_LABEL.match(doc_label.strip())
    return m.group(1) if m else None


def build_zi_url(doc_label: str) -> str | None:
    """Build the verified ZI PDF URL from a doc_label.

    Pattern: https://zimas.lacity.org/documents/zoneinfo/ZI{digits}.pdf
    Returns None if doc_label is not a valid ZI label.
    """
    digits = extract_zi_number(doc_label)
    if not digits:
        return None
    return f"{_ZI_PDF_BASE}/ZI{digits}.pdf"


# ── Rate limiting ─────────────────────────────────────────────────────────────

def _rate_limit() -> None:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL_SEC:
        time.sleep(_MIN_REQUEST_INTERVAL_SEC - elapsed)
    _last_request_time = time.time()


# ── HTTP operations ───────────────────────────────────────────────────────────

def verify_zi_url(url: str, timeout: int = _DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """HEAD-verify a ZI PDF URL.

    Returns (verified: bool, notes: str).
    Failure is non-blocking — ZIMAS HEAD requests are unreliable and the
    caller should proceed to GET regardless.
    """
    try:
        import requests
    except ImportError:
        return False, "requests not installed; skipping URL verification"

    try:
        _rate_limit()
        resp = requests.head(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code == 200:
            return True, "HEAD 200 OK"
        return False, f"HEAD returned {resp.status_code}"
    except Exception as e:
        return False, f"HEAD request failed: {e}"


def _get_default_cache_dir() -> Path:
    try:
        from config.settings import RAW_CACHE_DIR
        return RAW_CACHE_DIR / "documents"
    except (ImportError, AttributeError):
        return Path.home() / ".cache" / "gsheet_calc" / "documents"


def fetch_zi_pdf(
    url: str,
    cache_dir: Path,
    timeout: int = _DEFAULT_TIMEOUT,
) -> tuple[Path | None, str, str]:
    """Fetch a ZI PDF and cache it locally.

    Returns (cache_path | None, fetch_status, notes).
    fetch_status values: "success" / "cached" / "failed"

    Cache layout: {cache_dir}/zi_document/ZI{digits}.pdf
    Matching the layout used by governing_docs/document_fetcher.py so both
    modules share the same cached files.
    """
    try:
        import requests
    except ImportError:
        return None, "failed", "requests not installed"

    filename = url.rsplit("/", 1)[-1]  # e.g. "ZI2478.pdf"
    doc_subdir = cache_dir / "zi_document"
    doc_subdir.mkdir(parents=True, exist_ok=True)
    cache_path = doc_subdir / filename
    meta_path = cache_path.with_suffix(".meta.json")

    # Cache hit
    if cache_path.exists() and meta_path.exists():
        return cache_path, "cached", f"Cache hit: {cache_path}"

    # Fetch
    try:
        _rate_limit()
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
        resp.raise_for_status()

        cache_path.write_bytes(resp.content)
        meta = {
            "url": url,
            "content_type": resp.headers.get("Content-Type"),
            "content_length": len(resp.content),
            "status_code": resp.status_code,
        }
        meta_path.write_text(json.dumps(meta, indent=2))
        return cache_path, "success", f"Fetched {len(resp.content)} bytes"

    except Exception as e:
        return None, "failed", f"GET failed: {e}"


# ── PDF header extraction ─────────────────────────────────────────────────────

def extract_zi_header(
    pdf_path: Path,
) -> tuple[str | None, str | None, str | None, str | None, str]:
    """Extract header fields from a ZI PDF using pdfplumber.

    Returns (title, ordinance_number, effective_date, header_zi_number, quality).
    quality: "good" / "weak" / "failed"

    header_zi_number is the ZI number as printed in the PDF header (digits only,
    e.g. "2478"). Used by callers to cross-check against the expected ZI from
    doc_label. None if the header ZI line was not found.

    Only reads the first page. Stops before rule content (COMMENTS,
    INSTRUCTIONS, BACKGROUND sections). Does not harvest body references.
    """
    if not pdf_path.exists():
        return None, None, None, None, "failed"

    try:
        import pdfplumber
    except ImportError:
        return None, None, None, None, "failed"

    first_page_text = ""
    try:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            logging.getLogger("pdfplumber").setLevel(logging.ERROR)
            logging.getLogger("pdfminer").setLevel(logging.ERROR)

            with pdfplumber.open(str(pdf_path)) as pdf:
                if pdf.pages:
                    try:
                        first_page_text = pdf.pages[0].extract_text() or ""
                    except Exception:
                        pass
    except Exception:
        return None, None, None, None, "failed"

    char_count = len(first_page_text.strip())
    if char_count == 0:
        return None, None, None, None, "failed"
    quality = "good" if char_count >= _MIN_GOOD_TEXT_LENGTH else "weak"

    header_region = first_page_text[:500]

    ordinance_number: str | None = None
    m = _HEADER_ORD.search(header_region)
    if m:
        ordinance_number = m.group(1).replace(",", "").strip()

    effective_date: str | None = None
    m = _HEADER_DATE.search(header_region)
    if m:
        effective_date = m.group(1).strip()

    header_zi_number: str | None = None
    m = _HEADER_ZI.search(header_region)
    if m:
        header_zi_number = m.group(1).strip()

    # Title: lines after ZI/ordinance header lines, before section keywords
    title: str | None = None
    title_lines: list[str] = []
    past_header = False
    for line in first_page_text.split("\n"):
        stripped = line.strip()
        if _HEADER_ZI.search(stripped) or _HEADER_ORD.search(stripped):
            past_header = True
            continue
        if _HEADER_DATE.search(stripped):
            continue
        if past_header and stripped:
            if stripped.upper().startswith(("COMMENT", "INSTRUCTION", "BACKGROUND")):
                break
            title_lines.append(stripped)
    if title_lines:
        title = " ".join(title_lines[:3]).strip()

    return title, ordinance_number, effective_date, header_zi_number, quality


# ── Main entry point ──────────────────────────────────────────────────────────

def run_zi_fetch(
    doc_label: str,
    cache_dir: Path | None = None,
    *,
    verify_url: bool = True,
    timeout: int = _DEFAULT_TIMEOUT,
) -> ZIFetchResult:
    """Fetch and minimally extract a ZI document.

    Called by structure_extractor._extract_zi() when fetch_enabled=True.

    Args:
        doc_label:  e.g. "ZI-2478"
        cache_dir:  local cache directory; defaults to RAW_CACHE_DIR/documents
        verify_url: attempt HEAD verification (non-blocking on failure)
        timeout:    HTTP timeout in seconds

    Returns ZIFetchResult with fetch status and extracted surface fields.
    """
    result = ZIFetchResult(doc_label=doc_label)

    # Build URL
    url = build_zi_url(doc_label)
    if not url:
        result.fetch_status = "failed"
        result.fetch_notes = (
            f"Could not build ZI PDF URL from doc_label={doc_label!r}. "
            "Expected format: ZI-<digits> or ZI<digits>."
        )
        return result

    result.url = url

    # Optional HEAD verification — non-blocking
    if verify_url:
        verified, notes = verify_zi_url(url, timeout=timeout)
        result.url_verified = verified
        result.url_verify_notes = notes
        # Proceed to GET regardless — ZIMAS HEAD responses are unreliable

    # Fetch (or cache hit)
    effective_cache_dir = cache_dir if cache_dir is not None else _get_default_cache_dir()
    cache_path, status, notes = fetch_zi_pdf(url, effective_cache_dir, timeout=timeout)

    if status == "failed":
        result.fetch_status = "failed"
        result.fetch_notes = notes
        return result

    result.cached_path = cache_path
    # Normalise "cached" → "success" so confidence.py rule fires uniformly
    result.fetch_status = "success"
    result.fetch_notes = notes

    # Extract header fields from cached PDF
    title, ordinance_number, effective_date, header_zi_number, quality = extract_zi_header(cache_path)
    result.extracted_title = title
    result.extracted_ordinance_number = ordinance_number
    result.extracted_effective_date = effective_date
    result.header_zi_number = header_zi_number
    result.extraction_quality = quality

    notes_parts = [f"Extraction quality: {quality}"]
    if effective_date:
        notes_parts.append(f"Effective date: {effective_date}")
    if not title:
        notes_parts.append("Title not extracted")
    result.extraction_notes = "; ".join(notes_parts)

    return result
