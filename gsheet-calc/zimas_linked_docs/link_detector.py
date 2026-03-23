"""ZIMAS linked-document detector.

Scans ZimasLinkedDocInput fields and raw ZIMAS identify response for signals
that linked authority materials exist. Produces LinkedDocCandidate list.

Detection sources:
    1. Structured Site fields (specific_plan, overlay_zones, q_conditions, d_limitations)
    2. Raw ZIMAS identify response — layers beyond parcel layer 105
    3. Raw text fragments from portal pages or notes fields

Candidates are raw signals — they carry no classification. The doc_classifier
step assigns doc_type and usability_posture. This step only asks: "is there
something here that might be a linked authority item?"

Posture: over-detect rather than miss. False positives are recoverable.
False negatives (missed Q condition, missed specific plan) are not.
"""

from __future__ import annotations

import re

from zimas_linked_docs.models import (
    LinkedDocCandidate,
    ZimasLinkedDocInput,
    ZimasDocIssue,
    PATTERN_SPECIFIC_PLAN_FIELD,
    PATTERN_OVERLAY_NAME_FIELD,
    PATTERN_Q_CONDITION_FIELD,
    PATTERN_D_LIMITATION_FIELD,
    PATTERN_ZI_NUMBER,
    PATTERN_ORDINANCE_NUMBER,
    PATTERN_CASE_NUMBER,
    PATTERN_URL_IN_TEXT,
    PATTERN_RAW_LAYER_ATTR,
    PATTERN_UNKNOWN,
    PATTERN_ZONE_STRING_PARSE,
    URL_CONF_DIRECT_LINK,
    URL_CONF_PORTAL_REDIRECT,
    URL_CONF_NONE,
)


# ── Compiled regex patterns ───────────────────────────────────────────────────

_RE_ZI = re.compile(r"\bZI-\d{3,5}\b", re.IGNORECASE)
# LA City ordinance numbers: O-186481 or Ord. No. 186481 or Ordinance No 186481
_RE_ORD = re.compile(r"\bO-(\d{5,6})\b|\bOrd(?:inance)?\.?\s*No\.?\s*(\d{5,6})\b", re.IGNORECASE)
# Case numbers: CPC-2006-5568, ZA-2014-123, DIR-2019-456, AA-2020-789, CF-2021-012, ENV-2018-345
_RE_CASE = re.compile(r"\b(CPC|ZA|DIR|AA|CF|ENV)-\d{4}-\d+\b", re.IGNORECASE)
# URLs
_RE_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# ZIMAS identify layer IDs to scan for linked authority signals.
# Layer 105 is parcel (handled by ingest/parcel.py) — excluded here.
# These are approximate — actual layer IDs vary by ZIMAS instance.
_SKIP_LAYER_IDS = {105}

# Known portal URL patterns (non-document redirects)
_PORTAL_URL_PATTERNS = (
    "arcgis.com",
    "gis.lacity.org",
    "zimas.lacity.org",
    "maps.lacity.org",
    "planning.lacity.org/zimas",
)


def _url_confidence(url: str) -> str:
    if url.lower().endswith(".pdf"):
        return URL_CONF_DIRECT_LINK
    for pattern in _PORTAL_URL_PATTERNS:
        if pattern in url.lower():
            return URL_CONF_PORTAL_REDIRECT
    return URL_CONF_NONE


def _make_id(apn: str | None, seq: int) -> str:
    apn_part = apn.replace("-", "").replace(" ", "") if apn else "NOAPN"
    return f"ZCAND-{apn_part}-{seq:03d}"


def detect_linked_docs(
    inp: ZimasLinkedDocInput,
) -> tuple[list[LinkedDocCandidate], list[ZimasDocIssue]]:
    """Scan all detection sources and return raw candidates + any issues.

    Candidates have no doc_type — classification is the next step.
    Over-detection is acceptable; under-detection is not.
    """
    candidates: list[LinkedDocCandidate] = []
    issues: list[ZimasDocIssue] = []
    seq = 0

    def _add(
        source_field: str,
        raw_value: str,
        detected_pattern: str,
        url: str | None = None,
        url_confidence: str = URL_CONF_NONE,
        notes: str = "",
        source_ordinance_number: str | None = None,
    ) -> None:
        nonlocal seq
        seq += 1
        candidates.append(
            LinkedDocCandidate(
                candidate_id=_make_id(inp.apn, seq),
                source_field=source_field,
                raw_value=raw_value.strip(),
                detected_pattern=detected_pattern,
                url=url,
                url_confidence=url_confidence,
                notes=notes,
                source_ordinance_number=source_ordinance_number,
            )
        )

    # ── 1. Structured Site fields ─────────────────────────────────────────────

    if inp.specific_plan:
        _add(
            source_field="specific_plan",
            raw_value=inp.specific_plan,
            detected_pattern=PATTERN_SPECIFIC_PLAN_FIELD,
            notes="Specific plan name from Site.specific_plan field.",
        )
        if inp.specific_plan_subarea:
            _add(
                source_field="specific_plan_subarea",
                raw_value=f"{inp.specific_plan} / {inp.specific_plan_subarea}",
                detected_pattern=PATTERN_SPECIFIC_PLAN_FIELD,
                notes="Subarea from Site.specific_plan_subarea field.",
            )

    for oz in inp.overlay_zones:
        if not oz.strip():
            continue
        _add(
            source_field="overlay_zones",
            raw_value=oz,
            detected_pattern=PATTERN_OVERLAY_NAME_FIELD,
            notes="Overlay zone name from Site.overlay_zones list.",
        )

    for qc in inp.q_conditions:
        if not qc.strip():
            continue
        _add(
            source_field="q_conditions",
            raw_value=qc,
            detected_pattern=PATTERN_Q_CONDITION_FIELD,
            notes="Q condition from Site.q_conditions list.",
            source_ordinance_number=inp.q_ordinance_number,
        )

    for dl in inp.d_limitations:
        if not dl.strip():
            continue
        _add(
            source_field="d_limitations",
            raw_value=dl,
            detected_pattern=PATTERN_D_LIMITATION_FIELD,
            notes="D limitation from Site.d_limitations list.",
            source_ordinance_number=inp.d_ordinance_number,
        )

    # ── 2. Raw ZIMAS identify response — scan non-parcel layers ──────────────

    raw_results = inp.raw_zimas_identify.get("results", [])
    if not isinstance(raw_results, list):
        issues.append(
            ZimasDocIssue(
                step="link_detector",
                field="raw_zimas_identify",
                severity="warning",
                message="raw_zimas_identify.results is not a list — skipping layer scan.",
                confidence_impact="none",
            )
        )
        raw_results = []

    for layer_result in raw_results:
        layer_id = layer_result.get("layerId")
        if layer_id in _SKIP_LAYER_IDS:
            continue

        layer_name = layer_result.get("layerName", f"layer_{layer_id}")
        attrs = layer_result.get("attributes", {})
        if not isinstance(attrs, dict):
            continue

        for attr_key, attr_val in attrs.items():
            if not attr_val or not isinstance(attr_val, str):
                continue
            raw = attr_val.strip()
            if not raw:
                continue

            source = f"zimas_layer_{layer_id}:{attr_key}"

            # Check for ZI numbers
            for match in _RE_ZI.finditer(raw):
                _add(
                    source_field=source,
                    raw_value=match.group(0),
                    detected_pattern=PATTERN_ZI_NUMBER,
                    notes=f"ZI number extracted from {layer_name}.{attr_key}",
                )

            # Check for ordinance numbers
            for match in _RE_ORD.finditer(raw):
                ord_num = match.group(1) or match.group(2)
                _add(
                    source_field=source,
                    raw_value=f"O-{ord_num}",
                    detected_pattern=PATTERN_ORDINANCE_NUMBER,
                    notes=f"Ordinance number extracted from {layer_name}.{attr_key}",
                )

            # Check for case numbers
            for match in _RE_CASE.finditer(raw):
                _add(
                    source_field=source,
                    raw_value=match.group(0).upper(),
                    detected_pattern=PATTERN_CASE_NUMBER,
                    notes=f"Case number extracted from {layer_name}.{attr_key}",
                )

            # Check for URLs
            for match in _RE_URL.finditer(raw):
                url = match.group(0)
                conf = _url_confidence(url)
                _add(
                    source_field=source,
                    raw_value=url,
                    detected_pattern=PATTERN_URL_IN_TEXT,
                    url=url,
                    url_confidence=conf,
                    notes=f"URL found in {layer_name}.{attr_key}",
                )

            # Overlay / specific plan name signal in layer attributes
            # Heuristic: field key contains "PLAN", "OVERLAY", "SPECIFIC", "CPIO"
            key_upper = attr_key.upper()
            if any(kw in key_upper for kw in ("SPECIFIC_PLAN", "OVERLAY", "CPIO", "SP_NAME")):
                _add(
                    source_field=source,
                    raw_value=raw,
                    detected_pattern=PATTERN_RAW_LAYER_ATTR,
                    notes=f"Overlay/plan name from {layer_name}.{attr_key}",
                )

    # ── 3. Raw text fragments ─────────────────────────────────────────────────

    for frag_idx, fragment in enumerate(inp.raw_text_fragments):
        if not fragment or not fragment.strip():
            continue
        source = f"raw_text_fragment_{frag_idx}"

        for match in _RE_ZI.finditer(fragment):
            _add(source, match.group(0), PATTERN_ZI_NUMBER,
                 notes="ZI number from raw text fragment.")

        for match in _RE_ORD.finditer(fragment):
            ord_num = match.group(1) or match.group(2)
            _add(source, f"O-{ord_num}", PATTERN_ORDINANCE_NUMBER,
                 notes="Ordinance number from raw text fragment.")

        for match in _RE_CASE.finditer(fragment):
            _add(source, match.group(0).upper(), PATTERN_CASE_NUMBER,
                 notes="Case number from raw text fragment.")

        for match in _RE_URL.finditer(fragment):
            url = match.group(0)
            conf = _url_confidence(url)
            _add(source, url, PATTERN_URL_IN_TEXT,
                 url=url, url_confidence=conf,
                 notes="URL from raw text fragment.")

    # ── 4. Zone string parse results ─────────────────────────────────────────
    # Consumes ZoningParseResult fields forwarded through ZimasLinkedDocInput.
    # Three sub-tasks:
    #   a) Surface parse failure as an issue (unresolved confidence = ambiguous zone)
    #   b) Gap-fill D/Q candidates missed by normalizer (e.g. inline-D suffix C2-2D)
    #   c) Supplemental districts not already in overlay_zones

    if inp.zoning_parse_confidence == "unresolved":
        issues.append(
            ZimasDocIssue(
                step="link_detector",
                field="zoning_parse_confidence",
                severity="error",
                message=(
                    "Zone string parser returned 'unresolved' confidence. "
                    "The zone string could not be matched to a known base zone. "
                    "Linked document detection for this parcel is incomplete."
                ),
                action_required=(
                    "Manually inspect the zone string and confirm applicable "
                    "chapter, overlays, and any D/Q conditions."
                ),
                confidence_impact="degrades_to_unresolved",
            )
        )
        if inp.zoning_parse_issues:
            for pi in inp.zoning_parse_issues:
                issues.append(
                    ZimasDocIssue(
                        step="link_detector",
                        field="zoning_parse_issues",
                        severity="warning",
                        message=f"Zone string parse issue: {pi}",
                        confidence_impact="none",
                    )
                )

    # Gap-fill D limitation: parser detected it but normalizer bracket-scan missed it
    # (covers inline-D suffix like C2-2D where [D] bracket is absent)
    if inp.has_d_from_zone_string and not inp.d_limitations:
        _add(
            source_field="zone_string_parse:d_limitation",
            raw_value="D",
            detected_pattern=PATTERN_ZONE_STRING_PARSE,
            notes=(
                "D limitation detected by zone string parser (inline-D suffix). "
                "Not present in Site.d_limitations — normalizer bracket-scan missed it."
            ),
            source_ordinance_number=inp.d_ordinance_number,
        )

    # Gap-fill Q condition: parser detected it but normalizer bracket-scan missed it
    if inp.has_q_from_zone_string and not inp.q_conditions:
        _add(
            source_field="zone_string_parse:q_condition",
            raw_value="Q",
            detected_pattern=PATTERN_ZONE_STRING_PARSE,
            notes=(
                "Q condition detected by zone string parser. "
                "Not present in Site.q_conditions — normalizer bracket-scan missed it."
            ),
            source_ordinance_number=inp.q_ordinance_number,
        )

    # Supplemental districts from parse not already covered by overlay_zones
    existing_overlays_upper = {oz.upper() for oz in inp.overlay_zones}
    for sd in inp.supplemental_districts_from_parse:
        if not sd.strip():
            continue
        if sd.upper() not in existing_overlays_upper:
            _add(
                source_field="zone_string_parse:supplemental_district",
                raw_value=sd,
                detected_pattern=PATTERN_ZONE_STRING_PARSE,
                notes=(
                    f"Supplemental district '{sd}' found by zone string parser "
                    "but absent from Site.overlay_zones."
                ),
            )

    # ── 5. Warn if nothing was detected at all ────────────────────────────────

    if not candidates:
        issues.append(
            ZimasDocIssue(
                step="link_detector",
                field="all_sources",
                severity="info",
                message=(
                    "No linked authority items detected. "
                    "Verify that specific_plan, overlay_zones, q_conditions, "
                    "and d_limitations fields were populated before ingestion."
                ),
                confidence_impact="none",
            )
        )

    return candidates, issues
