"""Tests for multi-parcel APN deduplication."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ingest.multi_parcel import resolve_multi_parcel_site


def _make_zimas_mock(features_per_apn: dict[str, list] | None = None):
    """Build a mock ZIMASClient that returns parcel features by BPP."""
    if features_per_apn is None:
        features_per_apn = {}

    mock = MagicMock()
    mock.pull_timestamp = "2026-01-01T00:00:00Z"

    def _query(bpp):
        return features_per_apn.get(bpp, [])

    mock.query_parcel_by_bpp.side_effect = _query

    # identify_at_stateplane returns a minimal response
    mock.identify_at_stateplane.return_value = {
        "results": [
            {"layerId": 1102, "attributes": {"ZONE_CMPLT": "C2-1VL"}},
            {"layerId": 105, "attributes": {"BPP": "1234567890", "Shape_Area": 5000.0}},
        ]
    }
    return mock


def _make_feature(bpp: str = "1234567890", area: float = 5000.0):
    return {
        "attributes": {"BPP": bpp, "Shape_Area": area, "MODLOT": "1"},
        "geometry": {"rings": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]},
    }


class TestAPNDedup:
    def test_duplicate_apns_are_removed(self):
        """Identical APNs submitted twice should be collapsed to one query."""
        zimas = _make_zimas_mock({
            "4305014026": [_make_feature("4305014026")],
        })

        site, issues = resolve_multi_parcel_site(
            ["4305014026", "4305014026"], "123 Test St", zimas
        )

        # Should only query ZIMAS once for this APN
        assert zimas.query_parcel_by_bpp.call_count == 1

        # Should have a dedup warning
        dup_issues = [i for i in issues if i.id == "MULTI-APN-DUP"]
        assert len(dup_issues) == 1
        assert "1 duplicate" in dup_issues[0].title

    def test_three_identical_apns_dedup_to_one(self):
        zimas = _make_zimas_mock({
            "1234567890": [_make_feature("1234567890")],
        })

        site, issues = resolve_multi_parcel_site(
            ["1234567890", "1234567890", "1234567890"], "123 Test St", zimas
        )

        assert zimas.query_parcel_by_bpp.call_count == 1
        dup_issues = [i for i in issues if i.id == "MULTI-APN-DUP"]
        assert len(dup_issues) == 1
        assert "2 duplicate" in dup_issues[0].title

    def test_distinct_apns_not_deduped(self):
        zimas = _make_zimas_mock({
            "1111111111": [_make_feature("1111111111")],
            "2222222222": [_make_feature("2222222222")],
        })

        site, issues = resolve_multi_parcel_site(
            ["1111111111", "2222222222"], "123 Test St", zimas
        )

        assert zimas.query_parcel_by_bpp.call_count == 2
        dup_issues = [i for i in issues if i.id == "MULTI-APN-DUP"]
        assert len(dup_issues) == 0

    def test_whitespace_variants_are_deduped(self):
        """' 4305014026 ' and '4305014026' are the same APN."""
        zimas = _make_zimas_mock({
            "4305014026": [_make_feature("4305014026")],
        })

        site, issues = resolve_multi_parcel_site(
            ["4305014026", " 4305014026 "], "123 Test St", zimas
        )

        assert zimas.query_parcel_by_bpp.call_count == 1
        dup_issues = [i for i in issues if i.id == "MULTI-APN-DUP"]
        assert len(dup_issues) == 1

    def test_blank_apns_are_skipped(self):
        zimas = _make_zimas_mock({
            "1234567890": [_make_feature("1234567890")],
        })

        site, issues = resolve_multi_parcel_site(
            ["1234567890", "", "  "], "123 Test St", zimas
        )

        assert zimas.query_parcel_by_bpp.call_count == 1
        # Blanks don't count as duplicates
        dup_issues = [i for i in issues if i.id == "MULTI-APN-DUP"]
        assert len(dup_issues) == 0
