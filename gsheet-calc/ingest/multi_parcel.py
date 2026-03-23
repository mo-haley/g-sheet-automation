"""Multi-parcel site assembly from user-supplied APN list.

Queries ZIMAS for each APN, merges parcel attributes and zoning data
into a single Site. Conservative merge rules:
  - lot area: summed across all parcels
  - zoning: must be consistent; flagged if not
  - overlays: union of all overlays
  - TOC tier: minimum (most conservative)
  - parcel geometry: not merged (future work)
"""

from __future__ import annotations

import logging

from ingest.parser import parse_zimas_response
from ingest.zimas import ZIMASClient
from models.issue import ReviewIssue
from models.site import DataSource, Site

logger = logging.getLogger(__name__)


def _parcel_centroid_stateplane(features: list[dict]) -> tuple[float, float] | None:
    """Compute the centroid of parcel features in state plane coordinates."""
    all_x: list[float] = []
    all_y: list[float] = []
    for feat in features:
        geom = feat.get("geometry", {})
        for ring in geom.get("rings", []):
            for pt in ring:
                all_x.append(pt[0])
                all_y.append(pt[1])
    if not all_x:
        return None
    return (sum(all_x) / len(all_x), sum(all_y) / len(all_y))


def resolve_multi_parcel_site(
    apn_list: list[str],
    address: str,
    zimas: ZIMASClient,
) -> tuple[Site, list[ReviewIssue]]:
    """Resolve a multi-parcel site from a list of APNs.

    For each APN:
      1. Query layer 105 for parcel features (BPP match)
      2. Compute the parcel centroid
      3. Run a full identify at that centroid
      4. Parse into a per-parcel Site

    Then merge all per-parcel Sites into one combined Site.

    Returns:
        Tuple of (merged Site, list of ReviewIssues).
    """
    issues: list[ReviewIssue] = []
    per_parcel: list[dict] = []  # {apn, site, features, identify_data, issues}

    for apn in apn_list:
        apn_clean = apn.strip()
        if not apn_clean:
            continue

        # Step 1: Query parcel features by BPP
        features = zimas.query_parcel_by_bpp(apn_clean)
        if not features:
            issues.append(ReviewIssue(
                id="MULTI-APN-001",
                category="ingest",
                severity="high",
                title=f"APN {apn_clean} not found in ZIMAS Landbase",
                description=(
                    f"No parcel features returned for BPP='{apn_clean}'. "
                    "Verify the APN is a valid 10-digit LA County assessor number."
                ),
                affected_fields=["lot_area_sf", "apn"],
                suggested_review_role="planner",
            ))
            continue

        # Step 2: Compute centroid
        centroid = _parcel_centroid_stateplane(features)
        if centroid is None:
            issues.append(ReviewIssue(
                id="MULTI-APN-002",
                category="ingest",
                severity="high",
                title=f"APN {apn_clean}: no geometry for centroid",
                description="Parcel features found but no geometry available for centroid calculation.",
                affected_fields=["lot_area_sf"],
                suggested_review_role="planner",
            ))
            continue

        # Step 3: Identify at centroid (use tight tolerance since we're at parcel center)
        identify_data = zimas.identify_at_stateplane(centroid[0], centroid[1])

        # Step 4: Parse into a Site
        parcel_site, _zp, parcel_issues = parse_zimas_response(
            address, identify_data, pull_timestamp=zimas.pull_timestamp
        )

        # Sum sub-lot areas from the BPP query (more accurate than identify)
        total_area = sum(
            float(f.get("attributes", {}).get("Shape_Area", 0))
            for f in features
        )
        if total_area > 0:
            parcel_site.lot_area_sf = total_area

        lots = [f.get("attributes", {}).get("MODLOT", "?") for f in features]
        logger.info(
            "APN %s: %d sub-lots %s, area=%.1f sf, zone=%s",
            apn_clean, len(features), lots, total_area, parcel_site.zone,
        )

        per_parcel.append({
            "apn": apn_clean,
            "site": parcel_site,
            "features": features,
            "area": total_area,
            "issues": parcel_issues,
        })

    if not per_parcel:
        # All APNs failed — return a minimal Site with issues
        return Site(address=address, site_basis="multi_parcel_user",
                    site_basis_note="All user-supplied APNs failed to resolve."), issues

    # --- Merge ---
    primary = per_parcel[0]["site"]
    all_apns = [p["apn"] for p in per_parcel]

    # Lot area: sum
    total_lot_area = sum(p["area"] for p in per_parcel)

    # Zoning consistency check
    zone_strings = list(dict.fromkeys(
        p["site"].zoning_string_raw for p in per_parcel if p["site"].zoning_string_raw
    ))
    base_zones = list(dict.fromkeys(
        p["site"].zone for p in per_parcel if p["site"].zone
    ))
    zoning_consistent = len(base_zones) <= 1

    if not zoning_consistent:
        issues.append(ReviewIssue(
            id="MULTI-ZONE-001",
            category="zoning",
            severity="critical",
            title="Inconsistent zoning across selected parcels",
            description=(
                f"Parcels span {len(base_zones)} distinct base zones: "
                f"{', '.join(base_zones)}. Raw zone strings: {', '.join(zone_strings)}. "
                "Downstream modules assume a single governing zone. "
                "Results may not be reliable for split-zone sites."
            ),
            affected_fields=["zone", "zoning_string_raw", "height_district"],
            suggested_review_role="zoning consultant",
            blocking=True,
        ))

    # Height district consistency
    hds = list(dict.fromkeys(
        p["site"].height_district for p in per_parcel if p["site"].height_district
    ))
    if len(hds) > 1:
        issues.append(ReviewIssue(
            id="MULTI-HD-001",
            category="zoning",
            severity="high",
            title="Inconsistent height districts across parcels",
            description=(
                f"Height districts: {', '.join(hds)}. "
                "Using the first parcel's height district. Verify which governs."
            ),
            affected_fields=["height_district"],
            suggested_review_role="planner",
        ))

    # Overlays: union
    all_overlays: list[str] = []
    seen_overlays: set[str] = set()
    for p in per_parcel:
        for ov in p["site"].overlay_zones:
            if ov not in seen_overlays:
                all_overlays.append(ov)
                seen_overlays.add(ov)

    # TOC: conservative (minimum across parcels, or None)
    toc_tiers = [p["site"].toc_tier for p in per_parcel if p["site"].toc_tier is not None]
    toc_tier = min(toc_tiers) if toc_tiers else None
    if toc_tiers and len(set(toc_tiers)) > 1:
        issues.append(ReviewIssue(
            id="MULTI-TOC-001",
            category="transit",
            severity="medium",
            title="Inconsistent TOC tiers across parcels",
            description=(
                f"TOC tiers: {', '.join(str(t) for t in toc_tiers)}. "
                f"Using minimum (Tier {toc_tier}) as conservative assumption."
            ),
            affected_fields=["toc_tier"],
            suggested_review_role="planner",
        ))

    # AB 2097: any parcel in area → whole site in area
    ab2097 = any(p["site"].ab2097_area for p in per_parcel if p["site"].ab2097_area is not None)

    # Q conditions / D limitations: union
    q_conds = list(dict.fromkeys(
        q for p in per_parcel for q in p["site"].q_conditions
    ))
    d_lims = list(dict.fromkeys(
        d for p in per_parcel for d in p["site"].d_limitations
    ))

    # Collect per-parcel issues
    for p in per_parcel:
        issues.extend(p["issues"])

    # Build merged site
    merged = Site(
        address=address,
        apn=", ".join(all_apns),
        coordinates=primary.coordinates,
        zoning_string_raw=primary.zoning_string_raw,
        zone=primary.zone,
        zone_code_chapter=primary.zone_code_chapter,
        height_district=primary.height_district,
        general_plan_land_use=primary.general_plan_land_use,
        community_plan_area=primary.community_plan_area,
        overlay_zones=all_overlays,
        q_conditions=q_conds,
        d_limitations=d_lims,
        toc_tier=toc_tier,
        ab2097_area=ab2097,
        lot_area_sf=total_lot_area if total_lot_area > 0 else None,
        multiple_parcels=len(per_parcel) > 1,
        parcel_count=sum(len(p["features"]) for p in per_parcel),
        site_basis="multi_parcel_user",
        site_basis_note=(
            f"Site assembled from {len(per_parcel)} user-specified APN(s): "
            f"{', '.join(all_apns)}. Total lot area summed across all sub-lots. "
            f"{'Zoning is consistent.' if zoning_consistent else 'WARNING: zoning is INCONSISTENT across parcels.'} "
            "Contiguity not verified."
        ),
        chapter_applicability_confidence=primary.chapter_applicability_confidence,
        parcel_match_confidence="user_specified",
        diag_all_zone_strings=zone_strings,
        diag_zoning_ambiguous=not zoning_consistent,
        diag_zoning_layer_count=primary.diag_zoning_layer_count,
        diag_parcel_layer_count=sum(len(p["features"]) for p in per_parcel),
        diag_identify_layers_returned=primary.diag_identify_layers_returned,
        data_sources=[
            DataSource(
                field="lot_area_sf",
                source="ZIMAS Landbase layer 105 (multi-parcel BPP query)",
                raw_reference=f"BPP: {', '.join(all_apns)}",
                confidence="auto_review",
                notes=f"Summed from {sum(len(p['features']) for p in per_parcel)} sub-lot(s) across {len(per_parcel)} assessor parcel(s).",
            ),
        ],
        pull_timestamp=zimas.pull_timestamp,
    )

    return merged, issues
