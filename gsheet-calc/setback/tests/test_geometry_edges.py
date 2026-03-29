"""Tests for setback geometry edge derivation.

Covers:
  1. Rectangular parcel polygon → 4 edges with correct lengths
  2. Rotated / non-axis-aligned polygon → still works
  3. Malformed or missing geometry → no edges, conservative skip
  4. build_setback_inputs_from_site() integration
  5. Coverage improves from THIN to PARTIAL with valid geometry
  6. Ambiguous geometry does not produce COMPLETE
"""

from __future__ import annotations

import math

import pytest

from setback.geometry_edges import derive_edges_from_geometry


# ---------------------------------------------------------------------------
# Helpers — build ESRI-format geometry dicts
# ---------------------------------------------------------------------------

def _esri_polygon(ring: list[list[float]], wkid: int = 2229) -> dict:
    """Build a minimal ESRI polygon geometry dict."""
    return {
        "rings": [ring],
        "spatialReference": {"wkid": wkid, "latestWkid": wkid},
    }


# A simple rectangular parcel: 50ft wide × 120ft deep
# Origin at (0, 0), axis-aligned
_RECT_RING = [
    [0.0, 0.0],
    [50.0, 0.0],
    [50.0, 120.0],
    [0.0, 120.0],
    [0.0, 0.0],  # closing point
]

# The same parcel from actual ZIMAS data (State Plane coords, 4 vertices + close)
_ZIMAS_RING = [
    [6445254.5077500045, 1842330.2205000073],
    [6445261.019749999, 1842381.518749997],
    [6445378.688749999, 1842353.992750004],
    [6445373.753250003, 1842315.064500004],
    [6445254.5077500045, 1842330.2205000073],
]

# A rotated rectangle (45° rotation, roughly 40ft × 100ft)
_ROTATED_RING = [
    [100.0, 100.0],
    [100.0 + 40 * math.cos(math.radians(45)), 100.0 + 40 * math.sin(math.radians(45))],
    [100.0 + 40 * math.cos(math.radians(45)) - 100 * math.sin(math.radians(45)),
     100.0 + 40 * math.sin(math.radians(45)) + 100 * math.cos(math.radians(45))],
    [100.0 - 100 * math.sin(math.radians(45)),
     100.0 + 100 * math.cos(math.radians(45))],
    [100.0, 100.0],  # close
]

# Pentagon (5 sides) — irregular but still within point limit
_PENTAGON_RING = [
    [0.0, 0.0],
    [60.0, 0.0],
    [80.0, 50.0],
    [30.0, 90.0],
    [-10.0, 50.0],
    [0.0, 0.0],
]


# ---------------------------------------------------------------------------
# 1. Rectangular parcel → 4 edges with correct lengths
# ---------------------------------------------------------------------------

class TestRectangularParcel:
    def test_returns_four_edges(self):
        edges, meta = derive_edges_from_geometry(_esri_polygon(_RECT_RING))
        assert len(edges) == 4

    def test_all_edges_typed_interior(self):
        edges, _ = derive_edges_from_geometry(_esri_polygon(_RECT_RING))
        for e in edges:
            assert e.edge_type == "interior"

    def test_edge_ids_are_stable(self):
        edges, _ = derive_edges_from_geometry(_esri_polygon(_RECT_RING))
        ids = [e.edge_id for e in edges]
        assert ids == ["geom_edge_0", "geom_edge_1", "geom_edge_2", "geom_edge_3"]

    def test_lot_dimensions_estimated(self):
        """Shorter pair avg ≈ 50, longer pair avg ≈ 120."""
        _, meta = derive_edges_from_geometry(_esri_polygon(_RECT_RING))
        assert meta["lot_width_ft"] == 50.0
        assert meta["lot_depth_ft"] == 120.0

    def test_meta_counts(self):
        _, meta = derive_edges_from_geometry(_esri_polygon(_RECT_RING))
        assert meta["ring_points"] == 4
        assert meta["segments_raw"] == 4
        assert meta["segments_kept"] == 4
        assert meta["skip_reason"] is None

    def test_zimas_real_geometry(self):
        """Parcel from actual ZIMAS cache should produce 4 edges."""
        edges, meta = derive_edges_from_geometry(_esri_polygon(_ZIMAS_RING))
        assert len(edges) == 4
        assert meta["lot_width_ft"] is not None
        assert meta["lot_depth_ft"] is not None
        assert meta["skip_reason"] is None


# ---------------------------------------------------------------------------
# 2. Rotated / non-axis-aligned polygon
# ---------------------------------------------------------------------------

class TestRotatedParcel:
    def test_rotated_rectangle_produces_four_edges(self):
        edges, meta = derive_edges_from_geometry(_esri_polygon(_ROTATED_RING))
        assert len(edges) == 4

    def test_rotated_lot_dimensions(self):
        _, meta = derive_edges_from_geometry(_esri_polygon(_ROTATED_RING))
        # Should detect ~40 and ~100 regardless of rotation
        assert meta["lot_width_ft"] is not None
        assert meta["lot_depth_ft"] is not None
        assert abs(meta["lot_width_ft"] - 40.0) < 1.0
        assert abs(meta["lot_depth_ft"] - 100.0) < 1.0

    def test_pentagon_produces_five_edges(self):
        edges, meta = derive_edges_from_geometry(_esri_polygon(_PENTAGON_RING))
        assert len(edges) == 5
        # Non-rectangular: no width/depth estimate
        assert meta["lot_width_ft"] is None
        assert meta["lot_depth_ft"] is None


# ---------------------------------------------------------------------------
# 3. Malformed or missing geometry → no edges
# ---------------------------------------------------------------------------

class TestMalformedGeometry:
    def test_none_geometry(self):
        edges, meta = derive_edges_from_geometry(None)
        assert edges == []
        assert meta["skip_reason"] == "no geometry provided"

    def test_empty_dict(self):
        edges, meta = derive_edges_from_geometry({})
        assert edges == []
        assert meta["skip_reason"] is not None

    def test_empty_rings(self):
        edges, meta = derive_edges_from_geometry({"rings": []})
        assert edges == []
        assert "no rings" in meta["skip_reason"]

    def test_degenerate_ring_two_points(self):
        geom = _esri_polygon([[0, 0], [10, 0], [0, 0]])
        edges, meta = derive_edges_from_geometry(geom)
        assert edges == []
        assert meta["skip_reason"] is not None

    def test_too_many_points_skips(self):
        """A very complex boundary (> 20 points) is skipped."""
        ring = [[float(i), float(i * 10)] for i in range(25)]
        ring.append(ring[0])  # close
        geom = _esri_polygon(ring)
        edges, meta = derive_edges_from_geometry(geom)
        assert edges == []
        assert "complex boundary" in meta["skip_reason"]

    def test_non_dict_geometry(self):
        edges, meta = derive_edges_from_geometry("not a dict")
        assert edges == []
        assert meta["skip_reason"] == "no geometry provided"

    def test_ring_with_tiny_segments_only(self):
        """All segments < 2ft → no usable edges."""
        ring = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5], [0.0, 0.0]]
        geom = _esri_polygon(ring)
        edges, meta = derive_edges_from_geometry(geom)
        assert edges == []
        assert "too few" in meta["skip_reason"]


# ---------------------------------------------------------------------------
# 4. build_setback_inputs_from_site() integration
# ---------------------------------------------------------------------------

class TestBuildSetbackInputsIntegration:
    def test_with_valid_geometry(self):
        """Site with parcel_geometry should produce edges in SetbackProjectInputs."""
        from models.site import Site
        from analysis.app_orchestrator import build_setback_inputs_from_site

        site = Site(
            address="1425 S Robertson Blvd",
            parcel_geometry=_esri_polygon(_RECT_RING),
        )
        inputs = build_setback_inputs_from_site(site)
        assert len(inputs.edges) == 4
        assert inputs.lot_width == 50.0
        assert inputs.lot_depth == 120.0

    def test_without_geometry(self):
        """Site without parcel_geometry should produce empty edges (THIN behavior)."""
        from models.site import Site
        from analysis.app_orchestrator import build_setback_inputs_from_site

        site = Site(address="123 Fake St")
        inputs = build_setback_inputs_from_site(site)
        assert len(inputs.edges) == 0
        assert inputs.lot_width is None
        assert inputs.lot_depth is None


# ---------------------------------------------------------------------------
# 5. Coverage improves from THIN to PARTIAL with valid geometry
# ---------------------------------------------------------------------------

class TestCoverageImprovement:
    def test_thin_without_geometry(self):
        """No geometry → THIN coverage (unchanged behavior)."""
        from models.site import Site
        from models.project import Project
        from analysis.app_orchestrator import build_setback_inputs_from_site
        from setback.setback_orchestrator import run_setback_module

        site = Site(address="123 Fake St", zone="C2")
        inputs = build_setback_inputs_from_site(site)

        result = run_setback_module(
            project_inputs=inputs,
            raw_zone="C2-1VL",
            base_zone="C2",
        )
        assert result.coverage_level.value == "thin"

    def test_partial_with_geometry(self):
        """Valid geometry → PARTIAL coverage (improvement)."""
        from models.site import Site
        from models.project import Project
        from analysis.app_orchestrator import build_setback_inputs_from_site
        from setback.setback_orchestrator import run_setback_module

        site = Site(
            address="1425 S Robertson Blvd",
            zone="C2",
            parcel_geometry=_esri_polygon(_RECT_RING),
        )
        inputs = build_setback_inputs_from_site(site)

        result = run_setback_module(
            project_inputs=inputs,
            raw_zone="C2-1VL",
            base_zone="C2",
        )
        assert result.coverage_level.value == "partial"


# ---------------------------------------------------------------------------
# 6. Ambiguous geometry does not produce COMPLETE
# ---------------------------------------------------------------------------

class TestNoFalseComplete:
    def test_derived_edges_never_produce_complete(self):
        """Geometry-derived edges (all interior) should never reach COMPLETE.

        COMPLETE requires overall_status in ("confirmed", "overridden"),
        which requires confirmed edge classifications. All-interior edges
        on an interior lot produce manual_confirm, so overall_status will
        not be "confirmed".
        """
        from models.site import Site
        from analysis.app_orchestrator import build_setback_inputs_from_site
        from setback.setback_orchestrator import run_setback_module

        site = Site(
            address="Test",
            zone="R3",
            parcel_geometry=_esri_polygon(_RECT_RING),
        )
        inputs = build_setback_inputs_from_site(site)

        result = run_setback_module(
            project_inputs=inputs,
            raw_zone="R3-1",
            base_zone="R3",
        )
        assert result.coverage_level.value != "complete"
        # Should be partial (edges present, but not fully confirmed)
        assert result.coverage_level.value == "partial"

    def test_complex_geometry_stays_thin(self):
        """Unusable geometry should not change coverage from THIN."""
        from models.site import Site
        from analysis.app_orchestrator import build_setback_inputs_from_site
        from setback.setback_orchestrator import run_setback_module

        site = Site(
            address="Test",
            zone="C2",
            parcel_geometry={"rings": []},  # malformed
        )
        inputs = build_setback_inputs_from_site(site)

        result = run_setback_module(
            project_inputs=inputs,
            raw_zone="C2-1VL",
            base_zone="C2",
        )
        assert result.coverage_level.value == "thin"
