"""Tests for /run route: rendering, partial data, and calc ledger structure."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from web.app import app, _build_calc_ledger
from models.result import CalcResult
from models.issue import ReviewIssue


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# _build_calc_ledger uses "entries" not "items" (Jinja2 collision guard)
# ---------------------------------------------------------------------------

class TestCalcLedgerKeyNaming:
    def test_ledger_sections_use_entries_key(self):
        """Sections must use 'entries', never 'items', to avoid dict.items collision in Jinja2."""
        area_results = [CalcResult(name="gross_lot_area", value=5000, unit="sf", formula="given")]
        density_results = [CalcResult(name="base_density", value=10, unit="units", formula="area/factor")]

        sections = _build_calc_ledger(
            area_results=area_results,
            density_results=density_results,
            far_results=[],
            parking_results=[],
            os_results=[],
            load_results=[],
            height_results=[],
        )

        assert len(sections) == 2
        for section in sections:
            assert "entries" in section, f"Section '{section['title']}' missing 'entries' key"
            assert "items" not in section, f"Section '{section['title']}' still uses 'items' — will collide with dict.items in Jinja2"
            assert isinstance(section["entries"], list)
            assert len(section["entries"]) > 0

    def test_ledger_entry_has_required_fields(self):
        results = [CalcResult(name="test_calc", value=42, unit="sf", formula="x+y",
                              intermediate_steps=["step1"], code_section="LAMC 12.22")]
        sections = _build_calc_ledger(
            area_results=results, density_results=[], far_results=[],
            parking_results=[], os_results=[], load_results=[], height_results=[],
        )
        entry = sections[0]["entries"][0]
        for key in ("label", "value", "unit", "formula", "steps", "code"):
            assert key in entry, f"Missing key '{key}' in ledger entry"

    def test_empty_results_produce_no_sections(self):
        sections = _build_calc_ledger([], [], [], [], [], [], [])
        assert sections == []


# ---------------------------------------------------------------------------
# /run route: success and partial-data paths
# ---------------------------------------------------------------------------

def _mock_geocode_and_zimas(monkeypatch):
    """Patch geocoder and ZIMAS to avoid real API calls in /run tests."""
    # Geocoder returns a fixed coordinate
    mock_geocoder_cls = MagicMock()
    mock_geocoder_cls.return_value.geocode.return_value = (34.054, -118.384)
    monkeypatch.setattr("web.app.Geocoder", mock_geocoder_cls)

    # ZIMAS returns a response with parcel + zoning layers
    mock_zimas_cls = MagicMock()
    mock_zimas = mock_zimas_cls.return_value
    mock_zimas.pull_timestamp = "2026-01-01T00:00:00Z"
    mock_zimas.identify_status.used_wide_tolerance = False
    mock_zimas.identify_status.critical_layers_resolved = True
    mock_zimas.identify.return_value = {
        "results": [
            {"layerId": 1102, "attributes": {"ZONE_CMPLT": "C2-1VL-O"}},
            {"layerId": 105, "attributes": {
                "BPP": "4305014026", "Shape_Area": 5000.0,
            }},
            {"layerId": 103, "attributes": {"CP_NAME": "Wilshire"}},
        ]
    }
    monkeypatch.setattr("web.app.ZIMASClient", mock_zimas_cls)

    return mock_zimas


class TestRunRoute:
    def test_run_returns_200(self, client, monkeypatch):
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
            "studios": "0", "one_br": "5", "two_br": "3", "three_br": "0",
            "policy_path": "base_zoning",
        })
        assert resp.status_code == 200
        assert b"Feasibility Results" in resp.data

    def test_run_with_zero_units_returns_200(self, client, monkeypatch):
        """A zero-unit submission (all fields blank) should still render."""
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
            "policy_path": "base_zoning",
        })
        assert resp.status_code == 200

    def test_run_missing_address_shows_error(self, client):
        resp = client.post("/run", data={"studios": "5"})
        assert resp.status_code == 200
        assert b"Address is required" in resp.data

    def test_run_renders_calc_ledger(self, client, monkeypatch):
        """The Calculation Detail section should render without the dict.items crash."""
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
            "studios": "0", "one_br": "10", "two_br": "0", "three_br": "0",
            "policy_path": "base_zoning",
        })
        assert resp.status_code == 200
        # The calc detail section header should be present
        assert b"Calculation Detail" in resp.data

    def test_run_with_partial_site_no_500(self, client, monkeypatch):
        """When ZIMAS returns no parcel data, /run should still render (with issues), not 500."""
        mock_geocoder_cls = MagicMock()
        mock_geocoder_cls.return_value.geocode.return_value = (34.0, -118.0)
        monkeypatch.setattr("web.app.Geocoder", mock_geocoder_cls)

        mock_zimas_cls = MagicMock()
        mock_zimas = mock_zimas_cls.return_value
        mock_zimas.pull_timestamp = "2026-01-01T00:00:00Z"
        mock_zimas.identify_status.used_wide_tolerance = True
        mock_zimas.identify_status.critical_layers_resolved = False
        # Only council district returned — no zoning, no parcels
        mock_zimas.identify.return_value = {
            "results": [
                {"layerId": 102, "attributes": {"OBJECTID": 1}},
            ]
        }
        monkeypatch.setattr("web.app.ZIMASClient", mock_zimas_cls)

        resp = client.post("/run", data={
            "address": "123 Fake St, Pasadena, CA",
            "studios": "0", "one_br": "5", "two_br": "0", "three_br": "0",
            "policy_path": "base_zoning",
        })
        assert resp.status_code == 200
        assert b"BLOCKING" in resp.data or b"blocking" in resp.data


# ---------------------------------------------------------------------------
# /run-address route: basic sanity
# ---------------------------------------------------------------------------

class TestRunAddressRoute:
    def test_run_address_returns_200(self, client, monkeypatch):
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run-address", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
        })
        assert resp.status_code == 200
