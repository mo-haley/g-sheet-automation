"""Derive setback edge inputs from ESRI parcel polygon geometry.

Parses the ZIMAS parcel geometry (ESRI polygon with rings in California
State Plane Zone V, feet) into EdgeInput objects suitable for the setback
pipeline.

Conservative posture:
  - All derived edges use edge_type="interior" because geometry alone
    cannot distinguish street-facing from interior lot lines.
  - The setback edge classifier will assign manual_confirm confidence
    to all derived edges (no rear/front anchor from geometry).
  - This moves coverage from THIN (no edges) to PARTIAL (edges present,
    classifications provisional) — an honest improvement.

Coordinate system:
  ZIMAS Landbase layer 105 uses WKID 2229 (California State Plane Zone V),
  which is in US survey feet. Euclidean distance between consecutive
  points gives segment length in feet directly.
"""

from __future__ import annotations

import math
from typing import Any

from setback.models import EdgeInput


# Minimum segment length (feet) to include as a real lot edge.
# Shorter segments are typically closing-point artifacts or slivers.
_MIN_EDGE_LENGTH_FT: float = 2.0

# Maximum number of ring points to consider a "simple" parcel.
# Parcels with very complex boundaries (e.g. flag lots with access strips)
# are not reliably decomposable into clean lot edges.
_MAX_RING_POINTS: int = 20


def _segment_length(p1: list[float], p2: list[float]) -> float:
    """Euclidean distance between two State Plane coordinate points (feet)."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    return math.sqrt(dx * dx + dy * dy)


def _segment_bearing_deg(p1: list[float], p2: list[float]) -> float:
    """Compass bearing in degrees (0=N, 90=E, 180=S, 270=W).

    Uses State Plane coordinates where X = easting, Y = northing.
    """
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    angle = math.degrees(math.atan2(dx, dy)) % 360
    return angle


def derive_edges_from_geometry(
    parcel_geometry: dict[str, Any] | None,
) -> tuple[list[EdgeInput], dict[str, Any]]:
    """Extract lot-line edges from ESRI parcel polygon geometry.

    Args:
        parcel_geometry: The Site.parcel_geometry dict, expected to have
            "rings" (list of coordinate rings). May be None.

    Returns:
        Tuple of:
          - List of EdgeInput objects (may be empty if geometry is unusable)
          - Metadata dict with derivation details:
              "source": "parcel_geometry"
              "ring_points": number of unique ring points
              "segments_raw": total segments before filtering
              "segments_kept": segments kept after min-length filter
              "lot_width_ft": estimated lot width (shorter pair avg), or None
              "lot_depth_ft": estimated lot depth (longer pair avg), or None
              "skip_reason": reason if no edges returned, or None
    """
    meta: dict[str, Any] = {
        "source": "parcel_geometry",
        "ring_points": 0,
        "segments_raw": 0,
        "segments_kept": 0,
        "lot_width_ft": None,
        "lot_depth_ft": None,
        "skip_reason": None,
    }

    if not parcel_geometry or not isinstance(parcel_geometry, dict):
        meta["skip_reason"] = "no geometry provided"
        return [], meta

    rings = parcel_geometry.get("rings")
    if not rings or not isinstance(rings, list) or len(rings) == 0:
        meta["skip_reason"] = "no rings in geometry"
        return [], meta

    # Use only the exterior ring (first ring). Interior rings (holes)
    # are not relevant for lot-line setback edges.
    ring = rings[0]
    if not isinstance(ring, list) or len(ring) < 4:
        # Need at least 3 unique points + closing point for a polygon
        meta["skip_reason"] = f"ring too short ({len(ring) if isinstance(ring, list) else 0} points)"
        return [], meta

    # Remove closing point if it duplicates the first
    if ring[0][0] == ring[-1][0] and ring[0][1] == ring[-1][1]:
        ring = ring[:-1]

    unique_points = len(ring)
    meta["ring_points"] = unique_points

    if unique_points < 3:
        meta["skip_reason"] = f"degenerate polygon ({unique_points} unique points)"
        return [], meta

    if unique_points > _MAX_RING_POINTS:
        meta["skip_reason"] = (
            f"complex boundary ({unique_points} points, max {_MAX_RING_POINTS}); "
            "automated edge derivation not reliable"
        )
        return [], meta

    # Build segments
    segments: list[dict[str, Any]] = []
    for i in range(unique_points):
        p1 = ring[i]
        p2 = ring[(i + 1) % unique_points]

        if not (isinstance(p1, (list, tuple)) and len(p1) >= 2
                and isinstance(p2, (list, tuple)) and len(p2) >= 2):
            meta["skip_reason"] = "malformed coordinate data"
            return [], meta

        length = _segment_length(p1, p2)
        bearing = _segment_bearing_deg(p1, p2)
        segments.append({
            "index": i,
            "p1": p1,
            "p2": p2,
            "length_ft": length,
            "bearing_deg": bearing,
        })

    meta["segments_raw"] = len(segments)

    # Filter out tiny segments (closing artifacts, slivers)
    kept = [s for s in segments if s["length_ft"] >= _MIN_EDGE_LENGTH_FT]
    meta["segments_kept"] = len(kept)

    if len(kept) < 3:
        meta["skip_reason"] = f"too few usable segments ({len(kept)} after filtering)"
        return [], meta

    # Estimate lot width / depth for rectangular-ish parcels (4 kept segments)
    if len(kept) == 4:
        lengths = sorted(s["length_ft"] for s in kept)
        # Shorter pair ≈ width, longer pair ≈ depth (typical LA lot orientation)
        meta["lot_width_ft"] = round((lengths[0] + lengths[1]) / 2, 1)
        meta["lot_depth_ft"] = round((lengths[2] + lengths[3]) / 2, 1)

    # Build EdgeInputs — all typed as "interior" (conservative: we don't
    # know which edges face streets, alleys, or neighbors)
    edges: list[EdgeInput] = []
    for s in kept:
        edges.append(EdgeInput(
            edge_id=f"geom_edge_{s['index']}",
            edge_type="interior",
        ))

    return edges, meta
