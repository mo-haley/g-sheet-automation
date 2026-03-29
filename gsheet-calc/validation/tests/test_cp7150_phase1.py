"""Tests for CP-7150 audit Phase 1 fixes.

Covers:
  A. RD5/RD6 setback corrections
  B. RD3-RD6 min lot area corrections
  C. RAS3/RAS4 front setback correction
  D. Side yard 16 ft cap on story increments
  E. R4/R5 rear yard story increment (+1 ft per story above 3rd, max 20 ft)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.settings import DATA_DIR
from setback.setback_yard_family import get_yard_family_rules
from setback.setback_authority import resolve_setback_authority
from setback.setback_edge_calc import (
    _evaluate_side_formula,
    _evaluate_rear_formula,
)
from setback.models import YardFormula


# ─── Helpers ────────────────────────────────────────────────────────────────

def _load_zone_tables():
    return json.loads((DATA_DIR / "zone_tables.json").read_text())


def _zone(name: str) -> dict:
    return _load_zone_tables()["zones"][name]


def _resolve(zone: str):
    """Quick authority + yard family resolution for a zone."""
    auth = resolve_setback_authority(
        raw_zone=None, base_zone=zone, height_district="1",
        specific_plan=None, cpio=None, d_limitation=None, q_condition=None,
        chapter_1a_applicable=False, small_lot_subdivision=False,
    )
    return get_yard_family_rules(auth)


# ─── A. RD5/RD6 setbacks ───────────────────────────────────────────────────


def test_rd5_setbacks():
    """RD5 front=20, side=10, rear=25 per Table 1b."""
    z = _zone("RD5")
    assert z["setbacks"]["front_ft"] == 20
    assert z["setbacks"]["side_ft"] == 10
    assert z["setbacks"]["rear_ft"] == 25


def test_rd6_setbacks():
    """RD6 front=20, side=10, rear=25 per Table 1b."""
    z = _zone("RD6")
    assert z["setbacks"]["front_ft"] == 20
    assert z["setbacks"]["side_ft"] == 10
    assert z["setbacks"]["rear_ft"] == 25


def test_rd1_5_setbacks_unchanged():
    """RD1.5 setbacks should NOT have changed: front=15, side=5, rear=15."""
    z = _zone("RD1.5")
    assert z["setbacks"]["front_ft"] == 15
    assert z["setbacks"]["side_ft"] == 5
    assert z["setbacks"]["rear_ft"] == 15


# ─── B. RD3-RD6 minimum lot areas ──────────────────────────────────────────


def test_rd3_min_lot_area():
    assert _zone("RD3")["min_lot_area_sf"] == 6000


def test_rd4_min_lot_area():
    assert _zone("RD4")["min_lot_area_sf"] == 8000


def test_rd5_min_lot_area():
    assert _zone("RD5")["min_lot_area_sf"] == 10000


def test_rd6_min_lot_area():
    assert _zone("RD6")["min_lot_area_sf"] == 12000


def test_rd1_5_min_lot_area_unchanged():
    """RD1.5 should remain 5000."""
    assert _zone("RD1.5")["min_lot_area_sf"] == 5000


def test_rd2_min_lot_area_unchanged():
    """RD2 should remain 5000."""
    assert _zone("RD2")["min_lot_area_sf"] == 5000


# ─── C. RAS3/RAS4 front setback ────────────────────────────────────────────


def test_ras3_front_setback():
    """RAS3 front = 5 ft per Table 1b (not 15 ft)."""
    assert _zone("RAS3")["setbacks"]["front_ft"] == 5


def test_ras4_front_setback():
    """RAS4 front = 5 ft per Table 1b (not 15 ft)."""
    assert _zone("RAS4")["setbacks"]["front_ft"] == 5


def test_ras3_side_unchanged():
    """RAS3 side should remain 5 ft (residential portion)."""
    assert _zone("RAS3")["setbacks"]["side_ft"] == 5


# ─── D. Side yard 16 ft cap ────────────────────────────────────────────────


def test_r3_side_formula_has_cap():
    """R3 side formula should have story_increment_max_ft = 16."""
    result = _resolve("R3")
    assert result.side_formula is not None
    assert result.side_formula.story_increment_max_ft == 16.0


def test_r4_side_formula_has_cap():
    """R4 side formula should have story_increment_max_ft = 16."""
    result = _resolve("R4")
    assert result.side_formula is not None
    assert result.side_formula.story_increment_max_ft == 16.0


def test_side_cap_applied_at_14_stories():
    """14 stories: base 5 + 12 increments = 17, but capped at 16 ft."""
    formula = YardFormula(
        yard_type="side",
        base_ft=5.0,
        story_increment_ft=1.0,
        story_threshold=2,
        story_increment_max_ft=16.0,
    )
    value, steps, _ = _evaluate_side_formula(formula, lot_width=None, number_of_stories=14)
    assert value == 16.0


def test_side_cap_not_reached_at_5_stories():
    """5 stories: base 5 + 3 increments = 8, under the 16 ft cap."""
    formula = YardFormula(
        yard_type="side",
        base_ft=5.0,
        story_increment_ft=1.0,
        story_threshold=2,
        story_increment_max_ft=16.0,
    )
    value, steps, _ = _evaluate_side_formula(formula, lot_width=None, number_of_stories=5)
    assert value == 8.0


def test_side_cap_exactly_at_boundary():
    """13 stories: base 5 + 11 increments = 16, exactly at cap."""
    formula = YardFormula(
        yard_type="side",
        base_ft=5.0,
        story_increment_ft=1.0,
        story_threshold=2,
        story_increment_max_ft=16.0,
    )
    value, steps, _ = _evaluate_side_formula(formula, lot_width=None, number_of_stories=13)
    assert value == 16.0


def test_side_no_cap_when_not_set():
    """Without cap, 14 stories should give 5 + 12 = 17 ft."""
    formula = YardFormula(
        yard_type="side",
        base_ft=5.0,
        story_increment_ft=1.0,
        story_threshold=2,
        story_increment_max_ft=None,
    )
    value, steps, _ = _evaluate_side_formula(formula, lot_width=None, number_of_stories=14)
    assert value == 17.0


# ─── E. R4/R5 rear yard story increment ────────────────────────────────────


def test_r4_rear_formula_has_story_increment():
    """R4 rear formula should have story increment: +1 ft above 3rd, max 20 ft."""
    result = _resolve("R4")
    assert result.rear_formula is not None
    assert result.rear_formula.story_increment_ft == 1.0
    assert result.rear_formula.story_threshold == 3
    assert result.rear_formula.story_increment_max_ft == 20.0


def test_r5_rear_formula_has_story_increment():
    """R5 rear formula should have story increment: +1 ft above 3rd, max 20 ft."""
    result = _resolve("R5")
    assert result.rear_formula is not None
    assert result.rear_formula.story_increment_ft == 1.0
    assert result.rear_formula.story_threshold == 3
    assert result.rear_formula.story_increment_max_ft == 20.0


def test_r3_rear_no_story_increment():
    """R3 rear yard has no story increment per Table 1b."""
    result = _resolve("R3")
    assert result.rear_formula is not None
    assert result.rear_formula.story_increment_ft is None


def test_rear_increment_at_5_stories():
    """R4/R5 rear: 5 stories → 15 + 2 = 17 ft (2 stories above 3rd)."""
    formula = YardFormula(
        yard_type="rear",
        base_ft=15.0,
        story_increment_ft=1.0,
        story_threshold=3,
        story_increment_max_ft=20.0,
    )
    value, steps, _ = _evaluate_rear_formula(formula, is_alley_edge=False, number_of_stories=5)
    assert value == 17.0


def test_rear_increment_capped_at_20():
    """R4/R5 rear: 10 stories → 15 + 7 = 22, capped at 20 ft."""
    formula = YardFormula(
        yard_type="rear",
        base_ft=15.0,
        story_increment_ft=1.0,
        story_threshold=3,
        story_increment_max_ft=20.0,
    )
    value, steps, _ = _evaluate_rear_formula(formula, is_alley_edge=False, number_of_stories=10)
    assert value == 20.0


def test_rear_no_increment_at_3_stories():
    """R4/R5 rear: 3 stories (at threshold) → 15 ft, no increment."""
    formula = YardFormula(
        yard_type="rear",
        base_ft=15.0,
        story_increment_ft=1.0,
        story_threshold=3,
        story_increment_max_ft=20.0,
    )
    value, steps, _ = _evaluate_rear_formula(formula, is_alley_edge=False, number_of_stories=3)
    assert value == 15.0


def test_rear_increment_provisional_when_stories_unknown():
    """When stories not provided, rear formula should be provisional."""
    formula = YardFormula(
        yard_type="rear",
        base_ft=15.0,
        story_increment_ft=1.0,
        story_threshold=3,
        story_increment_max_ft=20.0,
    )
    value, steps, is_provisional = _evaluate_rear_formula(
        formula, is_alley_edge=False, number_of_stories=None
    )
    assert value == 15.0
    assert is_provisional is True
