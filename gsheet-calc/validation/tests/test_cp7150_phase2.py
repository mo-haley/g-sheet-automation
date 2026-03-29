"""Tests for CP-7150 audit Phase 2: Height district zone-class-specific limits.

Verifies that height_districts.json now encodes zone-class-specific height/story
limits per Table 2 of CP-7150 (Jan 2026), and that HeightRule uses them.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.settings import DATA_DIR
from rules.deterministic.height import HeightRule
from models.site import Site
from models.project import Project


def _load_hd():
    return json.loads((DATA_DIR / "height_districts.json").read_text())


def _hs(hd: str, zone_class: str) -> dict:
    """Get height_story entry for a zone class in a height district."""
    data = _load_hd()
    return data["height_districts"][hd]["height_story_by_zone_class"].get(zone_class, {})


def _run_height(zone: str, hd: str) -> tuple:
    """Run HeightRule and return (height_limit, story_limit)."""
    site = Site(
        address="test",
        zone=zone,
        height_district=hd,
    )
    rule = HeightRule()
    results, issues = rule.evaluate(site, Project(project_name="test"))
    height = next((r.value for r in results if r.name == "height_limit_ft"), "MISSING")
    stories = next((r.value for r in results if r.name == "story_limit"), "MISSING")
    return height, stories


# ── Data-level tests: height_story_by_zone_class exists and is correct ────


def test_rd_r3_in_1l():
    """RD/R3 in 1L: 45 ft, 3 stories per Table 2."""
    entry = _hs("1L", "rd_r3")
    assert entry["height_ft"] == 45
    assert entry["stories"] == 3


def test_r4_r5_in_1l():
    """R4/R5 in 1L: 75 ft, 6 stories per Table 2."""
    entry = _hs("1L", "r4_r5")
    assert entry["height_ft"] == 75
    assert entry["stories"] == 6


def test_ras3_in_1l():
    """RAS3 in 1L: 50 ft, no story limit per Table 2."""
    entry = _hs("1L", "ras3")
    assert entry["height_ft"] == 50
    assert entry["stories"] is None


def test_c_m_in_1vl():
    """C/M in 1VL: 45 ft, 3 stories per Table 2 (not universal 36 ft)."""
    entry = _hs("1VL", "c_m")
    assert entry["height_ft"] == 45
    assert entry["stories"] == 3


def test_r4_r5_in_1vl():
    """R4/R5 in 1VL: 45 ft, 3 stories."""
    entry = _hs("1VL", "r4_r5")
    assert entry["height_ft"] == 45
    assert entry["stories"] == 3


def test_r2_in_1vl():
    """R2 in 1VL: 30 ft, 2 stories."""
    entry = _hs("1VL", "r2")
    assert entry["height_ft"] == 30
    assert entry["stories"] == 2


def test_r2_capped_at_33_in_hd2():
    """R2 is always capped at 33 ft regardless of HD."""
    entry = _hs("2", "r2")
    assert entry["height_ft"] == 33


def test_r2_capped_at_33_in_hd3():
    entry = _hs("3", "r2")
    assert entry["height_ft"] == 33


def test_r2_capped_at_33_in_hd4():
    entry = _hs("4", "r2")
    assert entry["height_ft"] == 33


def test_rd_r3_in_hd2():
    """RD/R3 in HD2: 75 ft, 6 stories."""
    entry = _hs("2", "rd_r3")
    assert entry["height_ft"] == 75
    assert entry["stories"] == 6


def test_r4_r5_in_hd1_no_limit():
    """R4/R5 in HD1: no height or story limit from HD."""
    entry = _hs("1", "r4_r5")
    assert entry["height_ft"] is None
    assert entry["stories"] is None


def test_1ss_empty():
    """1SS has no zone-specific entries (governed by specific plan)."""
    data = _load_hd()
    hs = data["height_districts"]["1SS"]["height_story_by_zone_class"]
    assert hs == {}


# ── HeightRule integration tests: correct values used ────────────────────


def test_height_rule_r3_1l():
    """HeightRule: R3 in 1L should return 45 ft / 3 stories."""
    h, s = _run_height("R3", "1L")
    assert h == 45
    assert s == 3


def test_height_rule_r4_1l():
    """HeightRule: R4 in 1L should return 75 ft / 6 stories."""
    h, s = _run_height("R4", "1L")
    assert h == 75
    assert s == 6


def test_height_rule_c2_1vl():
    """HeightRule: C2 in 1VL should return 45 ft / 3 stories (not old 36/3)."""
    h, s = _run_height("C2", "1VL")
    assert h == 45
    assert s == 3


def test_height_rule_r2_hd2():
    """HeightRule: R2 in HD2 should return 33 ft (R2 is always capped)."""
    h, s = _run_height("R2", "2")
    assert h == 33


def test_height_rule_r4_hd1():
    """HeightRule: R4 in HD1 should return no limit (None)."""
    h, s = _run_height("R4", "1")
    assert h is None
    assert s is None


def test_height_rule_ras3_1l():
    """HeightRule: RAS3 in 1L should return 50 ft / no story limit."""
    h, s = _run_height("RAS3", "1L")
    assert h == 50
    assert s is None


def test_zone_class_map_has_cr():
    """CR should be in zone_class_map (added for completeness)."""
    data = _load_hd()
    assert data["zone_class_map"].get("CR") == "c_m"
