"""Tests for ZIMAS identify tolerance, retry, cache validation, and status surfacing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from config.settings import ZIMAS_LAYERS
from ingest.zimas import ZIMASClient, IdentifyStatus, _build_identify_params


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ZONING_LAYER = ZIMAS_LAYERS["zoning"]              # Chapter 1 (1102)
ZONING_CH1A_LAYER = ZIMAS_LAYERS["zoning_ch1a"]    # Chapter 1A (1101)
PARCEL_LAYER = ZIMAS_LAYERS["parcels"]              # Landbase (105)


def _make_response(*layer_ids: int) -> dict:
    """Build a minimal identify response with results for given layer IDs."""
    return {
        "results": [
            {"layerId": lid, "attributes": {"OBJECTID": i}}
            for i, lid in enumerate(layer_ids)
        ]
    }


def _make_full_response() -> dict:
    """Response containing both critical layers plus extras."""
    return _make_response(ZONING_LAYER, PARCEL_LAYER, 103, 1400)


def _make_partial_response() -> dict:
    """Response missing parcel layer (simulates tight-tolerance miss)."""
    return _make_response(ZONING_LAYER, 103, 1400)


def _make_empty_response() -> dict:
    return {"results": []}


def _mock_resp(data: dict) -> MagicMock:
    """Build a mock requests.Response returning the given JSON."""
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# _build_identify_params
# ---------------------------------------------------------------------------

class TestBuildIdentifyParams:
    def test_default_tolerance_is_15(self):
        params = _build_identify_params(100.0, 200.0, [1, 2])
        assert params["tolerance"] == 15

    def test_custom_tolerance(self):
        params = _build_identify_params(100.0, 200.0, [1, 2], tolerance=30)
        assert params["tolerance"] == 30

    def test_geometry_format(self):
        params = _build_identify_params(123.5, 456.7, [1])
        assert params["geometry"] == "123.5,456.7"

    def test_layers_format(self):
        params = _build_identify_params(0, 0, [105, 1102])
        assert params["layers"] == "all:105,1102"


# ---------------------------------------------------------------------------
# Cache validation — stale cache missing critical layers triggers re-fetch
# ---------------------------------------------------------------------------

class TestCacheValidation:
    def test_cache_with_critical_layers_is_valid(self):
        client = ZIMASClient(cache=MagicMock())
        assert client._has_critical_layers(_make_full_response()) is True

    def test_chapter_1a_zoning_is_valid(self):
        """Chapter 1A zoning (1101) satisfies the zoning requirement."""
        client = ZIMASClient(cache=MagicMock())
        data = _make_response(ZONING_CH1A_LAYER, PARCEL_LAYER, 103)
        assert client._has_critical_layers(data) is True

    def test_cache_missing_parcel_is_invalid(self):
        client = ZIMASClient(cache=MagicMock())
        assert client._has_critical_layers(_make_partial_response()) is False

    def test_cache_missing_zoning_is_invalid(self):
        client = ZIMASClient(cache=MagicMock())
        data = _make_response(PARCEL_LAYER, 103)
        assert client._has_critical_layers(data) is False

    def test_empty_response_is_invalid(self):
        client = ZIMASClient(cache=MagicMock())
        assert client._has_critical_layers(_make_empty_response()) is False

    @patch("ingest.zimas.requests.get")
    def test_stale_cache_triggers_refetch(self, mock_get):
        """Cached response missing parcels should be ignored and re-fetched."""
        cache = MagicMock()
        cache.get.return_value = {
            "pull_timestamp": "2026-01-01T00:00:00+00:00",
            "data": _make_partial_response(),
        }
        mock_get.return_value = _mock_resp(_make_full_response())

        client = ZIMASClient(cache=cache)
        result = client.identify(34.0, -118.0)

        assert mock_get.called
        assert client._has_critical_layers(result)


# ---------------------------------------------------------------------------
# Two-pass tolerance retry
# ---------------------------------------------------------------------------

class TestToleranceRetry:
    @patch("ingest.zimas.requests.get")
    def test_standard_tolerance_sufficient_no_retry(self, mock_get):
        """When standard tolerance returns critical layers, no retry happens."""
        cache = MagicMock()
        cache.get.return_value = None
        mock_get.return_value = _mock_resp(_make_full_response())

        client = ZIMASClient(cache=cache)
        result = client.identify(34.0, -118.0)

        assert mock_get.call_count == 1
        assert client._has_critical_layers(result)

    @patch("ingest.zimas.requests.get")
    def test_wide_tolerance_retry_on_missing_parcel(self, mock_get):
        """Missing parcel at standard tolerance triggers wide retry."""
        cache = MagicMock()
        cache.get.return_value = None
        mock_get.side_effect = [
            _mock_resp(_make_partial_response()),
            _mock_resp(_make_full_response()),
        ]

        client = ZIMASClient(cache=cache)
        result = client.identify(34.0, -118.0)

        assert mock_get.call_count == 2
        assert client._has_critical_layers(result)

        # Verify tolerance values used
        calls = mock_get.call_args_list
        assert calls[0][1]["params"]["tolerance"] == 15
        assert calls[1][1]["params"]["tolerance"] == 30

    @patch("ingest.zimas.requests.get")
    def test_both_passes_fail_returns_best_available(self, mock_get):
        """When both passes miss critical layers, returns the richer response."""
        cache = MagicMock()
        cache.get.return_value = None
        mock_get.side_effect = [
            _mock_resp(_make_empty_response()),
            _mock_resp(_make_response(ZONING_LAYER, 103)),
        ]

        client = ZIMASClient(cache=cache)
        result = client.identify(34.0, -118.0)

        assert mock_get.call_count == 2
        assert len(result["results"]) == 2


# ---------------------------------------------------------------------------
# identify_status surfacing
# ---------------------------------------------------------------------------

class TestIdentifyStatus:
    @patch("ingest.zimas.requests.get")
    def test_status_clean_when_standard_succeeds(self, mock_get):
        """No flags set when standard tolerance is sufficient."""
        cache = MagicMock()
        cache.get.return_value = None
        mock_get.return_value = _mock_resp(_make_full_response())

        client = ZIMASClient(cache=cache)
        client.identify(34.0, -118.0)

        assert client.identify_status.used_wide_tolerance is False
        assert client.identify_status.critical_layers_resolved is True

    @patch("ingest.zimas.requests.get")
    def test_status_wide_tolerance_when_retry_succeeds(self, mock_get):
        """used_wide_tolerance=True, critical_layers_resolved=True when retry works."""
        cache = MagicMock()
        cache.get.return_value = None
        mock_get.side_effect = [
            _mock_resp(_make_partial_response()),
            _mock_resp(_make_full_response()),
        ]

        client = ZIMASClient(cache=cache)
        client.identify(34.0, -118.0)

        assert client.identify_status.used_wide_tolerance is True
        assert client.identify_status.critical_layers_resolved is True

    @patch("ingest.zimas.requests.get")
    def test_status_unresolved_when_both_fail(self, mock_get):
        """Both flags set when neither tolerance returns critical layers."""
        cache = MagicMock()
        cache.get.return_value = None
        mock_get.side_effect = [
            _mock_resp(_make_empty_response()),
            _mock_resp(_make_response(103)),
        ]

        client = ZIMASClient(cache=cache)
        client.identify(34.0, -118.0)

        assert client.identify_status.used_wide_tolerance is True
        assert client.identify_status.critical_layers_resolved is False

    @patch("ingest.zimas.requests.get")
    def test_status_reset_on_each_identify_call(self, mock_get):
        """identify_status is reset at the start of each identify() call."""
        cache = MagicMock()
        cache.get.return_value = None

        # First call: fails both passes
        mock_get.side_effect = [
            _mock_resp(_make_empty_response()),
            _mock_resp(_make_response(103)),
        ]

        client = ZIMASClient(cache=cache)
        client.identify(34.0, -118.0)
        assert client.identify_status.critical_layers_resolved is False

        # Second call: succeeds on first pass
        cache.get.return_value = None
        mock_get.side_effect = [_mock_resp(_make_full_response())]
        client.identify(34.1, -118.1)
        assert client.identify_status.used_wide_tolerance is False
        assert client.identify_status.critical_layers_resolved is True


# ---------------------------------------------------------------------------
# Downstream: parcel extraction with missing data produces explicit issue
# ---------------------------------------------------------------------------

class TestParcelExtractionFailureReporting:
    def test_no_parcel_layer_produces_blocking_issue(self):
        from ingest.parcel import extract_parcel_data

        data = _make_partial_response()  # Has zoning but no parcel
        attrs, sources, issues = extract_parcel_data(data)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.id == "INGEST-PARCEL-001"
        assert issue.severity == "high"
        assert issue.blocking is True
        assert "lot_area_sf" in issue.affected_fields

    def test_empty_response_produces_blocking_issue(self):
        from ingest.parcel import extract_parcel_data

        attrs, sources, issues = extract_parcel_data(_make_empty_response())

        assert len(issues) == 1
        assert issues[0].blocking is True


# ---------------------------------------------------------------------------
# Startup import sanity
# ---------------------------------------------------------------------------

class TestStartupImports:
    def test_web_app_importable(self):
        """The gunicorn target module is importable."""
        from web.app import app
        assert app is not None
        assert app.name == "web.app"

    def test_app_orchestrator_importable(self):
        from analysis.app_orchestrator import run_app
        assert callable(run_app)

    def test_result_common_importable(self):
        from models.result_common import AppResult, ModuleResult
        assert AppResult is not None
        assert ModuleResult is not None
