"""ZIMAS linked-document classifier.

Takes LinkedDocCandidate list and produces LinkedDocRecord list.

Classification rules are LA City-specific. They are intentionally conservative:
when the doc type is ambiguous, we assign a more restrictive posture rather
than a more permissive one. An unknown overlay gets confidence_interrupter_only,
not machine_usable.

Deduplication: candidates that refer to the same underlying document are
collapsed into one record. Collapsing rules are conservative — two items are
only collapsed when the match is unambiguous (exact same ZI number, same
ordinance number, same specific plan name from two fields).

All records start at confidence_state = detected_not_interpreted.
The confidence module upgrades this after fetching.
"""

from __future__ import annotations

import re

from zimas_linked_docs.models import (
    LinkedDocCandidate,
    LinkedDocRecord,
    ZimasDocIssue,
    DOC_TYPE_ORDINANCE,
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_OVERLAY_SUPPLEMENTAL,
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_ZI_DOCUMENT,
    DOC_TYPE_MAP_FIGURE_PACKET,
    DOC_TYPE_CASE_DOCUMENT,
    DOC_TYPE_PLANNING_PAGE,
    DOC_TYPE_PDF_ARTIFACT,
    DOC_TYPE_UNKNOWN_ARTIFACT,
    POSTURE_MACHINE_USABLE,
    POSTURE_MANUAL_REVIEW_FIRST,
    POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
    PATTERN_SPECIFIC_PLAN_FIELD,
    PATTERN_OVERLAY_NAME_FIELD,
    PATTERN_Q_CONDITION_FIELD,
    PATTERN_D_LIMITATION_FIELD,
    PATTERN_ZI_NUMBER,
    PATTERN_ORDINANCE_NUMBER,
    PATTERN_CASE_NUMBER,
    PATTERN_URL_IN_TEXT,
    PATTERN_RAW_LAYER_ATTR,
    PATTERN_ZONE_STRING_PARSE,
    URL_CONF_DIRECT_LINK,
    URL_CONF_PORTAL_REDIRECT,
    CONF_DETECTED_NOT_INTERPRETED,
    CONF_DETECTED_URL_UNVERIFIED,
)


# ── Classification helpers ────────────────────────────────────────────────────

def _is_cpio(name: str) -> bool:
    """Return True if the overlay name indicates a CPIO."""
    n = name.upper()
    return "CPIO" in n or "COMMUNITY PLAN IMPLEMENTATION" in n


def _is_supplemental(name: str) -> bool:
    """Return True if the overlay name indicates a supplemental use district."""
    n = name.upper()
    return (
        "SUD" in n
        or "SUPPLEMENTAL USE" in n
        or "SUPPLEMENTAL DISTRICT" in n
    )


def _is_map_figure(name: str, url: str | None) -> bool:
    """Return True if this looks like a map/figure packet rather than an ordinance."""
    if url and any(kw in url.lower() for kw in ("exhibit", "figure", "map", "atlas")):
        return True
    n = name.upper()
    return any(kw in n for kw in ("EXHIBIT", "FIGURE", "MAP SHEET", "ATLAS", "FIGURE SET"))


def _is_planning_page(url: str) -> bool:
    """Return True if the URL looks like a DCP planning page (not a document)."""
    u = url.lower()
    return (
        "planning.lacity.org" in u
        and not u.endswith(".pdf")
        and "pdf" not in u.split("?")[0].lower()
    )


_RE_ZI_EXTRACT = re.compile(r"\bZI-(\d{3,5})\b", re.IGNORECASE)
_RE_ORD_EXTRACT = re.compile(r"\bO-(\d{5,6})\b", re.IGNORECASE)


def _make_record_id(apn: str | None, seq: int) -> str:
    apn_part = apn.replace("-", "").replace(" ", "") if apn else "NOAPN"
    return f"ZDOC-{apn_part}-{seq:03d}"


# ── Main classifier ───────────────────────────────────────────────────────────

def classify_candidates(
    candidates: list[LinkedDocCandidate],
    apn: str | None = None,
) -> tuple[list[LinkedDocRecord], list[ZimasDocIssue]]:
    """Classify candidates into typed records with usability postures.

    Returns (records, issues). Records are not yet assigned fetch decisions
    or confidence states — those come from subsequent pipeline steps.
    """
    records: list[LinkedDocRecord] = []
    issues: list[ZimasDocIssue] = []
    seq = 0

    # Deduplication keys: track what we have already recorded to collapse
    # same-key candidates into one record.
    # Key format: (doc_type, normalized_label)
    seen: dict[tuple[str, str], int] = {}  # → index in records list

    def _record_or_merge(
        candidates_for_record: list[LinkedDocCandidate],
        doc_type: str,
        doc_label: str,
        usability_posture: str,
        doc_type_confidence: str = "provisional",
        doc_type_notes: str = "",
        url: str | None = None,
        url_confidence: str = URL_CONF_PORTAL_REDIRECT,
        source_ordinance_number: str | None = None,
    ) -> None:
        nonlocal seq
        key = (doc_type, doc_label.upper().strip())
        if key in seen:
            # Merge candidate provenance into existing record
            idx = seen[key]
            existing = records[idx]
            for cand in candidates_for_record:
                if cand.candidate_id not in existing.candidate_ids:
                    existing.candidate_ids.append(cand.candidate_id)
                if cand.source_field not in existing.detected_from_fields:
                    existing.detected_from_fields.append(cand.source_field)
                if cand.raw_value not in existing.raw_values:
                    existing.raw_values.append(cand.raw_value)
            # Upgrade URL confidence if incoming is better
            if url and url_confidence == URL_CONF_DIRECT_LINK:
                existing.url = url
                existing.url_confidence = url_confidence
            return

        seq += 1
        initial_conf = (
            CONF_DETECTED_URL_UNVERIFIED
            if (url and url_confidence != URL_CONF_DIRECT_LINK)
            else CONF_DETECTED_NOT_INTERPRETED
        )

        record = LinkedDocRecord(
            record_id=_make_record_id(apn, seq),
            doc_type=doc_type,
            doc_label=doc_label,
            usability_posture=usability_posture,
            detected_from_fields=[c.source_field for c in candidates_for_record],
            raw_values=[c.raw_value for c in candidates_for_record],
            candidate_ids=[c.candidate_id for c in candidates_for_record],
            url=url,
            url_confidence=url_confidence,
            doc_type_confidence=doc_type_confidence,
            doc_type_notes=doc_type_notes,
            confidence_state=initial_conf,
            source_ordinance_number=source_ordinance_number,
        )
        seen[key] = len(records)
        records.append(record)

    # ── Process each candidate ────────────────────────────────────────────────

    for cand in candidates:
        raw = cand.raw_value
        pattern = cand.detected_pattern

        # — Structured field: specific plan —
        if pattern == PATTERN_SPECIFIC_PLAN_FIELD:
            _record_or_merge(
                candidates_for_record=[cand],
                doc_type=DOC_TYPE_SPECIFIC_PLAN,
                doc_label=raw,
                usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                doc_type_confidence="confirmed",
                doc_type_notes=(
                    "Specific plan name from ZIMAS Site.specific_plan field. "
                    "Presence interrupts all calc topics. Contents not interpreted."
                ),
            )

        # — Structured field: Q condition —
        elif pattern == PATTERN_Q_CONDITION_FIELD:
            _record_or_merge(
                candidates_for_record=[cand],
                doc_type=DOC_TYPE_Q_CONDITION,
                doc_label=raw,
                usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                doc_type_confidence="confirmed",
                doc_type_notes=(
                    "Q condition from ZIMAS Site.q_conditions field. "
                    "Restrictions are site-specific and must be read in source document."
                ),
                source_ordinance_number=cand.source_ordinance_number,
            )

        # — Structured field: D limitation —
        elif pattern == PATTERN_D_LIMITATION_FIELD:
            _record_or_merge(
                candidates_for_record=[cand],
                doc_type=DOC_TYPE_D_LIMITATION,
                doc_label=raw,
                usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                doc_type_confidence="confirmed",
                doc_type_notes=(
                    "D limitation from ZIMAS Site.d_limitations field. "
                    "Reduces density/FAR below base zone; amount must be confirmed from source."
                ),
                source_ordinance_number=cand.source_ordinance_number,
            )

        # — Structured field: overlay name —
        elif pattern in (PATTERN_OVERLAY_NAME_FIELD, PATTERN_RAW_LAYER_ATTR):
            if _is_cpio(raw):
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_OVERLAY_CPIO,
                    doc_label=raw,
                    usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
                    doc_type_confidence="confirmed",
                    doc_type_notes=(
                        "CPIO detected by name. Frequently overrides base zone FAR, "
                        "density, parking, and setbacks. Subarea placement is manual."
                    ),
                )
            elif _is_supplemental(raw):
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_OVERLAY_SUPPLEMENTAL,
                    doc_label=raw,
                    usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
                    doc_type_confidence="confirmed",
                    doc_type_notes="Supplemental use district detected by name.",
                )
            elif "SPECIFIC PLAN" in raw.upper():
                # Specific plans sometimes appear in overlay_zones in ZIMAS
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_SPECIFIC_PLAN,
                    doc_label=raw,
                    usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                    doc_type_confidence="provisional",
                    doc_type_notes=(
                        "Specific plan name detected in overlay_zones field. "
                        "Classified as specific_plan; confirm whether this is a "
                        "standalone plan or a CPIO-linked plan."
                    ),
                )
            else:
                # Unknown overlay — err conservative
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_UNKNOWN_ARTIFACT,
                    doc_label=raw,
                    usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                    doc_type_confidence="ambiguous",
                    doc_type_notes=(
                        "Overlay-like name detected but type could not be confirmed. "
                        "Could be CPIO, SUD, or specific plan overlay. Treat as interrupter."
                    ),
                )
                issues.append(
                    ZimasDocIssue(
                        step="doc_classifier",
                        field=cand.source_field,
                        severity="warning",
                        message=f"Overlay name '{raw}' could not be classified as CPIO, SUD, or specific plan.",
                        action_required="Confirm overlay type manually and reclassify.",
                        confidence_impact="degrades_to_unresolved",
                    )
                )

        # — ZI number —
        elif pattern == PATTERN_ZI_NUMBER:
            zi_match = _RE_ZI_EXTRACT.search(raw)
            label = zi_match.group(0).upper() if zi_match else raw
            _record_or_merge(
                candidates_for_record=[cand],
                doc_type=DOC_TYPE_ZI_DOCUMENT,
                doc_label=label,
                usability_posture=POSTURE_MACHINE_USABLE,
                doc_type_confidence="confirmed",
                doc_type_notes=(
                    "ZI document number confirmed. LADBS ZI lookup provides "
                    "structured title and subject. Surface-level use only."
                ),
            )

        # — Ordinance number —
        elif pattern == PATTERN_ORDINANCE_NUMBER:
            ord_match = _RE_ORD_EXTRACT.search(raw)
            label = ord_match.group(0).upper() if ord_match else raw
            _record_or_merge(
                candidates_for_record=[cand],
                doc_type=DOC_TYPE_ORDINANCE,
                doc_label=label,
                usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
                doc_type_confidence="provisional",
                doc_type_notes=(
                    "Ordinance number detected. May be a CPIO ordinance, overlay "
                    "ordinance, or general zoning amendment. Type must be confirmed."
                ),
            )

        # — Case number —
        elif pattern == PATTERN_CASE_NUMBER:
            _record_or_merge(
                candidates_for_record=[cand],
                doc_type=DOC_TYPE_CASE_DOCUMENT,
                doc_label=raw.upper(),
                usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                doc_type_confidence="confirmed",
                doc_type_notes=(
                    "Planning case number detected. Case conditions may restrict "
                    "or modify base zone entitlements. Treat as interrupter."
                ),
            )

        # — URL in text —
        elif pattern == PATTERN_URL_IN_TEXT:
            url = cand.url or raw
            url_conf = cand.url_confidence

            if _is_planning_page(url):
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_PLANNING_PAGE,
                    doc_label=url,
                    usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
                    doc_type_confidence="provisional",
                    doc_type_notes="DCP planning page link — reference only.",
                    url=url,
                    url_confidence=url_conf,
                )
            elif _is_map_figure(raw, url):
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_MAP_FIGURE_PACKET,
                    doc_label=url,
                    usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
                    doc_type_confidence="provisional",
                    doc_type_notes="Map/figure packet link detected.",
                    url=url,
                    url_confidence=url_conf,
                )
            elif url.lower().endswith(".pdf") or url_conf == URL_CONF_DIRECT_LINK:
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_PDF_ARTIFACT,
                    doc_label=url,
                    usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
                    doc_type_confidence="ambiguous",
                    doc_type_notes=(
                        "Direct PDF link detected; document type not confirmed. "
                        "Classify after fetching."
                    ),
                    url=url,
                    url_confidence=url_conf,
                )
            else:
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_UNKNOWN_ARTIFACT,
                    doc_label=url,
                    usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                    doc_type_confidence="ambiguous",
                    doc_type_notes="URL detected but type cannot be confirmed without fetching.",
                    url=url,
                    url_confidence=url_conf,
                )

        # — Zone string parse: gap-fill D/Q and supplemental districts —
        elif pattern == PATTERN_ZONE_STRING_PARSE:
            sf = cand.source_field
            if sf == "zone_string_parse:d_limitation":
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_D_LIMITATION,
                    doc_label=raw,
                    usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                    doc_type_confidence="confirmed",
                    doc_type_notes=(
                        "D limitation detected by zone string parser (inline-D suffix). "
                        "Normalizer bracket-scan missed it. Amount must be confirmed from source."
                    ),
                    source_ordinance_number=cand.source_ordinance_number,
                )
            elif sf == "zone_string_parse:q_condition":
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_Q_CONDITION,
                    doc_label=raw,
                    usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                    doc_type_confidence="confirmed",
                    doc_type_notes=(
                        "Q condition detected by zone string parser. "
                        "Normalizer bracket-scan missed it. Restrictions must be read from source."
                    ),
                    source_ordinance_number=cand.source_ordinance_number,
                )
            elif sf == "zone_string_parse:supplemental_district":
                # Same dispatch logic as PATTERN_OVERLAY_NAME_FIELD
                if _is_cpio(raw):
                    _record_or_merge(
                        candidates_for_record=[cand],
                        doc_type=DOC_TYPE_OVERLAY_CPIO,
                        doc_label=raw,
                        usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
                        doc_type_confidence="confirmed",
                        doc_type_notes=(
                            "CPIO detected via zone string parser (supplemental district). "
                            "Subarea placement is manual."
                        ),
                    )
                elif _is_supplemental(raw):
                    _record_or_merge(
                        candidates_for_record=[cand],
                        doc_type=DOC_TYPE_OVERLAY_SUPPLEMENTAL,
                        doc_label=raw,
                        usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
                        doc_type_confidence="confirmed",
                        doc_type_notes="Supplemental use district detected via zone string parser.",
                    )
                elif "SPECIFIC PLAN" in raw.upper():
                    _record_or_merge(
                        candidates_for_record=[cand],
                        doc_type=DOC_TYPE_SPECIFIC_PLAN,
                        doc_label=raw,
                        usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                        doc_type_confidence="provisional",
                        doc_type_notes=(
                            "Specific plan name detected via zone string parser. "
                            "Confirm whether standalone plan or CPIO-linked plan."
                        ),
                    )
                else:
                    _record_or_merge(
                        candidates_for_record=[cand],
                        doc_type=DOC_TYPE_UNKNOWN_ARTIFACT,
                        doc_label=raw,
                        usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                        doc_type_confidence="ambiguous",
                        doc_type_notes=(
                            "Supplemental district from zone string parse; type could not "
                            "be confirmed. Treat as interrupter."
                        ),
                    )
            else:
                # Unrecognised zone_string_parse source_field sub-type
                _record_or_merge(
                    candidates_for_record=[cand],
                    doc_type=DOC_TYPE_UNKNOWN_ARTIFACT,
                    doc_label=raw,
                    usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                    doc_type_confidence="ambiguous",
                    doc_type_notes=f"zone_string_parse candidate with unrecognised source_field '{sf}'.",
                )

        # — Unclassifiable —
        else:
            _record_or_merge(
                candidates_for_record=[cand],
                doc_type=DOC_TYPE_UNKNOWN_ARTIFACT,
                doc_label=raw,
                usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
                doc_type_confidence="ambiguous",
                doc_type_notes=f"Detection pattern '{pattern}' produced no classification.",
            )
            issues.append(
                ZimasDocIssue(
                    step="doc_classifier",
                    field=cand.source_field,
                    severity="warning",
                    message=f"Could not classify candidate '{raw}' (pattern: {pattern}).",
                    action_required="Review raw ZIMAS data to identify document type.",
                    confidence_impact="degrades_to_provisional",
                )
            )

    return records, issues
