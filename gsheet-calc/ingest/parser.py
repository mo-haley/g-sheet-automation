from __future__ import annotations

"""Parse and merge ZIMAS API responses into a Site model."""

import logging
from collections import Counter

from config.settings import ZIMAS_LAYERS
from ingest.normalizer import (
    extract_d_limitations,
    extract_height_district,
    extract_overlays,
    extract_q_conditions,
    infer_chapter,
    normalize_zone,
)
from ingest.parcel import extract_parcel_data
from ingest.zoning_parser import ZoningParseResult, parse_zoning_string
from models.issue import ReviewIssue
from models.site import DataSource, Site

logger = logging.getLogger(__name__)


def _find_layer_results(identify_data: dict, layer_id: int) -> list[dict]:
    """Find all identify results for a given layer ID."""
    return [
        r for r in identify_data.get("results", [])
        if r.get("layerId") == layer_id
    ]


def _get_attr(results: list[dict], *keys: str) -> str | None:
    """Extract the first non-empty attribute from identify results."""
    for r in results:
        attrs = r.get("attributes", {})
        for key in keys:
            val = attrs.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    return None


def _get_all_attr_values(results: list[dict], *keys: str) -> list[str]:
    """Extract all distinct non-empty attribute values from identify results."""
    values: list[str] = []
    seen: set[str] = set()
    for r in results:
        attrs = r.get("attributes", {})
        for key in keys:
            val = attrs.get(key)
            if val is not None and str(val).strip():
                v = str(val).strip()
                if v not in seen:
                    values.append(v)
                    seen.add(v)
    return values


def parse_zimas_response(
    address: str,
    identify_data: dict,
    coordinates: tuple[float, float] | None = None,
    pull_timestamp: str | None = None,
) -> tuple[Site, ZoningParseResult | None, list[ReviewIssue]]:
    """Parse a ZIMAS identify response into a Site model.

    Returns:
        Tuple of (Site, ZoningParseResult | None, list of ReviewIssues).
        ZoningParseResult is None only when no zoning string was returned by ZIMAS.
        Callers should pass ZoningParseResult to ZimasLinkedDocInput for richer
        linked-document detection (D/Q ordinance numbers, supplemental districts,
        parse confidence).
    """
    issues: list[ReviewIssue] = []
    sources: list[DataSource] = []
    raw_files: list[str] = []

    # --- Zoning ---
    zoning_results = _find_layer_results(identify_data, ZIMAS_LAYERS["zoning"])
    zoning_ch1a_results = _find_layer_results(identify_data, ZIMAS_LAYERS["zoning_ch1a"])

    # With a generous identify tolerance we may get multiple zone polygons.
    # Deduplicate on ZONE_CMPLT and pick the most frequent (= most likely to
    # be the actual parcel zone rather than an adjacent one caught by tolerance).
    all_zone_strings = _get_all_attr_values(zoning_results, "ZONE_CMPLT")
    zoning_ambiguous = False
    if not all_zone_strings:
        all_zone_strings = _get_all_attr_values(zoning_ch1a_results, "ZONE_CMPLT")

    if len(all_zone_strings) > 1:
        # Multiple distinct zone strings — pick the most frequent raw result
        raw_values = [
            str(r.get("attributes", {}).get("ZONE_CMPLT", "")).strip()
            for r in zoning_results
            if r.get("attributes", {}).get("ZONE_CMPLT")
        ]
        freq = Counter(raw_values)
        zoning_raw = freq.most_common(1)[0][0] if freq else all_zone_strings[0]
        zoning_ambiguous = True
        logger.info(
            "Multiple zone strings at query point: %s — selected '%s' (most frequent)",
            all_zone_strings, zoning_raw,
        )
    elif all_zone_strings:
        zoning_raw = all_zone_strings[0]
    else:
        zoning_raw = _get_attr(zoning_results, "ZONE_CLASS", "ZONE")
        if not zoning_raw:
            zoning_raw = _get_attr(zoning_ch1a_results, "ZONE_CLASS", "ZONE")

    zone = normalize_zone(zoning_raw)
    hd = extract_height_district(zoning_raw)
    overlays = extract_overlays(zoning_raw)
    q_conds = extract_q_conditions(zoning_raw)
    d_lims = extract_d_limitations(zoning_raw)

    # Run the richer zone string parser
    zoning_parse: ZoningParseResult | None = None
    if zoning_raw:
        zoning_parse = parse_zoning_string(
            zoning_raw,
            q_ordinances=q_conds if q_conds else None,
            d_ordinances=d_lims if d_lims else None,
        )

    if zoning_ambiguous:
        issues.append(
            ReviewIssue(
                id="INGEST-ZONE-002",
                category="zoning",
                severity="high",
                title="Multiple distinct zoning designations at query point",
                description=(
                    f"Identify returned {len(all_zone_strings)} distinct zone strings: "
                    f"{', '.join(all_zone_strings)}. Selected '{zoning_raw}' as most "
                    "frequent. The geocoded point may be near a zone boundary; verify "
                    "which zone applies to the subject parcel."
                ),
                affected_fields=["zone", "zoning_string_raw"],
                suggested_review_role="planner",
            )
        )

    # Chapter inference
    layer_source = None
    chapter_confidence = "unknown"
    if zoning_results and not zoning_ch1a_results:
        layer_source = "zoning"
        chapter_confidence = "medium"
    elif zoning_ch1a_results and not zoning_results:
        layer_source = "zoning_ch1a"
        chapter_confidence = "medium"
    elif zoning_results and zoning_ch1a_results:
        layer_source = None
        chapter_confidence = "low"
        issues.append(
            ReviewIssue(
                id="INGEST-CHAP-001",
                category="zoning",
                severity="high",
                title="Both Chapter 1 and Chapter 1A zoning layers returned results",
                description=(
                    "Cannot determine chapter applicability from ZIMAS data alone. "
                    "Downstream calculations (density area, alley credit) depend on this determination."
                ),
                affected_fields=["zone_code_chapter", "chapter_applicability_confidence"],
                suggested_review_role="zoning consultant",
                blocking=True,
            )
        )
    else:
        issues.append(
            ReviewIssue(
                id="INGEST-ZONE-001",
                category="zoning",
                severity="critical",
                title="No zoning data returned from ZIMAS",
                description="Neither Chapter 1 nor Chapter 1A zoning layers returned results.",
                affected_fields=["zone", "zoning_string_raw"],
                suggested_review_role="planner",
                blocking=True,
            )
        )

    zone_chapter = infer_chapter(zone, layer_source)

    if zoning_raw:
        sources.append(
            DataSource(
                field="zoning_string_raw",
                source="ZIMAS zoning layer",
                raw_reference=zoning_raw,
                confidence="auto_review",
                notes="ZIMAS disclaims guaranteed accuracy.",
            )
        )

    # --- General Plan Land Use ---
    gp_results = _find_layer_results(identify_data, ZIMAS_LAYERS["gp_land_use"])
    gp_land_use = _get_attr(gp_results, "GPLU", "GP_ZONE", "LAND_USE")
    if gp_land_use:
        sources.append(
            DataSource(field="general_plan_land_use", source="ZIMAS GP layer", confidence="auto_review")
        )

    # --- Community Plan ---
    cp_results = _find_layer_results(identify_data, ZIMAS_LAYERS["community_plan"])
    community_plan = _get_attr(cp_results, "CP_NAME", "NAME", "COMMUNITY_PLAN")
    if community_plan:
        sources.append(
            DataSource(field="community_plan_area", source="ZIMAS community plan layer", confidence="auto_review")
        )

    # --- TOC ---
    toc_results = _find_layer_results(identify_data, ZIMAS_LAYERS["toc"])
    toc_tier_raw = _get_attr(toc_results, "TOC_TIER", "TIER")
    toc_tier = None
    if toc_tier_raw:
        try:
            toc_tier = int(toc_tier_raw)
            sources.append(
                DataSource(field="toc_tier", source="ZIMAS TOC layer", confidence="auto_review")
            )
        except ValueError:
            issues.append(
                ReviewIssue(
                    id="INGEST-TOC-001",
                    category="transit",
                    severity="medium",
                    title="Non-numeric TOC tier value",
                    description=f"Raw TOC tier value '{toc_tier_raw}' could not be parsed.",
                    affected_fields=["toc_tier"],
                    suggested_review_role="planner",
                )
            )

    # --- AB 2097 ---
    ab2097_results = _find_layer_results(identify_data, ZIMAS_LAYERS["ab2097"])
    ab2097_area = len(ab2097_results) > 0 and bool(_get_attr(ab2097_results, "OBJECTID", "FID"))
    if ab2097_results:
        sources.append(
            DataSource(field="ab2097_area", source="ZIMAS AB 2097 layer", confidence="auto_review")
        )

    # --- Coastal ---
    coastal_results = _find_layer_results(identify_data, ZIMAS_LAYERS["coastal"])
    coastal_zone = len(coastal_results) > 0 and bool(_get_attr(coastal_results, "OBJECTID", "FID", "ZONE"))

    # --- Parcel ---
    parcel_attrs, parcel_sources, parcel_issues = extract_parcel_data(identify_data)
    sources.extend(parcel_sources)
    issues.extend(parcel_issues)

    # --- Diagnostics ---
    returned_layer_ids = sorted({r.get("layerId") for r in identify_data.get("results", [])})

    # Determine site basis
    multi = parcel_attrs.get("multiple_parcels", False)
    pcount = parcel_attrs.get("parcel_count", 1)
    if multi:
        site_basis = "single_parcel_assumed"
        site_basis_note = (
            f"{pcount} parcels returned at query point but only the first was used. "
            "If the project spans multiple parcels, supply explicit APN list."
        )
    else:
        site_basis = "single_parcel_assumed"
        site_basis_note = "Address-only geocode resolved to a single parcel."

    # --- Build Site ---
    site = Site(
        address=address,
        apn=parcel_attrs.get("apn"),
        coordinates=coordinates,
        zoning_string_raw=zoning_raw,
        zone=zone,
        zone_code_chapter=zone_chapter,
        height_district=hd,
        general_plan_land_use=gp_land_use,
        community_plan_area=community_plan,
        overlay_zones=overlays,
        q_conditions=q_conds,
        d_limitations=d_lims,
        coastal_zone=coastal_zone if coastal_results else None,
        toc_tier=toc_tier,
        ab2097_area=ab2097_area if ab2097_results else None,
        lot_area_sf=parcel_attrs.get("lot_area_sf"),
        parcel_geometry=parcel_attrs.get("parcel_geometry"),
        multiple_parcels=multi,
        parcel_count=pcount,
        site_basis=site_basis,
        site_basis_note=site_basis_note,
        chapter_applicability_confidence=chapter_confidence,
        parcel_match_confidence="auto_review" if parcel_attrs.get("apn") else "unknown",
        diag_all_zone_strings=all_zone_strings,
        diag_zoning_ambiguous=zoning_ambiguous,
        diag_zoning_layer_count=len(zoning_results),
        diag_parcel_layer_count=len(_find_layer_results(identify_data, ZIMAS_LAYERS["parcels"])),
        diag_identify_layers_returned=returned_layer_ids,
        data_sources=sources,
        raw_source_files=raw_files,
        pull_timestamp=pull_timestamp,
    )

    return site, zoning_parse, issues
