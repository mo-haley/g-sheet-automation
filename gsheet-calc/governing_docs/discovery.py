"""Phase 1 discovery: find site controls from existing parsed/cached data.

Three discovery sources, all local / no network:
  1. Site model (already-parsed fields)
  2. ZoningParseResult (structured zoning parse)
  3. Raw ZIMAS identify JSON (cached responses)

Each function returns a list of SiteControl observations.
Over-discovery is preferred — the registry handles dedup.
"""

from __future__ import annotations

import re

from governing_docs.models import (
    ControlType,
    DiscoverySourceType,
    SiteControl,
)


# --- Known supplemental district patterns ---

_CPIO_PATTERN = re.compile(r"CPIO", re.IGNORECASE)
_HPOZ_PATTERN = re.compile(r"HPOZ", re.IGNORECASE)
_SNAP_PATTERN = re.compile(r"SNAP", re.IGNORECASE)

# Known ZIMAS zoning layer IDs (from settings, but hardcoded here to avoid
# importing settings — keeps this module dependency-light)
_ZONING_LAYER_IDS = {1102, 1101}
_CH1A_LAYER_ID = 1101


def _classify_supplemental(raw: str) -> ControlType:
    """Classify a supplemental district string into a ControlType."""
    upper = raw.upper().strip()
    if _CPIO_PATTERN.search(upper):
        return ControlType.CPIO
    if _HPOZ_PATTERN.search(upper):
        return ControlType.HPOZ
    if _SNAP_PATTERN.search(upper):
        return ControlType.SNAP
    return ControlType.UNKNOWN_OVERLAY


def discover_from_site_model(site) -> list[SiteControl]:
    """Discover controls from an already-populated Site model instance.

    Reads: site.d_limitations, site.q_conditions, site.overlay_zones,
           site.specific_plan, site.specific_plan_subarea
    """
    controls: list[SiteControl] = []
    parcel_id = site.apn or (
        f"{site.coordinates[0]:.6f}_{site.coordinates[1]:.6f}"
        if site.coordinates else None
    )

    # D limitations
    for i, d_val in enumerate(getattr(site, "d_limitations", []) or []):
        sc = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value=d_val,
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail=f"Site.d_limitations[{i}]",
            parcel_id=parcel_id,
            ordinance_number=d_val if d_val and d_val.lower() != "d" else None,
            document_resolution_likely_required=True,
            resolution_notes="D limitation ordinance must be reviewed for density/FAR/height impact.",
        )
        if d_val and d_val.lower().startswith("ord-xxxxx"):
            sc.warnings.append("Placeholder ordinance number — real ordinance not yet identified.")
        controls.append(sc)

    # Q conditions
    for i, q_val in enumerate(getattr(site, "q_conditions", []) or []):
        controls.append(SiteControl(
            control_type=ControlType.Q_CONDITION,
            raw_value=q_val,
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail=f"Site.q_conditions[{i}]",
            parcel_id=parcel_id,
            ordinance_number=q_val if q_val and q_val.lower() != "q" else None,
            document_resolution_likely_required=True,
            resolution_notes="Q condition ordinance must be reviewed for use/density/parking restrictions.",
        ))

    # Overlay zones
    for i, overlay in enumerate(getattr(site, "overlay_zones", []) or []):
        ct = _classify_supplemental(overlay)
        controls.append(SiteControl(
            control_type=ct,
            raw_value=overlay,
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail=f"Site.overlay_zones[{i}]",
            parcel_id=parcel_id,
            normalized_name=overlay,
            document_resolution_likely_required=(ct != ControlType.UNKNOWN_OVERLAY),
        ))

    # Specific plan
    sp = getattr(site, "specific_plan", None)
    if sp:
        controls.append(SiteControl(
            control_type=ControlType.SPECIFIC_PLAN,
            raw_value=sp,
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail="Site.specific_plan",
            parcel_id=parcel_id,
            normalized_name=sp,
            subarea=getattr(site, "specific_plan_subarea", None),
            document_resolution_likely_required=True,
            resolution_notes="Specific plan document must be reviewed for all development standards.",
        ))

    return controls


def discover_from_zoning_parse(parse_result, parcel_id: str | None = None) -> list[SiteControl]:
    """Discover controls from a ZoningParseResult.

    Reads: has_D_limitation, D_ordinance_number, has_Q_condition,
           Q_ordinance_number, has_T_classification, supplemental_districts
    """
    controls: list[SiteControl] = []

    if getattr(parse_result, "has_D_limitation", False):
        ord_num = getattr(parse_result, "D_ordinance_number", None)
        controls.append(SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value=f"D (from zoning string: {parse_result.raw_string})",
            source_type=DiscoverySourceType.ZONING_PARSE_RESULT,
            source_detail=f"ZoningParseResult.has_D_limitation (raw: {parse_result.raw_string})",
            parcel_id=parcel_id,
            ordinance_number=ord_num,
            document_resolution_likely_required=True,
        ))

    if getattr(parse_result, "has_Q_condition", False):
        ord_num = getattr(parse_result, "Q_ordinance_number", None)
        controls.append(SiteControl(
            control_type=ControlType.Q_CONDITION,
            raw_value=f"Q (from zoning string: {parse_result.raw_string})",
            source_type=DiscoverySourceType.ZONING_PARSE_RESULT,
            source_detail=f"ZoningParseResult.has_Q_condition (raw: {parse_result.raw_string})",
            parcel_id=parcel_id,
            ordinance_number=ord_num,
            document_resolution_likely_required=True,
        ))

    if getattr(parse_result, "has_T_classification", False):
        controls.append(SiteControl(
            control_type=ControlType.T_CLASSIFICATION,
            raw_value=f"T (from zoning string: {parse_result.raw_string})",
            source_type=DiscoverySourceType.ZONING_PARSE_RESULT,
            source_detail=f"ZoningParseResult.has_T_classification (raw: {parse_result.raw_string})",
            parcel_id=parcel_id,
            document_resolution_likely_required=True,
            resolution_notes="T classification indicates tentative tract map conditions.",
        ))

    for i, supp in enumerate(getattr(parse_result, "supplemental_districts", []) or []):
        ct = _classify_supplemental(supp)
        controls.append(SiteControl(
            control_type=ct,
            raw_value=supp,
            source_type=DiscoverySourceType.ZONING_PARSE_RESULT,
            source_detail=f"ZoningParseResult.supplemental_districts[{i}] (raw: {parse_result.raw_string})",
            parcel_id=parcel_id,
            normalized_name=supp,
            document_resolution_likely_required=(ct != ControlType.UNKNOWN_OVERLAY),
        ))

    return controls


def discover_from_raw_zimas(identify_data: dict, parcel_id: str | None = None) -> list[SiteControl]:
    """Discover controls directly from a raw ZIMAS identify response dict.

    This is the most authoritative source — reads ZONE_CMPLT directly.
    Parses the zoning string for D suffix, Q/T prefixes, and supplemental districts.
    """
    controls: list[SiteControl] = []

    for result in identify_data.get("results", []):
        layer_id = result.get("layerId")
        if layer_id not in _ZONING_LAYER_IDS:
            continue

        layer_name = result.get("layerName", "unknown")
        attrs = result.get("attributes", {})
        zone_cmplt = attrs.get("ZONE_CMPLT")

        if not zone_cmplt or str(zone_cmplt).strip().lower() == "null":
            continue

        raw = str(zone_cmplt).strip()
        pre_count = len(controls)
        _discover_controls_from_zone_string(
            raw, layer_id, layer_name, parcel_id, controls
        )

        # Tag controls discovered from Chapter 1A layer with format warning
        if layer_id == _CH1A_LAYER_ID:
            for ctrl in controls[pre_count:]:
                if not any("Chapter 1A" in w for w in ctrl.warnings):
                    ctrl.warnings.append(
                        f"Discovered from Chapter 1A zoning layer ({layer_id}). "
                        f"Bracket-delimited format may not be fully parseable — "
                        f"verify against ZIMAS parcel profile."
                    )

    return controls


def _discover_controls_from_zone_string(
    raw: str,
    layer_id: int,
    layer_name: str,
    parcel_id: str | None,
    controls: list[SiteControl],
) -> None:
    """Parse a ZONE_CMPLT string for control indicators.

    Intentionally over-discovers. Does not try to fully parse the zone —
    that's the zoning parser's job. We just look for control indicators.
    """
    source_detail = f"ZONE_CMPLT='{raw}' from layer {layer_id} ({layer_name})"

    # Check for D suffix: look for a segment ending in D after height district
    # Pattern: "2D" in "C2-2D-CPIO" or standalone "D" segment
    parts = raw.split("-")
    for part in parts:
        cleaned = part.strip().upper()
        # Remove bracket prefixes for this check
        cleaned_no_brackets = re.sub(r"[\[\]\(\)]", "", cleaned)
        if re.match(r"^\d+[A-Z]*D$", cleaned_no_brackets) and cleaned_no_brackets != "D":
            # Looks like "2D", "1VLD" — height district with D suffix
            controls.append(SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value=cleaned_no_brackets,
                source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                source_detail=source_detail,
                parcel_id=parcel_id,
                zimas_layer_id=layer_id,
                zimas_layer_name=layer_name,
                raw_field_name="ZONE_CMPLT",
                document_resolution_likely_required=True,
                resolution_notes="D limitation detected in zoning string. Ordinance number not available from ZONE_CMPLT alone.",
            ))
            break  # One D per zoning string
        if cleaned_no_brackets == "D":
            controls.append(SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value="D",
                source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                source_detail=source_detail,
                parcel_id=parcel_id,
                zimas_layer_id=layer_id,
                zimas_layer_name=layer_name,
                raw_field_name="ZONE_CMPLT",
                document_resolution_likely_required=True,
            ))
            break

    # Check for Q prefix: (Q) or [Q]
    if re.search(r"[\[\(]Q[\]\)]", raw, re.IGNORECASE):
        controls.append(SiteControl(
            control_type=ControlType.Q_CONDITION,
            raw_value="Q",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail=source_detail,
            parcel_id=parcel_id,
            zimas_layer_id=layer_id,
            zimas_layer_name=layer_name,
            raw_field_name="ZONE_CMPLT",
            document_resolution_likely_required=True,
            resolution_notes="Q condition detected in zoning string. Ordinance number not available from ZONE_CMPLT alone.",
        ))

    # Check for T prefix: (T) or [T]
    if re.search(r"[\[\(]T[\]\)]", raw, re.IGNORECASE):
        controls.append(SiteControl(
            control_type=ControlType.T_CLASSIFICATION,
            raw_value="T",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail=source_detail,
            parcel_id=parcel_id,
            zimas_layer_id=layer_id,
            zimas_layer_name=layer_name,
            raw_field_name="ZONE_CMPLT",
            document_resolution_likely_required=True,
        ))

    # Supplemental districts: typically the last segment(s) after HD
    # Also handle bracket-delimited Ch1A format: [LF1-WH1-5][P2-FA][CPIO]
    _discover_supplementals_from_zone_string(
        raw, layer_id, layer_name, parcel_id, controls
    )


def _discover_supplementals_from_zone_string(
    raw: str,
    layer_id: int,
    layer_name: str,
    parcel_id: str | None,
    controls: list[SiteControl],
) -> None:
    """Extract supplemental districts (CPIO, HPOZ, SNAP, etc.) from a zone string."""
    source_detail = f"ZONE_CMPLT='{raw}' from layer {layer_id} ({layer_name})"

    # Strategy 1: Check for bracket-delimited segments (Chapter 1A format)
    # e.g. "[LF1-WH1-5][P2-FA][CPIO]"
    bracket_segments = re.findall(r"\[([^\]]+)\]", raw)
    if bracket_segments:
        for seg in bracket_segments:
            seg_upper = seg.strip().upper()
            if _CPIO_PATTERN.search(seg_upper):
                controls.append(SiteControl(
                    control_type=ControlType.CPIO,
                    raw_value=seg,
                    source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                    source_detail=source_detail,
                    parcel_id=parcel_id,
                    normalized_name=seg,
                    zimas_layer_id=layer_id,
                    zimas_layer_name=layer_name,
                    raw_field_name="ZONE_CMPLT",
                    document_resolution_likely_required=True,
                    warnings=["CPIO found in bracket-delimited (Chapter 1A?) zoning string."],
                ))
            elif _HPOZ_PATTERN.search(seg_upper):
                controls.append(SiteControl(
                    control_type=ControlType.HPOZ,
                    raw_value=seg,
                    source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                    source_detail=source_detail,
                    parcel_id=parcel_id,
                    normalized_name=seg,
                    zimas_layer_id=layer_id,
                    zimas_layer_name=layer_name,
                    raw_field_name="ZONE_CMPLT",
                    document_resolution_likely_required=True,
                ))
            elif _SNAP_PATTERN.search(seg_upper):
                controls.append(SiteControl(
                    control_type=ControlType.SNAP,
                    raw_value=seg,
                    source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                    source_detail=source_detail,
                    parcel_id=parcel_id,
                    normalized_name=seg,
                    zimas_layer_id=layer_id,
                    zimas_layer_name=layer_name,
                    raw_field_name="ZONE_CMPLT",
                    document_resolution_likely_required=True,
                ))
        return  # Bracket format handled — don't also do hyphen splitting

    # Strategy 2: Hyphen-delimited format (Chapter 1 style)
    # e.g. "C2-2D-CPIO" — supplementals are after the height district segment
    parts = raw.split("-")
    if len(parts) <= 2:
        return  # No supplemental segment

    # Skip first part (base zone) and second part (height district, possibly with D)
    for part in parts[2:]:
        cleaned = part.strip()
        if not cleaned:
            continue
        # Skip if this looks like a numeric/HD segment
        if re.match(r"^\d+[A-Z]*$", cleaned.upper()):
            continue

        ct = _classify_supplemental(cleaned)
        controls.append(SiteControl(
            control_type=ct,
            raw_value=cleaned,
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail=source_detail,
            parcel_id=parcel_id,
            normalized_name=cleaned,
            zimas_layer_id=layer_id,
            zimas_layer_name=layer_name,
            raw_field_name="ZONE_CMPLT",
            document_resolution_likely_required=(ct != ControlType.UNKNOWN_OVERLAY),
        ))
