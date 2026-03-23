"""Input coverage assessment for the ZIMAS linked-doc pipeline.

Evaluates how complete and trustworthy the inputs to the pipeline were.
Returns a coverage level and issues that explain which signals were absent
or degraded.

A "clean" linked-doc registry built from thin or uncertain inputs is NOT
evidence of no linked authority. It is evidence of an insufficient search.
This module makes that distinction explicit.

Coverage levels (best to worst):
    complete   — primary detection sources present and zone parse confirmed
    partial    — some sources absent, or parse quality reduced but not failed
    thin       — raw identify absent AND structured fields empty/no parse result
    uncertain  — zone parse explicitly returned "unresolved"

These are orthogonal to registry_confidence (which describes what was found).
Coverage describes how much to trust that the search was thorough.

Determination order (first match wins):
    1. uncertain — zoning_parse_confidence == "unresolved"
    2. thin      — raw_zimas_identify empty AND (structured fields all empty
                   OR no zone parse result)
    3. partial   — any of: raw identify absent, parse provisional, parse issues
                   present, structured fields empty, APN absent
    4. complete  — none of the above
"""

from __future__ import annotations

from zimas_linked_docs.models import (
    ZimasLinkedDocInput,
    ZimasDocIssue,
    INPUT_COVERAGE_COMPLETE,
    INPUT_COVERAGE_PARTIAL,
    INPUT_COVERAGE_THIN,
    INPUT_COVERAGE_UNCERTAIN,
)


def assess_input_coverage(
    inp: ZimasLinkedDocInput,
) -> tuple[str, list[ZimasDocIssue]]:
    """Assess the completeness and trustworthiness of pipeline inputs.

    Returns (coverage_level, issues).
    Issues are informational/warning explanations of coverage gaps — they
    do not represent detection results and should be prepended to all_issues
    before registry issues so they appear in source order.
    """
    issues: list[ZimasDocIssue] = []
    deficiencies: list[str] = []  # internal tags driving level determination
    uncertain = False

    # ── Zone string parse quality ─────────────────────────────────────────────

    if inp.zoning_parse_confidence == "unresolved":
        uncertain = True
        issues.append(ZimasDocIssue(
            step="input_coverage",
            field="zoning_parse_confidence",
            severity="error",
            message=(
                "Zone string parse confidence is 'unresolved'. "
                "The zone string could not be matched to a known base zone. "
                "Overlay, D, and Q signals derived from the zone string are "
                "incomplete or absent. Sparse linked-doc results may reflect "
                "this parse failure, not genuine absence of linked authority."
            ),
            action_required=(
                "Manually inspect the zone string and confirm applicable overlays, "
                "D/Q conditions, and supplemental districts."
            ),
            confidence_impact="degrades_to_unresolved",
        ))

    elif inp.zoning_parse_confidence == "provisional":
        deficiencies.append("zone_parse_provisional")
        issues.append(ZimasDocIssue(
            step="input_coverage",
            field="zoning_parse_confidence",
            severity="warning",
            message=(
                "Zone string parse confidence is 'provisional'. "
                "Some zone-string-derived signals (supplemental districts, "
                "D/Q details) may be incomplete."
            ),
            confidence_impact="degrades_to_provisional",
        ))

    elif inp.zoning_parse_confidence is None:
        deficiencies.append("zone_parse_absent")
        issues.append(ZimasDocIssue(
            step="input_coverage",
            field="zoning_parse_confidence",
            severity="warning",
            message=(
                "No zone string parse result provided (zoning_parse_confidence is None). "
                "Inline-D detection, supplemental district detection, and D/Q ordinance "
                "hints are unavailable. The pipeline ran without zone-string-derived signals."
            ),
            action_required=(
                "Populate ZimasLinkedDocInput with ZoningParseResult fields before running."
            ),
            confidence_impact="degrades_to_provisional",
        ))

    # ── Zone parse issues ─────────────────────────────────────────────────────

    if inp.zoning_parse_issues:
        deficiencies.append("zone_parse_issues_present")
        for pi in inp.zoning_parse_issues:
            issues.append(ZimasDocIssue(
                step="input_coverage",
                field="zoning_parse_issues",
                severity="warning",
                message=f"Zone string parse issue: {pi}",
                confidence_impact="none",
            ))

    # ── Raw ZIMAS identify data ───────────────────────────────────────────────

    raw_results = inp.raw_zimas_identify.get("results", [])
    if not raw_results:
        deficiencies.append("raw_identify_absent")
        issues.append(ZimasDocIssue(
            step="input_coverage",
            field="raw_zimas_identify",
            severity="warning",
            message=(
                "raw_zimas_identify contains no results. "
                "Layer-level scanning for ZI numbers, ordinance numbers, case numbers, "
                "and URLs did not run. Detection relied solely on structured Site fields "
                "and zone-string-derived signals."
            ),
            action_required=(
                "Pass the full ZIMAS identify response via "
                "ZimasLinkedDocInput.raw_zimas_identify to enable complete detection."
            ),
            confidence_impact="degrades_to_provisional",
        ))

    # ── Raw text fragments ────────────────────────────────────────────────────

    if not inp.raw_text_fragments:
        # Expected to be absent for most callers — info only, not a deficiency tag
        issues.append(ZimasDocIssue(
            step="input_coverage",
            field="raw_text_fragments",
            severity="info",
            message=(
                "No raw_text_fragments provided. "
                "Text-pattern scanning for ZI numbers, ordinance numbers, and case "
                "references from portal pages did not run."
            ),
            confidence_impact="none",
        ))

    # ── Structured Site fields ────────────────────────────────────────────────
    # A confirmed zone parse is authoritative: if it returned no overlays/Q/D,
    # that IS a meaningful finding, not a coverage gap. Only flag absent structured
    # fields as a deficiency when the zone parse was also absent or non-confirmed.

    has_structured = any([
        inp.specific_plan,
        inp.overlay_zones,
        inp.q_conditions,
        inp.d_limitations,
    ])

    if not has_structured and inp.zoning_parse_confidence is None:
        deficiencies.append("structured_empty_no_parse")
        issues.append(ZimasDocIssue(
            step="input_coverage",
            field="structured_fields",
            severity="error",
            message=(
                "No structured Site fields were populated (specific_plan, overlay_zones, "
                "q_conditions, d_limitations all absent) and no zone parse result was "
                "provided. The pipeline had no primary zone-string signals to work from. "
                "Zero linked-doc results here are not meaningful."
            ),
            action_required=(
                "Populate Site fields from ingest before running. "
                "Pass ZoningParseResult fields via ZimasLinkedDocInput."
            ),
            confidence_impact="degrades_to_unresolved",
        ))

    elif not has_structured and inp.zoning_parse_confidence not in (None, "confirmed"):
        # Parse ran but was provisional — structured fields provide a useful cross-check
        # that is absent here.
        deficiencies.append("structured_fields_empty")
        issues.append(ZimasDocIssue(
            step="input_coverage",
            field="structured_fields",
            severity="warning",
            message=(
                "No structured Site fields were populated (specific_plan, overlay_zones, "
                "q_conditions, d_limitations all absent). "
                "Zone-string-derived signals were the only primary detection source, "
                "and parse confidence was not 'confirmed'."
            ),
            confidence_impact="degrades_to_provisional",
        ))

    # ── APN ───────────────────────────────────────────────────────────────────

    if not inp.apn:
        deficiencies.append("apn_absent")
        issues.append(ZimasDocIssue(
            step="input_coverage",
            field="apn",
            severity="info",
            message=(
                "APN not provided. Record and candidate IDs use placeholder 'NOAPN'. "
                "Parcel identity cross-referencing is unavailable."
            ),
            confidence_impact="none",
        ))

    # ── Inline-D gap-fill signal ──────────────────────────────────────────────
    # When gap-fill was required, the zone string format was non-standard.
    # This doesn't degrade coverage level on its own, but it's a provenance note.

    if inp.has_d_from_zone_string and not inp.d_limitations:
        issues.append(ZimasDocIssue(
            step="input_coverage",
            field="d_limitations",
            severity="info",
            message=(
                "D limitation was detected by zone string parser (inline-D suffix) "
                "but was absent from Site.d_limitations — normalizer bracket-scan missed it. "
                "Gap-fill was applied. Non-standard zone string format; "
                "verify that no other signals from this string were dropped."
            ),
            confidence_impact="none",
        ))

    # ── Determine coverage level ──────────────────────────────────────────────

    # Rule 1: uncertain — parse explicitly failed
    if uncertain:
        return INPUT_COVERAGE_UNCERTAIN, issues

    # Rule 2: thin — raw identify absent AND zone-string signals are also weak/absent.
    # A confirmed zone parse is sufficient to make results meaningful even without
    # raw identify data — it means we checked the zone string authoritatively.
    # Thin requires that BOTH raw identify AND zone-string derivation are inadequate.
    zone_parse_weak = inp.zoning_parse_confidence not in ("confirmed",)  # None or provisional
    if "raw_identify_absent" in deficiencies and zone_parse_weak and (
        "structured_empty_no_parse" in deficiencies
        or "structured_fields_empty" in deficiencies
        or "zone_parse_absent" in deficiencies
    ):
        return INPUT_COVERAGE_THIN, issues

    # Rule 3: partial — any significant detection gap
    _partial_triggers = {
        "raw_identify_absent",
        "zone_parse_provisional",
        "zone_parse_absent",
        "zone_parse_issues_present",
        "structured_fields_empty",
        "structured_empty_no_parse",
    }
    if any(d in _partial_triggers for d in deficiencies):
        return INPUT_COVERAGE_PARTIAL, issues

    # Rule 4: complete — no significant gaps
    return INPUT_COVERAGE_COMPLETE, issues
