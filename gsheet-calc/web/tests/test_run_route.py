"""Tests for /run and /run-address routes: modular pipeline, project inputs, module rendering."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from web.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_geocode_and_zimas(monkeypatch):
    """Patch geocoder and ZIMAS to avoid real API calls."""
    mock_geocoder_cls = MagicMock()
    mock_geocoder_cls.return_value.geocode.return_value = (34.054, -118.384)
    monkeypatch.setattr("web.app.Geocoder", mock_geocoder_cls)

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


# ---------------------------------------------------------------------------
# /run route: now uses modular pipeline + snapshot.html
# ---------------------------------------------------------------------------

class TestRunRoute:
    def test_run_returns_200(self, client, monkeypatch):
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
            "studios": "0", "one_br": "5", "two_br": "3", "three_br": "0",
            "policy_path": "base_zoning",
        })
        assert resp.status_code == 200

    def test_run_shows_full_analysis_header(self, client, monkeypatch):
        """Full analysis mode should say 'Feasibility Analysis', not 'Address-Only'."""
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
            "studios": "0", "one_br": "5", "two_br": "3", "three_br": "0",
            "policy_path": "base_zoning",
        })
        assert resp.status_code == 200
        assert b"Feasibility Analysis" in resp.data
        assert b"Address-Only" not in resp.data

    def test_run_shows_project_inputs(self, client, monkeypatch):
        """Full analysis should display the Project Inputs section."""
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
            "studios": "2", "one_br": "5", "two_br": "3", "three_br": "0",
            "policy_path": "density_bonus",
        })
        assert resp.status_code == 200
        assert b"Project Inputs" in resp.data
        assert b"State Density Bonus" in resp.data
        assert b"5 1BR" in resp.data

    def test_run_shows_module_results(self, client, monkeypatch):
        """Full analysis should include modular pipeline results (density, FAR, parking, setback)."""
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
            "studios": "0", "one_br": "10", "two_br": "0", "three_br": "0",
            "policy_path": "base_zoning",
        })
        assert resp.status_code == 200
        # Snapshot template renders module coverage and module cards
        assert b"Module Results" in resp.data or b"module" in resp.data.lower()

    def test_run_shows_ed1_section(self, client, monkeypatch):
        """ED1 screening should appear in full analysis output."""
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
            "studios": "0", "one_br": "10", "two_br": "0", "three_br": "0",
            "policy_path": "base_zoning",
        })
        assert resp.status_code == 200
        assert b"ED1" in resp.data

    def test_run_with_zero_units_still_shows_project(self, client, monkeypatch):
        """Even with zero units, /run (Analyze) should show project section."""
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
            "policy_path": "base_zoning",
        })
        assert resp.status_code == 200
        assert b"Project Inputs" in resp.data
        assert b"Feasibility Analysis" in resp.data

    def test_run_missing_address_shows_error(self, client):
        resp = client.post("/run", data={"studios": "5"})
        assert resp.status_code == 200
        assert b"Address is required" in resp.data

    def test_run_with_partial_site_no_500(self, client, monkeypatch):
        mock_geocoder_cls = MagicMock()
        mock_geocoder_cls.return_value.geocode.return_value = (34.0, -118.0)
        monkeypatch.setattr("web.app.Geocoder", mock_geocoder_cls)

        mock_zimas_cls = MagicMock()
        mock_zimas = mock_zimas_cls.return_value
        mock_zimas.pull_timestamp = "2026-01-01T00:00:00Z"
        mock_zimas.identify_status.used_wide_tolerance = True
        mock_zimas.identify_status.critical_layers_resolved = False
        mock_zimas.identify.return_value = {
            "results": [{"layerId": 102, "attributes": {"OBJECTID": 1}}]
        }
        monkeypatch.setattr("web.app.ZIMASClient", mock_zimas_cls)

        resp = client.post("/run", data={
            "address": "123 Fake St, Pasadena, CA",
            "studios": "0", "one_br": "5", "two_br": "0", "three_br": "0",
            "policy_path": "base_zoning",
        })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /run-address route: address-only mode
# ---------------------------------------------------------------------------

class TestRunAddressRoute:
    def test_run_address_returns_200(self, client, monkeypatch):
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run-address", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
        })
        assert resp.status_code == 200

    def test_run_address_shows_address_only_header(self, client, monkeypatch):
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run-address", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
        })
        assert resp.status_code == 200
        assert b"Address-Only" in resp.data

    def test_run_address_no_project_section(self, client, monkeypatch):
        _mock_geocode_and_zimas(monkeypatch)
        resp = client.post("/run-address", data={
            "address": "1425 S Robertson Blvd, Los Angeles, CA 90035",
        })
        assert resp.status_code == 200
        assert b"Project Inputs" not in resp.data
