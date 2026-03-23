"""ZI PDF text extraction and reference harvesting.

Extracts text from fetched ZI PDFs using pdfplumber, then harvests
structured references (ordinance numbers, case numbers, CPIO mentions).

Does NOT interpret zoning rules. Only extracts evidence.
"""

from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class ExtractionQuality(str, Enum):
    """Quality of text extraction from a PDF."""
    GOOD = "good"           # >= 50 chars of real text
    WEAK = "weak"           # Text extracted but very short or mostly noise
    FAILED = "failed"       # No text extracted or exception


class ReferenceConfidence(str, Enum):
    """How the reference was found in the text."""
    DIRECT_HEADER = "direct_header"
    # Found in a structured header line like "ORDINANCE NO. 185539"
    # at the top of the document. Very high confidence.

    BODY_MENTION = "body_mention"
    # Found in body text with explicit context ("Ordinance No.", "pursuant to Ordinance")
    # High confidence but may refer to a different action than the ZI itself.

    WEAK_PATTERN = "weak_pattern"
    # Regex matched a digit sequence that could be an ordinance number
    # but the surrounding text doesn't confirm it. Low confidence.


@dataclass
class HarvestedReference:
    """A single reference harvested from ZI document text."""
    reference_type: str  # "ordinance", "case_number", "cpio_mention", "lamc_section"
    value: str
    confidence: ReferenceConfidence
    page_number: int | None = None
    text_snippet: str | None = None  # Surrounding text for provenance
    notes: str | None = None


@dataclass
class ZIExtractionResult:
    """Result of extracting text and references from a ZI PDF."""
    zi_code: str
    source_path: str  # Path to the cached PDF
    quality: ExtractionQuality

    # Extracted text
    full_text: str = ""
    page_count: int = 0
    char_count: int = 0

    # Structured header fields (from top of document)
    header_zi_number: str | None = None
    header_ordinance_number: str | None = None
    header_effective_date: str | None = None
    header_title: str | None = None
    header_council_district: str | None = None

    # Harvested references
    references: list[HarvestedReference] = field(default_factory=list)

    # Extraction metadata
    extraction_error: str | None = None
    pdfplumber_warnings: list[str] = field(default_factory=list)

    @property
    def ordinance_references(self) -> list[HarvestedReference]:
        return [r for r in self.references if r.reference_type == "ordinance"]

    @property
    def case_references(self) -> list[HarvestedReference]:
        return [r for r in self.references if r.reference_type == "case_number"]

    @property
    def cpio_mentions(self) -> list[HarvestedReference]:
        return [r for r in self.references if r.reference_type == "cpio_mention"]


# ── Regex patterns ───────────────────────────────────────────────────

# Header ordinance: "ORDINANCE NO. 185,539" or "ORDINANCE NO. 185539"
_HEADER_ORD = re.compile(
    r"^ORDINANCE\s+NO\.?\s*([\d,]+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Body ordinance mention: "Ordinance No. 185,539" or "pursuant to Ordinance 187,096"
_BODY_ORD = re.compile(
    r"(?:Ordinance|Ord\.?)\s+(?:No\.?\s*)?([\d,]{5,})",
    re.IGNORECASE,
)

# Header ZI number
_HEADER_ZI = re.compile(
    r"^(?:ZI\s+NO\.|ZONING\s+INFORMATION\s+NO\.)\s*([\d]+)",
    re.MULTILINE | re.IGNORECASE,
)

# Effective date
_HEADER_DATE = re.compile(
    r"EFFECTIVE\s+DATE:\s*(.+?)$",
    re.MULTILINE | re.IGNORECASE,
)

# Council district
_HEADER_CD = re.compile(
    r"COUNCIL\s+DISTRICT:\s*(.+?)$",
    re.MULTILINE | re.IGNORECASE,
)

# Case numbers
_CASE_PATTERN = re.compile(
    r"((?:CPC|DIR|ZA|ADM|ENV)-\d{4}-\d+[A-Z0-9-]*)",
    re.IGNORECASE,
)

# LAMC section references
_LAMC_PATTERN = re.compile(
    r"(?:LAMC\s+)?Section\s+([\d]+\.[\d]+[A-Z]*(?:\d+)?(?:\([a-z]\))?)",
    re.IGNORECASE,
)

# CPIO mentions
_CPIO_PATTERN = re.compile(
    r"((?:\w+\s+)?Community\s+Plan\s+Implementation\s+Overlay(?:\s+District)?|CPIO)",
    re.IGNORECASE,
)

_MIN_GOOD_TEXT_LENGTH = 50


def extract_zi_text(pdf_path: str | Path) -> ZIExtractionResult:
    """Extract text and harvest references from a ZI PDF.

    Args:
        pdf_path: Path to a cached ZI PDF file.

    Returns:
        ZIExtractionResult with text, header fields, and harvested references.
    """
    pdf_path = Path(pdf_path)
    zi_code = pdf_path.stem  # e.g. "ZI2478"

    result = ZIExtractionResult(
        zi_code=zi_code,
        source_path=str(pdf_path),
        quality=ExtractionQuality.FAILED,
    )

    if not pdf_path.exists():
        result.extraction_error = f"File not found: {pdf_path}"
        return result

    try:
        import pdfplumber
    except ImportError:
        result.extraction_error = "pdfplumber not installed"
        return result

    # Suppress pdfplumber color space warnings (common in ZIMAS PDFs)
    captured_warnings: list[str] = []
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # pdfplumber logs to stderr, not warnings — suppress via logging
            logging.getLogger("pdfplumber").setLevel(logging.ERROR)
            logging.getLogger("pdfminer").setLevel(logging.ERROR)

            with pdfplumber.open(str(pdf_path)) as pdf:
                result.page_count = len(pdf.pages)
                page_texts: list[str] = []

                for i, page in enumerate(pdf.pages):
                    try:
                        text = page.extract_text() or ""
                    except Exception as e:
                        text = ""
                        captured_warnings.append(f"Page {i+1} extraction failed: {e}")
                    page_texts.append(text)

                result.full_text = "\n\n".join(page_texts)
                result.char_count = len(result.full_text.strip())

            for warning in w:
                captured_warnings.append(str(warning.message))

    except Exception as e:
        result.extraction_error = str(e)
        result.quality = ExtractionQuality.FAILED
        return result

    result.pdfplumber_warnings = captured_warnings

    # Assess quality
    if result.char_count >= _MIN_GOOD_TEXT_LENGTH:
        result.quality = ExtractionQuality.GOOD
    elif result.char_count > 0:
        result.quality = ExtractionQuality.WEAK
    else:
        result.quality = ExtractionQuality.FAILED
        result.extraction_error = "No text extracted from any page"
        return result

    # Parse header fields from first page
    first_page = page_texts[0] if page_texts else ""
    _parse_header(first_page, result)

    # Harvest references from all pages
    for i, text in enumerate(page_texts):
        _harvest_references(text, page_number=i + 1, result=result)

    return result


def _parse_header(text: str, result: ZIExtractionResult) -> None:
    """Extract structured header fields from the first page."""
    # Limit to first ~500 chars for header parsing (avoid body matches)
    header_region = text[:500]

    m = _HEADER_ZI.search(header_region)
    if m:
        result.header_zi_number = m.group(1).strip()

    m = _HEADER_ORD.search(header_region)
    if m:
        result.header_ordinance_number = m.group(1).replace(",", "").strip()

    m = _HEADER_DATE.search(header_region)
    if m:
        result.header_effective_date = m.group(1).strip()

    m = _HEADER_CD.search(header_region)
    if m:
        result.header_council_district = m.group(1).strip()

    # Title: text between the header fields and "COMMENTS:" or "INSTRUCTIONS:"
    # This is fragile — only attempt if we have the ZI number line
    lines = text.split("\n")
    title_lines: list[str] = []
    past_header = False
    for line in lines:
        stripped = line.strip()
        if _HEADER_ZI.search(stripped) or _HEADER_ORD.search(stripped):
            past_header = True
            continue
        if _HEADER_DATE.search(stripped) or _HEADER_CD.search(stripped):
            continue
        if past_header and stripped:
            if stripped.upper().startswith(("COMMENT", "INSTRUCTION", "BACKGROUND")):
                break
            title_lines.append(stripped)
    if title_lines:
        result.header_title = " ".join(title_lines[:3]).strip()


def _harvest_references(
    text: str,
    page_number: int,
    result: ZIExtractionResult,
) -> None:
    """Harvest authority references from page text."""
    seen_ords: set[str] = set()
    seen_cases: set[str] = set()

    # Ordinance references
    # First check header pattern (direct_header confidence)
    if page_number == 1:
        for m in _HEADER_ORD.finditer(text[:500]):
            num = m.group(1).replace(",", "").strip()
            if num not in seen_ords:
                seen_ords.add(num)
                snippet = _get_snippet(text, m.start(), m.end())
                result.references.append(HarvestedReference(
                    reference_type="ordinance",
                    value=num,
                    confidence=ReferenceConfidence.DIRECT_HEADER,
                    page_number=page_number,
                    text_snippet=snippet,
                    notes="Found in ZI header as 'ORDINANCE NO.'",
                ))

    # Body ordinance mentions
    for m in _BODY_ORD.finditer(text):
        num = m.group(1).replace(",", "").strip()
        if num in seen_ords:
            continue
        seen_ords.add(num)
        snippet = _get_snippet(text, m.start(), m.end())
        # Determine if this is within header region (first 500 chars of page 1)
        if page_number == 1 and m.start() < 500:
            # May overlap with header — check if already captured
            if any(r.value == num and r.confidence == ReferenceConfidence.DIRECT_HEADER
                   for r in result.references):
                continue
        result.references.append(HarvestedReference(
            reference_type="ordinance",
            value=num,
            confidence=ReferenceConfidence.BODY_MENTION,
            page_number=page_number,
            text_snippet=snippet,
        ))

    # Case numbers
    for m in _CASE_PATTERN.finditer(text):
        case = m.group(1).upper()
        if case in seen_cases:
            continue
        seen_cases.add(case)
        snippet = _get_snippet(text, m.start(), m.end())
        result.references.append(HarvestedReference(
            reference_type="case_number",
            value=case,
            confidence=ReferenceConfidence.BODY_MENTION,
            page_number=page_number,
            text_snippet=snippet,
        ))

    # CPIO mentions
    for m in _CPIO_PATTERN.finditer(text):
        cpio_text = m.group(1).strip()
        snippet = _get_snippet(text, m.start(), m.end())
        result.references.append(HarvestedReference(
            reference_type="cpio_mention",
            value=cpio_text,
            confidence=ReferenceConfidence.BODY_MENTION,
            page_number=page_number,
            text_snippet=snippet,
        ))


def _get_snippet(text: str, start: int, end: int, context: int = 60) -> str:
    """Extract a text snippet around a match for provenance."""
    snippet_start = max(0, start - context)
    snippet_end = min(len(text), end + context)
    snippet = text[snippet_start:snippet_end].strip()
    if snippet_start > 0:
        snippet = "..." + snippet
    if snippet_end < len(text):
        snippet = snippet + "..."
    return snippet
