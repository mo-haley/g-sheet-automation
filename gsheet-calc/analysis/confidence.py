"""Confidence aggregation across calculation results."""

from models.result import CalcResult


CONFIDENCE_ORDER = {"high": 2, "medium": 1, "low": 0}


def aggregate_confidence(results: list[CalcResult]) -> str:
    """Return the lowest confidence level across all results."""
    if not results:
        return "low"
    min_conf = min(CONFIDENCE_ORDER.get(r.confidence, 0) for r in results)
    for label, val in CONFIDENCE_ORDER.items():
        if val == min_conf:
            return label
    return "low"
