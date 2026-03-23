"""Setback edge classifier (Step 3).

Classifies each lot-line edge as front / side / rear / side_street_side.
Returns per-edge ClassifiedEdge objects with confidence level and reason.

═══════════════════════════════════════════════
CLASSIFICATION POSTURE
═══════════════════════════════════════════════

Conservative by design. When an edge role cannot be determined confidently
from the supplied edge_type and lot_type alone, the function returns
confidence="manual_confirm" with an explicit reason rather than guessing.

Callers can improve classification confidence by:
  - Using edge_type="interior_rear" to designate the rear lot line on lots
    without alley access. Without this designation, all generic interior
    edges on non-alley lots return manual_confirm.
  - Ensuring lot_type matches the actual lot configuration. Mismatched
    lot_type (e.g., "interior" for a lot with two street edges) will
    produce manual_confirm outputs with a note to re-check lot_type.
  - Setting lot_geometry_regular=True only for genuinely rectangular/regular
    lots. Irregular geometry degrades all classifications to manual_confirm.

═══════════════════════════════════════════════
EDGE_TYPE VALUES
═══════════════════════════════════════════════

  "street"        — abuts a named or unnamed public street
  "alley"         — abuts a public alley (classified as rear without exception)
  "interior"      — interior lot line; rear vs. side is NOT designated by caller
  "interior_rear" — interior lot line explicitly designated as the rear lot
                    line; classified as rear (confirmed) when exactly one
                    interior_rear edge is present

═══════════════════════════════════════════════
CLASSIFICATION RULES BY LOT TYPE
═══════════════════════════════════════════════

interior:
    Single street edge → front (confirmed)
    Single alley edge  → rear (confirmed)
    interior_rear edge → rear (confirmed, if only one)
    Generic interior   → side (confirmed) IF rear is assigned; else manual_confirm

corner:
    Two street edges   → first = front (manual_confirm),
                         second = side_street_side (manual_confirm)
                         [order is input-list order; assignment tentative]
    Alley / interior_rear → rear (confirmed)
    Generic interior   → side (confirmed if rear assigned; else manual_confirm)

through:
    Two street edges   → both = front (manual_confirm)
                         [through lots have no canonical rear lot line]
    All other edges    → manual_confirm with reason

flag:
    All edges          → manual_confirm (access strip complicates all roles)

irregular geometry (lot_geometry_regular=False):
    Classification runs normally, then ALL edges are degraded to
    manual_confirm. The tentative classification is preserved in the reason
    string so reviewers have a starting point.
"""

from __future__ import annotations

from setback.models import ClassifiedEdge, EdgeInput


_VALID_LOT_TYPES: frozenset[str] = frozenset({"interior", "corner", "through", "flag"})
_KNOWN_EDGE_TYPES: frozenset[str] = frozenset({"street", "alley", "interior", "interior_rear"})


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _label(edge: EdgeInput) -> str:
    """Human-readable edge label, including street name when available."""
    if edge.street_name:
        return f"'{edge.edge_id}' ({edge.street_name})"
    return f"'{edge.edge_id}'"


def _partition(edges: list[EdgeInput]) -> tuple[
    list[EdgeInput],  # street
    list[EdgeInput],  # alley
    list[EdgeInput],  # interior_rear
    list[EdgeInput],  # interior (generic)
    list[EdgeInput],  # unknown
]:
    """Split edges into five buckets by edge_type."""
    street, alley, int_rear, interior, unknown = [], [], [], [], []
    for e in edges:
        if e.edge_type == "street":
            street.append(e)
        elif e.edge_type == "alley":
            alley.append(e)
        elif e.edge_type == "interior_rear":
            int_rear.append(e)
        elif e.edge_type == "interior":
            interior.append(e)
        else:
            unknown.append(e)
    return street, alley, int_rear, interior, unknown


def _unknown_edge(e: EdgeInput) -> ClassifiedEdge:
    return ClassifiedEdge(
        edge_id=e.edge_id,
        classification="side",      # safest fallback
        confidence="manual_confirm",
        manual_confirm_reason=(
            f"Unrecognized edge_type '{e.edge_type}' on edge {_label(e)}. "
            f"Valid values: {', '.join(sorted(_KNOWN_EDGE_TYPES))}. "
            "Classification defaulted to 'side' — confirm manually."
        ),
    )


def _degrade_all_to_manual_confirm(
    classified: list[ClassifiedEdge],
    reason: str,
) -> list[ClassifiedEdge]:
    """Override all confidence levels to manual_confirm, prepending reason.

    Tentative classification is preserved in the reason string so reviewers
    have a starting point. Existing manual_confirm reasons are appended.
    """
    result: list[ClassifiedEdge] = []
    for c in classified:
        if c.confidence == "confirmed":
            combined = f"{reason} — tentative classification: {c.classification}."
        else:
            # Already manual_confirm; prepend new reason
            prior = f" Prior note: {c.manual_confirm_reason}" if c.manual_confirm_reason else ""
            combined = f"{reason}{prior}"
        result.append(ClassifiedEdge(
            edge_id=c.edge_id,
            classification=c.classification,
            confidence="manual_confirm",
            manual_confirm_reason=combined,
        ))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Rear-assignment helpers shared across lot types
# ─────────────────────────────────────────────────────────────────────────────

def _assign_alley_rear(
    alley_edges: list[EdgeInput],
    result: list[ClassifiedEdge],
) -> bool:
    """Classify alley edges as rear; append to result. Returns True if assigned."""
    if not alley_edges:
        return False
    if len(alley_edges) == 1:
        result.append(ClassifiedEdge(
            edge_id=alley_edges[0].edge_id,
            classification="rear",
            confidence="confirmed",
        ))
    else:
        for e in alley_edges:
            result.append(ClassifiedEdge(
                edge_id=e.edge_id,
                classification="rear",
                confidence="manual_confirm",
                manual_confirm_reason=(
                    f"{len(alley_edges)} alley edges found — expected at most 1. "
                    "Rear assignment is provisional; confirm which alley edge is "
                    "the rear lot line."
                ),
            ))
    return True


def _assign_interior_rear(
    int_rear_edges: list[EdgeInput],
    result: list[ClassifiedEdge],
) -> bool:
    """Classify interior_rear edges as rear; append to result. Returns True if assigned."""
    if not int_rear_edges:
        return False
    if len(int_rear_edges) == 1:
        result.append(ClassifiedEdge(
            edge_id=int_rear_edges[0].edge_id,
            classification="rear",
            confidence="confirmed",
        ))
    else:
        for e in int_rear_edges:
            result.append(ClassifiedEdge(
                edge_id=e.edge_id,
                classification="rear",
                confidence="manual_confirm",
                manual_confirm_reason=(
                    f"{len(int_rear_edges)} edges designated 'interior_rear' — "
                    "expected at most 1. Confirm which is the rear lot line."
                ),
            ))
    return True


def _assign_generic_interior(
    generic_interior: list[EdgeInput],
    rear_assigned: bool,
    result: list[ClassifiedEdge],
    no_rear_reason: str,
) -> None:
    """Classify generic interior edges; append to result.

    When rear_assigned is True → all get "side" (confirmed).
    When False → all get manual_confirm with no_rear_reason.
    """
    for e in generic_interior:
        if rear_assigned:
            result.append(ClassifiedEdge(
                edge_id=e.edge_id,
                classification="side",
                confidence="confirmed",
            ))
        else:
            result.append(ClassifiedEdge(
                edge_id=e.edge_id,
                classification="side",          # tentative
                confidence="manual_confirm",
                manual_confirm_reason=no_rear_reason,
            ))


# ─────────────────────────────────────────────────────────────────────────────
# Per lot-type classifiers
# ─────────────────────────────────────────────────────────────────────────────

def _classify_interior(edges: list[EdgeInput]) -> list[ClassifiedEdge]:
    """Interior lot: 1 street = front; alley/interior_rear = rear; remainder = sides.

    Generic interior edges without a rear anchor (alley or interior_rear) are
    returned as manual_confirm — rear vs. side cannot be determined from
    edge_type alone. Callers should designate the rear edge as
    edge_type='interior_rear' on non-alley lots.
    """
    result: list[ClassifiedEdge] = []
    street, alley, int_rear, generic, unknown = _partition(edges)

    # ── Front ────────────────────────────────────────────────────────────────
    if len(street) == 1:
        result.append(ClassifiedEdge(
            edge_id=street[0].edge_id,
            classification="front",
            confidence="confirmed",
        ))
    elif len(street) == 0:
        # No street edge — cannot identify front; surfaces through interior fallback
        pass
    else:
        for e in street:
            result.append(ClassifiedEdge(
                edge_id=e.edge_id,
                classification="front",
                confidence="manual_confirm",
                manual_confirm_reason=(
                    f"Interior lot has {len(street)} street edges — expected 1. "
                    "Cannot auto-assign the primary front lot line. "
                    "Verify lot_type (should this be 'corner' or 'through'?)."
                ),
            ))

    # ── Rear ─────────────────────────────────────────────────────────────────
    rear_from_alley = _assign_alley_rear(alley, result)
    rear_from_int = _assign_interior_rear(int_rear, result)
    rear_assigned = rear_from_alley or rear_from_int

    # ── Sides (or unresolved interior) ───────────────────────────────────────
    no_street_suffix = (
        " No street edge found either — edge data may be incomplete."
        if len(street) == 0 else ""
    )
    _assign_generic_interior(
        generic,
        rear_assigned,
        result,
        no_rear_reason=(
            "Interior lot without alley or 'interior_rear' edge designation — "
            "cannot determine which interior edge is the rear lot line from "
            "edge_type data alone. "
            "Designate the rear edge as edge_type='interior_rear', "
            "or confirm rear vs. side classification manually."
            + no_street_suffix
        ),
    )

    for e in unknown:
        result.append(_unknown_edge(e))

    return result


def _classify_corner(edges: list[EdgeInput]) -> list[ClassifiedEdge]:
    """Corner lot: two street edges → front + side_street_side (both manual_confirm).

    The primary frontage cannot be determined from edge data alone. The first
    street edge in the input list is tentatively assigned 'front'; the second
    is tentatively assigned 'side_street_side'. Both assignments are provisional
    and must be confirmed by the reviewer.

    Input list order is the ONLY basis for tentative front vs. side_street_side
    assignment. If the caller knows which street is primary, supplying the
    primary street edge first reduces review burden without changing confidence.
    """
    result: list[ClassifiedEdge] = []
    street, alley, int_rear, generic, unknown = _partition(edges)

    # ── Two street edges: expected case ──────────────────────────────────────
    if len(street) == 2:
        e_front, e_side = street[0], street[1]
        result.append(ClassifiedEdge(
            edge_id=e_front.edge_id,
            classification="front",
            confidence="manual_confirm",
            manual_confirm_reason=(
                f"Corner lot: {_label(e_front)} tentatively assigned 'front' "
                f"(first street edge in input list). "
                f"Confirm this is the primary frontage — "
                f"{_label(e_side)} may be the front instead."
            ),
        ))
        result.append(ClassifiedEdge(
            edge_id=e_side.edge_id,
            classification="side_street_side",
            confidence="manual_confirm",
            manual_confirm_reason=(
                f"Corner lot: {_label(e_side)} tentatively assigned "
                f"'side_street_side' (second street edge in input list). "
                f"Confirm this is the secondary frontage — "
                f"it may instead be the front lot line."
            ),
        ))
    elif len(street) == 0:
        pass  # no street edges; generic interior edges will surface the issue
    else:
        # 1 or 3+ street edges — anomalous for a corner lot
        classifications = ["front", "side_street_side"] + ["front"] * max(0, len(street) - 2)
        for i, e in enumerate(street):
            result.append(ClassifiedEdge(
                edge_id=e.edge_id,
                classification=classifications[i],
                confidence="manual_confirm",
                manual_confirm_reason=(
                    f"Corner lot has {len(street)} street edge(s) — expected 2. "
                    "Front and side_street_side assignments are provisional. "
                    "Verify lot_type or correct edge data to match actual configuration."
                ),
            ))

    # ── Rear ─────────────────────────────────────────────────────────────────
    rear_from_alley = _assign_alley_rear(alley, result)
    rear_from_int = _assign_interior_rear(int_rear, result)
    rear_assigned = rear_from_alley or rear_from_int

    # ── Sides ─────────────────────────────────────────────────────────────────
    _assign_generic_interior(
        generic,
        rear_assigned,
        result,
        no_rear_reason=(
            "Corner lot without alley or 'interior_rear' designation — "
            "cannot confirm whether this interior edge is a side or the "
            "rear lot line. Designate the rear edge or confirm manually."
        ),
    )

    for e in unknown:
        result.append(_unknown_edge(e))

    return result


def _classify_through(edges: list[EdgeInput]) -> list[ClassifiedEdge]:
    """Through lot: both street edges classified as front (manual_confirm).

    Through lots have two street frontages and no conventional rear lot line.
    Both street edges require front yard treatment potentially, but the exact
    treatment of each face (prevailing setback, depth, orientation relative to
    building) must be confirmed for each street independently.

    Non-street edges on through lots are all manual_confirm — the 'side' or
    'rear' role for interior edges depends on building orientation and cannot
    be inferred from edge_type alone.
    """
    result: list[ClassifiedEdge] = []
    street, alley, int_rear, generic, unknown = _partition(edges)

    # ── Street edges: both front ──────────────────────────────────────────────
    for e in street:
        count_note = (
            "" if len(street) == 2
            else f" (through lot has {len(street)} street edges — expected 2)"
        )
        result.append(ClassifiedEdge(
            edge_id=e.edge_id,
            classification="front",
            confidence="manual_confirm",
            manual_confirm_reason=(
                f"Through lot: {_label(e)} classified as 'front'{count_note}. "
                "Through lots have two street frontages — each may require "
                "separate front yard analysis. Confirm prevailing setback "
                "requirements independently for each street face."
            ),
        ))

    # ── Alley on through lot — unusual ───────────────────────────────────────
    for e in alley:
        result.append(ClassifiedEdge(
            edge_id=e.edge_id,
            classification="rear",
            confidence="manual_confirm",
            manual_confirm_reason=(
                f"Through lot: alley edge {_label(e)} is unusual — "
                "through lots do not typically have a conventional rear lot line. "
                "Confirm whether a rear yard requirement applies to this edge."
            ),
        ))

    # ── interior_rear on through lot — unusual ────────────────────────────────
    for e in int_rear:
        result.append(ClassifiedEdge(
            edge_id=e.edge_id,
            classification="rear",
            confidence="manual_confirm",
            manual_confirm_reason=(
                f"Through lot: 'interior_rear' designation on {_label(e)} is unusual. "
                "Through lots do not have a conventional rear lot line — "
                "confirm whether a rear yard applies to this edge."
            ),
        ))

    # ── Generic interior — cannot classify confidently on through lots ────────
    for e in generic:
        result.append(ClassifiedEdge(
            edge_id=e.edge_id,
            classification="side",              # tentative
            confidence="manual_confirm",
            manual_confirm_reason=(
                f"Through lot: interior edge {_label(e)} cannot be confidently "
                "classified. Through lots have no conventional rear lot line — "
                "side vs. rear role depends on building orientation. "
                "Confirm edge role and applicable yard requirement."
            ),
        ))

    for e in unknown:
        result.append(_unknown_edge(e))

    return result


def _classify_flag(edges: list[EdgeInput]) -> list[ClassifiedEdge]:
    """Flag lot: all edges return manual_confirm.

    The access strip (pole) may not carry standard yard requirements. Without
    geometric data identifying which edges bound the buildable pad vs. the
    access strip, all edge classifications are provisional regardless of
    edge_type. Reviewers must identify and handle the pole separately.
    """
    result: list[ClassifiedEdge] = []
    for e in edges:
        # Assign a tentative classification based on edge_type to give reviewers
        # a starting point — the manual_confirm flag marks it as unconfirmed.
        if e.edge_type == "street":
            tentative = "front"
        elif e.edge_type in ("alley", "interior_rear"):
            tentative = "rear"
        else:
            tentative = "side"

        result.append(ClassifiedEdge(
            edge_id=e.edge_id,
            classification=tentative,
            confidence="manual_confirm",
            manual_confirm_reason=(
                f"Flag lot: {_label(e)} tentatively classified as '{tentative}' "
                "from edge_type only. All flag lot edge classifications are "
                "provisional — the access strip (pole) typically does not carry "
                "standard yard requirements and must be identified and excluded. "
                "Confirm each edge role and whether standard yard rules apply."
            ),
        ))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def classify_edges(
    lot_type: str,
    lot_geometry_regular: bool,
    edges: list[EdgeInput],
) -> list[ClassifiedEdge]:
    """Classify each lot-line edge as front / side / rear / side_street_side.

    Args:
        lot_type:             "interior" / "corner" / "through" / "flag"
        lot_geometry_regular: True for standard rectangular/regular lots.
                              False degrades ALL edge confidences to
                              manual_confirm after tentative classification.
        edges:                Per-edge descriptions. Order matters for corner
                              lots — the first street edge is tentatively
                              assigned 'front', the second 'side_street_side'.

    Returns:
        List of ClassifiedEdge in the same order as input edges.
        confidence="confirmed" means the role is unambiguous from the inputs.
        confidence="manual_confirm" means the role is tentative and requires
        reviewer confirmation before using in yard calculations.

    Does NOT:
        - calculate any yard dimensions (that is setback_edge_calc.py)
        - evaluate formula parametrics
        - determine adjacency zone requirements
        - pre-decide yard amounts for any edge
    """
    if not edges:
        return []

    lot_type_norm = lot_type.lower().strip()

    # ── Unknown lot type ──────────────────────────────────────────────────────
    if lot_type_norm not in _VALID_LOT_TYPES:
        return [
            ClassifiedEdge(
                edge_id=e.edge_id,
                classification="side",
                confidence="manual_confirm",
                manual_confirm_reason=(
                    f"Unrecognized lot_type '{lot_type}'. "
                    f"Valid values: {', '.join(sorted(_VALID_LOT_TYPES))}. "
                    "Cannot classify edges without a valid lot type."
                ),
            )
            for e in edges
        ]

    # ── Flag lot: always fully manual_confirm ─────────────────────────────────
    # Flag lot handling runs before the geometry check because the geometry
    # check only adds a second layer of degradation that is already captured
    # by the flag lot reason.
    if lot_type_norm == "flag":
        return _classify_flag(edges)

    # ── Standard classification ───────────────────────────────────────────────
    if lot_type_norm == "interior":
        classified = _classify_interior(edges)
    elif lot_type_norm == "corner":
        classified = _classify_corner(edges)
    else:  # "through"
        classified = _classify_through(edges)

    # ── Irregular geometry override ───────────────────────────────────────────
    # Runs AFTER classification so the tentative role is preserved in the
    # reason string. Reviewers see what the algorithm inferred plus why it
    # cannot be confirmed.
    if not lot_geometry_regular:
        classified = _degrade_all_to_manual_confirm(
            classified,
            reason=(
                "Irregular lot geometry (lot_geometry_regular=False) — "
                "standard edge assignment rules may not apply. "
                "All classifications are tentative and require manual confirmation"
            ),
        )

    return classified
