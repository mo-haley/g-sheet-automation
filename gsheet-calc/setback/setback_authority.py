"""Setback authority resolution (Step 1).

Accepts raw zoning inputs, resolves code family and yard rule inheritance,
and flags authority interrupters (Specific Plan, CPIO, D limitation,
Q condition) that have been detected but not interpreted.

ASSUMPTION — LAMC section citations:
    Section numbers follow the density module's zone-to-section mapping
    (density/density_authority.py ZONE_DENSITY_TABLE). The yard-specific
    subsections within each zone section (e.g., 12.10-C for R3 side yards)
    have NOT been independently verified against published LAMC text.
    Verify each section reference before using this output for permit work.
"""

from __future__ import annotations

from setback.models import (
    AuthorityInterrupt,
    EarlyExit,
    SetbackAuthorityResult,
    SetbackIssue,
)

# ---------------------------------------------------------------------------
# Zone → yard family table
# ---------------------------------------------------------------------------
# Keys must match the normalized base zone strings produced by the density
# module's extract_base_zone() or site.zone (already extracted from ZIMAS).
#
# Fields:
#   code_family          — broad grouping used by downstream modules
#   baseline_yard_family — residential yard standard to inherit; None for splits
#   sections             — LAMC sections covering this zone's yard requirements
#   cm_split             — True: yard family depends on use type (CM only)
#   ras_split            — True: ground-floor and above-grade yards differ (RAS)
#
# ASSUMPTION — RD zones: sprint scope is multifamily/mixed-use. RD zones
#   are included so the module does not hard-error on them, but downstream
#   yard formula coverage should be verified before relying on RD output.
#
# ASSUMPTION — MR zones: MR1→R4 and MR2→R5 inheritance follows the density
#   module's pattern. Verify against LAMC 12.17.5 yard provisions.
#
# ASSUMPTION — C2 section: the density module maps C2 to LAMC 12.14.5
#   (same as C1.5). If C2 has its own LAMC section for yard requirements,
#   update C2's entry here.

ZONE_SETBACK_TABLE: dict[str, dict] = {
    # Restricted Density Residential
    "RD1.5": {"code_family": "RD", "baseline_yard_family": "RD1.5", "sections": ["LAMC 12.09.5"], "cm_split": False, "ras_split": False},
    "RD2":   {"code_family": "RD", "baseline_yard_family": "RD2",   "sections": ["LAMC 12.09.5"], "cm_split": False, "ras_split": False},
    "RD3":   {"code_family": "RD", "baseline_yard_family": "RD3",   "sections": ["LAMC 12.09.5"], "cm_split": False, "ras_split": False},
    "RD4":   {"code_family": "RD", "baseline_yard_family": "RD4",   "sections": ["LAMC 12.09.5"], "cm_split": False, "ras_split": False},
    "RD5":   {"code_family": "RD", "baseline_yard_family": "RD5",   "sections": ["LAMC 12.09.5"], "cm_split": False, "ras_split": False},
    "RD6":   {"code_family": "RD", "baseline_yard_family": "RD6",   "sections": ["LAMC 12.09.5"], "cm_split": False, "ras_split": False},
    # Multifamily Residential
    "R3":    {"code_family": "R3", "baseline_yard_family": "R3",    "sections": ["LAMC 12.10"],   "cm_split": False, "ras_split": False},
    "R4":    {"code_family": "R4", "baseline_yard_family": "R4",    "sections": ["LAMC 12.11"],   "cm_split": False, "ras_split": False},
    "R5":    {"code_family": "R5", "baseline_yard_family": "R5",    "sections": ["LAMC 12.12"],   "cm_split": False, "ras_split": False},
    # Residential/Commercial Blend — ground-floor vs. above split
    "RAS3":  {"code_family": "RAS", "baseline_yard_family": None,   "sections": ["LAMC 12.10.5"], "cm_split": False, "ras_split": True},
    "RAS4":  {"code_family": "RAS", "baseline_yard_family": None,   "sections": ["LAMC 12.11.5"], "cm_split": False, "ras_split": True},
    # Commercial — residential uses inherit the residential yard family
    "CR":    {"code_family": "C",  "baseline_yard_family": "R4",   "sections": ["LAMC 12.13"],   "cm_split": False, "ras_split": False},
    "C1":    {"code_family": "C",  "baseline_yard_family": "R3",   "sections": ["LAMC 12.14"],   "cm_split": False, "ras_split": False},
    "C1.5":  {"code_family": "C",  "baseline_yard_family": "R4",   "sections": ["LAMC 12.14.5"], "cm_split": False, "ras_split": False},
    "C2":    {"code_family": "C",  "baseline_yard_family": "R4",   "sections": ["LAMC 12.14.5"], "cm_split": False, "ras_split": False},
    "C4":    {"code_family": "C",  "baseline_yard_family": "R4",   "sections": ["LAMC 12.16"],   "cm_split": False, "ras_split": False},
    "C5":    {"code_family": "C",  "baseline_yard_family": "R4",   "sections": ["LAMC 12.16.5"], "cm_split": False, "ras_split": False},
    # CM — split: R3-class uses vs. other residential at floor level
    "CM":    {"code_family": "CM", "baseline_yard_family": None,   "sections": ["LAMC 12.17.5"], "cm_split": True,  "ras_split": False},
    # Industrial with Residential
    "MR1":   {"code_family": "M",  "baseline_yard_family": "R4",   "sections": ["LAMC 12.17.5"], "cm_split": False, "ras_split": False},
    "MR2":   {"code_family": "M",  "baseline_yard_family": "R5",   "sections": ["LAMC 12.17.5"], "cm_split": False, "ras_split": False},
}

# When a commercial zone inherits residential yards, the residential zone's
# own section also governs the yard dimensions. Both sections are included
# in governing_sections so the reviewer knows where to look.
_INHERITED_RESIDENTIAL_SECTIONS: dict[str, str] = {
    "R3": "LAMC 12.10",
    "R4": "LAMC 12.11",
    "R5": "LAMC 12.12",
}

# CM split: both candidate residential sections apply until use type is confirmed.
_CM_CANDIDATE_SECTIONS = ["LAMC 12.10", "LAMC 12.11"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_chapter_1a(value: bool | str | None) -> bool | None:
    """Normalize chapter_1a_applicable input to True / False / None.

    Accepts booleans, common string variants, and None. Anything
    unrecognized resolves to None (unresolved).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ("true", "yes", "1", "chapter_1a", "1a"):
        return True
    if v in ("false", "no", "0", "chapter_1", "1"):
        return False
    return None


def _resolve_zone_entry(base_zone: str | None, raw_zone: str | None) -> tuple[dict | None, str]:
    """Look up the zone table entry, returning (entry, zone_key_used).

    Prefers base_zone (pre-extracted). Falls back to raw_zone with a
    simple suffix strip (e.g. "C2-1VL" → "C2"). Does not attempt
    full regex parsing of raw_zone — for complex raw strings, the caller
    should pre-extract via density.density_authority.extract_base_zone().
    """
    for candidate in (base_zone, raw_zone):
        if not candidate or not candidate.strip():
            continue
        key = candidate.strip().upper()
        entry = ZONE_SETBACK_TABLE.get(key)
        if entry:
            return entry, candidate
        # Try stripping height district suffix: "C2-1VL" → "C2"
        stripped = key.split("-")[0].strip()
        entry = ZONE_SETBACK_TABLE.get(stripped)
        if entry:
            return entry, candidate
    return None, base_zone or raw_zone or ""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def resolve_setback_authority(
    raw_zone: str | None,
    base_zone: str | None,
    height_district: str | None,  # accepted, reserved for future height-district yard logic
    specific_plan: str | None,
    cpio: str | None,
    d_limitation: str | None,
    q_condition: str | None,
    chapter_1a_applicable: bool | str | None,
    small_lot_subdivision: bool,
) -> SetbackAuthorityResult:
    """Resolve code family, inherited yard rules, and authority interrupts.

    Steps:
      1. Early exit gate — small_lot_subdivision
      2. Zone table lookup → code_family, baseline_yard_family, sections
      3. Authority interrupters (Specific Plan, CPIO, D, Q)
      4. Split conditions (CM, RAS)
      5. governing_yard_family: set only when no interrupter and no split

    The height_district parameter is accepted but not evaluated here.
    Height-district-specific yard modifications (e.g., VL, XL height
    envelopes) are deferred to setback_yard_family.py.

    Returns:
        SetbackAuthorityResult with early_exit, confidence, baseline and
        governing yard families, authority_interrupters, and issues.
    """
    issues: list[SetbackIssue] = []

    # ── GATE 1: small lot subdivision ───────────────────────────────────────
    if small_lot_subdivision:
        return SetbackAuthorityResult(
            early_exit=EarlyExit(
                triggered=True,
                reason=(
                    "Small lot subdivision — separate setback regime applies "
                    "(LAMC 12.22-C-27 or applicable SLS guidelines). "
                    "This module does not cover small lot subdivision setbacks."
                ),
            ),
            confidence="unresolved",
            issues=[SetbackIssue(
                step="STEP_1_authority",
                field="small_lot_subdivision",
                severity="error",
                message=(
                    "Small lot subdivision flagged. Setback rules for SLS are "
                    "governed by a separate regime and are not calculated here."
                ),
                action_required=(
                    "Apply small lot subdivision setback standards per "
                    "LAMC 12.22-C-27 or the applicable SLS design guidelines."
                ),
                confidence_impact="degrades_to_unresolved",
            )],
        )

    # ── Zone lookup ──────────────────────────────────────────────────────────
    entry, zone_key_used = _resolve_zone_entry(base_zone, raw_zone)

    if entry is None:
        zone_display = base_zone or raw_zone or "(none provided)"
        return SetbackAuthorityResult(
            confidence="unresolved",
            issues=[SetbackIssue(
                step="STEP_1_authority",
                field="base_zone",
                severity="error",
                message=(
                    f"Zone '{zone_display}' not found in setback zone table. "
                    "Cannot determine yard family or governing sections."
                ),
                action_required=(
                    f"Manually determine yard family and governing LAMC sections "
                    f"for zone '{zone_display}'."
                ),
                confidence_impact="degrades_to_unresolved",
            )],
        )

    code_family: str = entry["code_family"]
    baseline_yard_family: str | None = entry["baseline_yard_family"]
    cm_split: bool = entry["cm_split"]
    ras_split: bool = entry["ras_split"]

    # Build governing sections list.
    # For commercial zones inheriting residential yards: include both the
    # commercial section (establishing the inheritance rule) and the
    # residential section (providing the actual yard dimensions).
    governing_sections: list[str] = list(entry["sections"])

    if baseline_yard_family in _INHERITED_RESIDENTIAL_SECTIONS and not cm_split and not ras_split:
        res_section = _INHERITED_RESIDENTIAL_SECTIONS[baseline_yard_family]
        if res_section not in governing_sections:
            governing_sections.append(res_section)

    # CM: both candidate residential sections apply pending use-type confirmation
    if cm_split:
        for sec in _CM_CANDIDATE_SECTIONS:
            if sec not in governing_sections:
                governing_sections.append(sec)

    # ── Authority interrupters ───────────────────────────────────────────────
    authority_interrupters: list[AuthorityInterrupt] = []
    confidence = "confirmed"

    if specific_plan:
        authority_interrupters.append(AuthorityInterrupt(
            source="specific_plan",
            reason=(
                f"Specific plan '{specific_plan}' detected but document not pulled. "
                "Cannot confirm whether it overrides base yard requirements."
            ),
            status="not_interpreted",
        ))
        issues.append(SetbackIssue(
            step="STEP_1_authority",
            field="specific_plan",
            severity="warning",
            message=(
                f"Specific plan '{specific_plan}' present but not interpreted "
                "for setback/yard provisions."
            ),
            action_required=(
                f"Download and review specific plan '{specific_plan}' for "
                "setback and yard requirements."
            ),
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    if cpio:
        authority_interrupters.append(AuthorityInterrupt(
            source="cpio",
            reason=(
                f"CPIO '{cpio}' detected but document not pulled. "
                "Cannot confirm whether it overrides base yard requirements."
            ),
            status="not_interpreted",
        ))
        issues.append(SetbackIssue(
            step="STEP_1_authority",
            field="cpio",
            severity="warning",
            message=(
                f"CPIO '{cpio}' present but not interpreted for setback/yard provisions."
            ),
            action_required=(
                f"Review CPIO '{cpio}' for setback and yard requirements."
            ),
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    if d_limitation:
        authority_interrupters.append(AuthorityInterrupt(
            source="d_limitation",
            reason=(
                f"D limitation '{d_limitation}' detected but ordinance not reviewed "
                "for setback impact."
            ),
            status="not_interpreted",
        ))
        issues.append(SetbackIssue(
            step="STEP_1_authority",
            field="d_limitation",
            severity="warning",
            message=(
                f"D limitation '{d_limitation}' present but ordinance not reviewed "
                "for setback provisions."
            ),
            action_required=(
                f"Review D limitation ordinance '{d_limitation}' for setback requirements."
            ),
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    if q_condition:
        authority_interrupters.append(AuthorityInterrupt(
            source="q_condition",
            reason=(
                f"Q condition '{q_condition}' detected but ordinance not reviewed "
                "for setback impact."
            ),
            status="not_interpreted",
        ))
        issues.append(SetbackIssue(
            step="STEP_1_authority",
            field="q_condition",
            severity="warning",
            message=(
                f"Q condition '{q_condition}' present but ordinance not reviewed "
                "for setback provisions."
            ),
            action_required=(
                f"Review Q condition ordinance '{q_condition}' for setback requirements."
            ),
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    # ── Split conditions ─────────────────────────────────────────────────────
    # CM and RAS splits are unresolved until the reviewer confirms use type.
    # These degrade confidence independently of overlay interrupters.

    if cm_split:
        # CM is always unresolved: the yard family cannot be determined
        # without knowing the use type.
        confidence = "unresolved"
        issues.append(SetbackIssue(
            step="STEP_1_authority",
            field="cm_split",
            severity="warning",
            message=(
                "CM zone: yard family depends on use type. "
                "R3-class uses inherit R3 yards (LAMC 12.10); "
                "other residential at floor level inherit R4 yards (LAMC 12.11). "
                "Both options are presented — neither is auto-selected."
            ),
            action_required=(
                "Confirm CM zone use type to select R3 or R4 yard family. "
                "Do not apply a single yard standard without confirmation."
            ),
            confidence_impact="degrades_to_unresolved",
        ))

    if ras_split:
        # RAS is provisional: the zone is known, but ground-floor and
        # above-grade yard treatment must be confirmed separately.
        if confidence == "confirmed":
            confidence = "provisional"
        issues.append(SetbackIssue(
            step="STEP_1_authority",
            field="ras_split",
            severity="warning",
            message=(
                "RAS zone: ground-floor commercial and residential-above "
                "portions may have different yard requirements. "
                "Review the RAS zone section for applicable yard rules per use."
            ),
            action_required=(
                "Confirm RAS zone yard treatment for ground-floor commercial "
                "and residential-above portions separately."
            ),
            confidence_impact="degrades_to_provisional",
        ))

    # ── governing_yard_family ────────────────────────────────────────────────
    # Governing is None whenever any interrupter is present OR any split is
    # unresolved. The baseline is still available but cannot be treated as
    # governing without resolving all interrupts and splits.
    has_interrupter = bool(authority_interrupters)
    governing_yard_family: str | None = (
        None
        if (has_interrupter or cm_split or ras_split)
        else baseline_yard_family
    )

    return SetbackAuthorityResult(
        code_family=code_family,
        baseline_yard_family=baseline_yard_family,
        governing_yard_family=governing_yard_family,
        cm_split=cm_split,
        ras_split=ras_split,
        governing_sections=governing_sections,
        authority_interrupters=authority_interrupters,
        chapter_1a_applicable=_normalize_chapter_1a(chapter_1a_applicable),
        early_exit=EarlyExit(triggered=False),
        confidence=confidence,
        issues=issues,
    )
