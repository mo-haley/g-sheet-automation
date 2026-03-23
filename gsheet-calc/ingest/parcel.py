"""GeoHub parcel data extraction from ZIMAS identify results."""

from __future__ import annotations

import logging

from models.issue import ReviewIssue
from models.site import DataSource

logger = logging.getLogger(__name__)


def extract_parcel_data(
    identify_results: dict,
) -> tuple[dict, list[DataSource], list[ReviewIssue]]:
    """Extract parcel attributes from ZIMAS identify response.

    Layer 105 is the Landbase layer. With a generous identify tolerance,
    multiple parcel features may be returned. When they share the same
    BPP (Book-Page-Parcel / assessor parcel number), they are sub-lots
    of the same assessor parcel — their areas are summed.

    Returns:
        Tuple of (parcel_attrs dict, data_sources list, issues list).
    """
    attrs: dict = {}
    sources: list[DataSource] = []
    issues: list[ReviewIssue] = []

    results = identify_results.get("results", [])
    parcel_results = [r for r in results if r.get("layerId") == 105]

    if not parcel_results:
        issues.append(
            ReviewIssue(
                id="INGEST-PARCEL-001",
                category="ingest",
                severity="high",
                title="No parcel data returned from ZIMAS",
                description="ZIMAS identify did not return Landbase layer (105) results at this location.",
                affected_fields=["lot_area_sf", "apn", "parcel_geometry"],
                suggested_review_role="planner",
                blocking=True,
            )
        )
        return attrs, sources, issues

    # Group parcels by BPP (assessor parcel number). Sub-lots of the same
    # assessor parcel share the same BPP.
    by_bpp: dict[str, list[dict]] = {}
    no_bpp: list[dict] = []
    for pr in parcel_results:
        pa = pr.get("attributes", {})
        bpp = pa.get("BPP", "").strip()
        if bpp:
            by_bpp.setdefault(bpp, []).append(pr)
        else:
            no_bpp.append(pr)

    # Extract APN — prefer BPP (10-digit assessor number), fall back to PIND/PIN
    def _extract_apn(parcel_attrs: dict) -> tuple[str | None, str | None]:
        for key in ("BPP", "PIND", "PIN", "AIN", "APN", "PARCEL_ID"):
            val = parcel_attrs.get(key)
            if val is not None and str(val).strip():
                return str(val).strip(), key
        return None, None

    # Pick the primary parcel. If there's exactly one BPP group, use it.
    # If multiple BPP groups, take the first (closest to geocode point) and flag.
    if len(by_bpp) == 1:
        bpp_key = next(iter(by_bpp))
        group = by_bpp[bpp_key]
        parcel = group[0]
        parcel_pa = parcel.get("attributes", {})

        apn_val, apn_key = _extract_apn(parcel_pa)
        if apn_val:
            attrs["apn"] = apn_val
            sources.append(
                DataSource(
                    field="apn",
                    source="ZIMAS Landbase layer 105",
                    raw_reference=apn_key,
                    confidence="auto_review",
                )
            )

        # Sum area across all sub-lots in this assessor parcel
        total_area = 0.0
        area_key_used = None
        for pr in group:
            pa = pr.get("attributes", {})
            for key in ("Shape_Area", "SHAPE_Area", "LOT_AREA", "AREA_SF"):
                if key in pa and pa[key]:
                    total_area += float(pa[key])
                    area_key_used = key
                    break

        if total_area > 0:
            attrs["lot_area_sf"] = total_area
            sources.append(
                DataSource(
                    field="lot_area_sf",
                    source="ZIMAS Landbase layer 105",
                    raw_reference=area_key_used,
                    confidence="auto_review",
                    notes=(
                        f"Summed from {len(group)} sub-lot(s) sharing BPP {bpp_key}. "
                        "Verify against survey if available."
                        if len(group) > 1
                        else "ZIMAS-reported lot area. Verify against survey if available."
                    ),
                )
            )

        attrs["parcel_count"] = len(group)
        attrs["multiple_parcels"] = False  # Same assessor parcel, not truly multi-parcel

        lots = [pr.get("attributes", {}).get("MODLOT", pr.get("attributes", {}).get("LOT", "?")) for pr in group]
        logger.info(
            "Single assessor parcel %s: %d sub-lot(s) %s, total area=%.1f sf",
            bpp_key, len(group), lots, total_area,
        )

    elif len(by_bpp) > 1:
        # Multiple distinct assessor parcels — pick first, flag for review
        bpp_key = next(iter(by_bpp))
        group = by_bpp[bpp_key]
        parcel = group[0]
        parcel_pa = parcel.get("attributes", {})

        apn_val, apn_key = _extract_apn(parcel_pa)
        if apn_val:
            attrs["apn"] = apn_val
            sources.append(
                DataSource(
                    field="apn",
                    source="ZIMAS Landbase layer 105",
                    raw_reference=apn_key,
                    confidence="auto_review",
                )
            )

        # Use only the first assessor parcel's area
        total_area = 0.0
        area_key_used = None
        for pr in group:
            pa = pr.get("attributes", {})
            for key in ("Shape_Area", "SHAPE_Area", "LOT_AREA", "AREA_SF"):
                if key in pa and pa[key]:
                    total_area += float(pa[key])
                    area_key_used = key
                    break
        if total_area > 0:
            attrs["lot_area_sf"] = total_area
            sources.append(
                DataSource(
                    field="lot_area_sf",
                    source="ZIMAS Landbase layer 105",
                    raw_reference=area_key_used,
                    confidence="auto_review",
                    notes=(
                        f"Area from assessor parcel {bpp_key} only ({len(group)} sub-lot(s)). "
                        f"{len(by_bpp)} distinct assessor parcels found at query point. "
                        "Site may span multiple parcels — verify lot area."
                    ),
                )
            )

        attrs["multiple_parcels"] = True
        attrs["parcel_count"] = sum(len(g) for g in by_bpp.values())
        all_bpps = list(by_bpp.keys())
        attrs["all_apns"] = all_bpps

        issues.append(
            ReviewIssue(
                id="INGEST-PARCEL-002",
                category="ingest",
                severity="high",
                title="Multiple assessor parcels at query point",
                description=(
                    f"{len(by_bpp)} distinct assessor parcels found (BPPs: {', '.join(all_bpps)}). "
                    f"Using {bpp_key} as primary. If the project spans multiple parcels, "
                    "lot area and parcel geometry will need manual aggregation."
                ),
                affected_fields=["lot_area_sf", "apn", "parcel_geometry", "multiple_parcels"],
                suggested_review_role="planner",
            )
        )

    elif no_bpp:
        # Fallback: no BPP field at all
        parcel = no_bpp[0]
        parcel_pa = parcel.get("attributes", {})

        apn_val, apn_key = _extract_apn(parcel_pa)
        if apn_val:
            attrs["apn"] = apn_val
            sources.append(
                DataSource(
                    field="apn",
                    source="ZIMAS Landbase layer 105",
                    raw_reference=apn_key,
                    confidence="auto_review",
                )
            )

        for key in ("Shape_Area", "SHAPE_Area", "LOT_AREA", "AREA_SF"):
            if key in parcel_pa and parcel_pa[key]:
                attrs["lot_area_sf"] = float(parcel_pa[key])
                sources.append(
                    DataSource(
                        field="lot_area_sf",
                        source="ZIMAS Landbase layer 105",
                        raw_reference=key,
                        confidence="auto_review",
                        notes="ZIMAS-reported lot area. Verify against survey if available.",
                    )
                )
                break

        if len(no_bpp) > 1:
            attrs["multiple_parcels"] = True
            attrs["parcel_count"] = len(no_bpp)

    # Extract geometry from first parcel
    if parcel_results:
        geometry = parcel_results[0].get("geometry")
        if geometry:
            attrs["parcel_geometry"] = geometry

    return attrs, sources, issues
