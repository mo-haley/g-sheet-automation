from __future__ import annotations

"""Parse and merge ZIMAS API responses into a Site model."""

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

    zoning_raw = _get_attr(zoning_results, "ZONE_CMPLT", "ZONE_CLASS", "ZONE")
    # Ch1A fallback: if no Ch1 zoning string, use Ch1A string
    if not zoning_raw:
        zoning_raw = _get_attr(zoning_ch1a_results, "ZONE_CMPLT", "ZONE_CLASS", "ZONE")
    zone = normalize_zone(zoning_raw)
    hd = extract_height_district(zoning_raw)
    overlays = extract_overlays(zoning_raw)
    q_conds = extract_q_conditions(zoning_raw)
    d_lims = extract_d_limitations(zoning_raw)

    # Run the richer zone string parser. Provides D/Q ordinance numbers,
    # supplemental districts, and parse_confidence. Pass raw q/d bracket values
    # as ordinance hints (they may contain ordinance numbers in some ZIMAS responses).
    zoning_parse: ZoningParseResult | None = None
    if zoning_raw:
        zoning_parse = parse_zoning_string(
            zoning_raw,
            q_ordinances=q_conds if q_conds else None,
            d_ordinances=d_lims if d_lims else None,
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
        multiple_parcels=parcel_attrs.get("multiple_parcels", False),
        parcel_count=parcel_attrs.get("parcel_count", 1),
        chapter_applicability_confidence=chapter_confidence,
        parcel_match_confidence="auto_review" if parcel_attrs.get("apn") else "unknown",
        data_sources=sources,
        raw_source_files=raw_files,
        pull_timestamp=pull_timestamp,
    )

    return site, zoning_parse, issues
