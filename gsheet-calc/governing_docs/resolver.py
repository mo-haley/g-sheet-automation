"""Resolver: determine resolution status for D/Q/CPIO controls.

No network fetching. No PDF retrieval. No OCR.
This module inspects what identifiers are available from discovery/registry
and optionally from ZIMAS parcel-profile data, and produces an honest
assessment of how far resolution can go with current data.

Resolution never overclaims "resolved" — if only a partial identifier exists,
that's what it says.

Source hierarchy (highest value first):
  1. ZIMAS parcel-profile authority items (ZI codes, overlay names, ordinances)
  2. ZIMAS MapServer identify fields (ZONE_CMPLT etc.)
  3. Parsed zoning string
  4. Manual entry / fixtures
"""

from __future__ import annotations

import re

from governing_docs.authority_links import extract_identifiers_for_control
from governing_docs.linker import link_registry
from governing_docs.models import (
    ControlLinkResult,
    ControlResolution,
    ControlType,
    LinkConfidence,
    ParcelProfileData,
    RegistryResolution,
    ResolutionStatus,
    SiteControl,
    SiteControlRegistry,
)


# ── Known placeholder patterns ──────────────────────────────────────

_PLACEHOLDER_ORD_PATTERN = re.compile(
    r"^ord-x{3,}$|^xxx|^placeholder|^unknown|^tbd$",
    re.IGNORECASE,
)

# Chapter 1A zoning layer ID
_CH1A_LAYER_ID = 1101


def resolve_registry(
    registry: SiteControlRegistry,
    community_plan_area: str | None = None,
    profile: ParcelProfileData | None = None,
) -> RegistryResolution:
    """Resolve all controls in a registry.

    Args:
        registry: The deduplicated SiteControlRegistry from Phase 1.
        community_plan_area: Community plan area name from Site/ZIMAS
            (e.g. "San Pedro"). Used to infer CPIO names.
        profile: Optional ZIMAS parcel profile data. When available,
            this is the highest-value source for identifiers.
    """
    result = RegistryResolution(parcel_id=registry.parcel_id)

    # Run control-to-authority linking
    link_results = link_registry(registry, profile)
    link_by_key = {lr.control.dedupe_key: lr for lr in link_results}

    for control in registry.controls:
        link_result = link_by_key.get(control.dedupe_key)
        resolution = _resolve_control(control, community_plan_area, profile, link_result)
        result.resolutions.append(resolution)

    # Registry-level warnings
    if result.needs_manual_review:
        count = sum(
            1 for r in result.resolutions
            if r.status == ResolutionStatus.MANUAL_REVIEW_REQUIRED
        )
        result.warnings.append(
            f"{count} control(s) require manual review before resolution can proceed."
        )

    unresolvable = [
        r for r in result.resolutions
        if r.status == ResolutionStatus.IDENTIFIED_ONLY
    ]
    if unresolvable:
        types = ", ".join(r.control.control_type.value for r in unresolvable)
        result.warnings.append(
            f"Controls with no actionable identifiers: {types}. "
            f"External lookup (ZIMAS case search, planning records) required."
        )

    return result


def _resolve_control(
    control: SiteControl,
    community_plan_area: str | None,
    profile: ParcelProfileData | None = None,
    link_result: ControlLinkResult | None = None,
) -> ControlResolution:
    """Route to the appropriate type-specific resolver."""
    if control.control_type == ControlType.D_LIMITATION:
        return _resolve_d_limitation(control, profile, link_result)
    elif control.control_type == ControlType.Q_CONDITION:
        return _resolve_q_condition(control, profile, link_result)
    elif control.control_type == ControlType.CPIO:
        return _resolve_cpio(control, community_plan_area, profile, link_result)
    else:
        return _resolve_generic(control)


# ── D Limitation resolver ────────────────────────────────────────────


def _resolve_d_limitation(
    control: SiteControl,
    profile: ParcelProfileData | None = None,
    link_result: ControlLinkResult | None = None,
) -> ControlResolution:
    """Resolve a D limitation control.

    Source priority:
    1. Linker result (deterministic or probable match)
    2. ZIMAS parcel profile (ZI items, ordinance references)
    3. Control's existing ordinance_number (from discovery)
    4. Nothing → identified_only
    """
    resolution = ControlResolution(
        control=control,
        status=ResolutionStatus.IDENTIFIED_ONLY,
        ordinance_number=control.ordinance_number,
    )

    _check_ch1a_format(control, resolution)

    ord_num = control.ordinance_number

    # Try to use linker result first
    if link_result and link_result.best_link:
        best = link_result.best_link
        if best.confidence in (LinkConfidence.DETERMINISTIC, LinkConfidence.PROBABLE):
            if best.ordinance_number and not ord_num:
                ord_num = best.ordinance_number
                resolution.ordinance_number = ord_num
                resolution.warnings.append(
                    f"D ordinance '{ord_num}' linked via {best.confidence.value} match: "
                    f"{best.rationale}"
                )
        elif best.confidence == LinkConfidence.CANDIDATE_SET:
            candidates = [
                f"ORD-{i.ordinance_number}" for i in best.linked_items
                if i.ordinance_number
            ]
            resolution.warnings.append(
                f"D limitation has {len(candidates)} candidate ordinance(s): "
                f"{', '.join(candidates)}. Manual review required to identify the D ordinance."
            )
        resolution.warnings.extend(best.warnings)

    # Fallback: try profile-based enrichment (for ZI items not caught by linker)
    if not ord_num and profile and profile.has_authority_items:
        profile_ids = extract_identifiers_for_control(profile, ControlType.D_LIMITATION)
        if profile_ids.get("ordinance_number"):
            ord_num = profile_ids["ordinance_number"]
            resolution.ordinance_number = ord_num
            resolution.warnings.append(
                f"D ordinance number '{ord_num}' obtained from ZIMAS parcel profile "
                f"(ZI item or authority link)."
            )

    if not ord_num:
        resolution.status = ResolutionStatus.IDENTIFIED_ONLY
        resolution.missing = ["ordinance_number", "document_text"]
        resolution.next_step = (
            "Look up D limitation ordinance number via ZIMAS parcel profile "
            "or LA City planning case search."
        )
        return resolution

    # Check for placeholder
    if _PLACEHOLDER_ORD_PATTERN.search(ord_num):
        resolution.status = ResolutionStatus.MANUAL_REVIEW_REQUIRED
        resolution.identifier_is_placeholder = True
        resolution.missing = ["real_ordinance_number", "document_text"]
        resolution.next_step = (
            f"Ordinance number '{ord_num}' appears to be a placeholder. "
            f"Obtain real ordinance number from planning records."
        )
        resolution.warnings.append(
            f"Placeholder ordinance number detected: '{ord_num}'"
        )
        return resolution

    # We have a real-looking ordinance number
    resolution.status = ResolutionStatus.IDENTIFIER_PARTIAL
    resolution.missing = ["document_text"]
    resolution.next_step = (
        f"D limitation ordinance '{ord_num}' identified. "
        f"Document must be retrieved and reviewed for density/FAR/height restrictions."
    )
    return resolution


# ── Q Condition resolver ─────────────────────────────────────────────


def _resolve_q_condition(
    control: SiteControl,
    profile: ParcelProfileData | None = None,
    link_result: ControlLinkResult | None = None,
) -> ControlResolution:
    """Resolve a Q condition control.

    Same pattern as D: ordinance number is the key identifier.
    ZIMAS identify does not provide Q ordinance numbers.
    """
    resolution = ControlResolution(
        control=control,
        status=ResolutionStatus.IDENTIFIED_ONLY,
        ordinance_number=control.ordinance_number,
    )

    _check_ch1a_format(control, resolution)

    ord_num = control.ordinance_number

    # Try linker result
    if link_result and link_result.best_link:
        best = link_result.best_link
        if best.confidence in (LinkConfidence.DETERMINISTIC, LinkConfidence.PROBABLE):
            if best.ordinance_number and not ord_num:
                ord_num = best.ordinance_number
                resolution.ordinance_number = ord_num
                resolution.warnings.append(
                    f"Q ordinance '{ord_num}' linked via {best.confidence.value} match: "
                    f"{best.rationale}"
                )
        elif best.confidence == LinkConfidence.CANDIDATE_SET:
            candidates = [
                f"ORD-{i.ordinance_number}" for i in best.linked_items
                if i.ordinance_number
            ]
            resolution.warnings.append(
                f"Q condition has {len(candidates)} candidate ordinance(s): "
                f"{', '.join(candidates)}. Manual review required."
            )
        resolution.warnings.extend(best.warnings)

    # Fallback: profile enrichment
    if not ord_num and profile and profile.has_authority_items:
        profile_ids = extract_identifiers_for_control(profile, ControlType.Q_CONDITION)
        if profile_ids.get("ordinance_number"):
            ord_num = profile_ids["ordinance_number"]
            resolution.ordinance_number = ord_num
            resolution.warnings.append(
                f"Q ordinance number '{ord_num}' obtained from ZIMAS parcel profile."
            )

    if not ord_num:
        resolution.status = ResolutionStatus.IDENTIFIED_ONLY
        resolution.missing = ["ordinance_number", "document_text"]
        resolution.next_step = (
            "Look up Q condition ordinance number via ZIMAS parcel profile "
            "or LA City planning case search."
        )
        return resolution

    if _PLACEHOLDER_ORD_PATTERN.search(ord_num):
        resolution.status = ResolutionStatus.MANUAL_REVIEW_REQUIRED
        resolution.identifier_is_placeholder = True
        resolution.missing = ["real_ordinance_number", "document_text"]
        resolution.next_step = (
            f"Ordinance number '{ord_num}' appears to be a placeholder. "
            f"Obtain real ordinance number from planning records."
        )
        resolution.warnings.append(
            f"Placeholder ordinance number detected: '{ord_num}'"
        )
        return resolution

    resolution.status = ResolutionStatus.IDENTIFIER_PARTIAL
    resolution.missing = ["document_text"]
    resolution.next_step = (
        f"Q condition ordinance '{ord_num}' identified. "
        f"Document must be retrieved and reviewed for use/density/parking restrictions."
    )
    return resolution


# ── CPIO resolver ────────────────────────────────────────────────────


def _resolve_cpio(
    control: SiteControl,
    community_plan_area: str | None,
    profile: ParcelProfileData | None = None,
    link_result: ControlLinkResult | None = None,
) -> ControlResolution:
    """Resolve a CPIO control.

    Source priority for CPIO identifiers:
    1. ZIMAS parcel profile: official CPIO name, subarea, ZI cross-reference
    2. Community plan area inference: "{CommunityPlan} CPIO"
    3. Discovery data: normalized_name, subarea from control

    Identifiers needed for full CPIO resolution:
    1. CPIO name (e.g. "San Pedro CPIO")
    2. Subarea (e.g. "E")
    3. Ordinance number
    4. Document text — always requires retrieval
    """
    resolution = ControlResolution(
        control=control,
        status=ResolutionStatus.IDENTIFIED_ONLY,
        ordinance_number=control.ordinance_number,
        subarea=control.subarea,
    )

    _check_ch1a_format(control, resolution)

    # Start with what discovery already found
    cpio_name = control.normalized_name
    subarea = control.subarea
    ordinance = control.ordinance_number
    inferred = False

    if cpio_name and cpio_name.upper().strip() == "CPIO":
        cpio_name = None

    # Use linker results for CPIO (deterministic overlay + probable ordinances)
    if link_result:
        for link in link_result.links:
            if link.confidence == LinkConfidence.UNLINKED:
                continue
            if link.overlay_name and not cpio_name:
                cpio_name = f"{link.overlay_name} CPIO"
                resolution.warnings.append(
                    f"CPIO name '{cpio_name}' from {link.confidence.value} link: "
                    f"{link.rationale}"
                )
            if link.subarea and not subarea:
                subarea = link.subarea
                resolution.subarea = subarea
                resolution.warnings.append(
                    f"CPIO subarea '{subarea}' from {link.confidence.value} link."
                )
            if link.ordinance_number and not ordinance:
                if link.confidence in (LinkConfidence.DETERMINISTIC, LinkConfidence.PROBABLE):
                    ordinance = link.ordinance_number
                    resolution.ordinance_number = ordinance
                    resolution.warnings.append(
                        f"CPIO ordinance '{ordinance}' from {link.confidence.value} link: "
                        f"{link.rationale}"
                    )
            resolution.warnings.extend(link.warnings)

    # Try to enrich from parcel profile (highest-value source)
    if profile and profile.has_authority_items:
        profile_ids = extract_identifiers_for_control(profile, ControlType.CPIO)

        # CPIO name from profile overlay text or ZI item
        profile_name = profile_ids.get("overlay_full_name") or profile_ids.get("overlay_name")
        if profile_name and not cpio_name:
            cpio_name = f"{profile_name} CPIO"
            resolution.warnings.append(
                f"CPIO name '{cpio_name}' obtained from ZIMAS parcel profile."
            )

        if profile_ids.get("subarea") and not subarea:
            subarea = profile_ids["subarea"]
            resolution.subarea = subarea
            resolution.warnings.append(
                f"CPIO subarea '{subarea}' obtained from ZIMAS parcel profile."
            )

        if profile_ids.get("ordinance_number") and not ordinance:
            ordinance = profile_ids["ordinance_number"]
            resolution.ordinance_number = ordinance
            resolution.warnings.append(
                f"CPIO ordinance '{ordinance}' obtained from ZIMAS parcel profile."
            )

    # Fallback: infer CPIO name from community plan area
    if not cpio_name and community_plan_area:
        cpio_name = f"{community_plan_area} CPIO"
        inferred = True
        resolution.warnings.append(
            f"CPIO name inferred from community plan area: '{cpio_name}'. "
            f"This is a common convention but not guaranteed."
        )

    resolution.inferred_name = cpio_name if inferred else None

    # Build missing list and determine status
    missing: list[str] = []

    has_name = bool(cpio_name)
    has_subarea = bool(subarea)
    has_ordinance = bool(ordinance)

    if not has_name:
        missing.append("cpio_name")
    if not has_subarea:
        missing.append("subarea")
    if not has_ordinance:
        missing.append("ordinance_number")
    missing.append("document_text")  # Always missing — no document fetching

    resolution.missing = missing

    if has_name and has_subarea and has_ordinance:
        resolution.status = ResolutionStatus.IDENTIFIER_COMPLETE
        resolution.next_step = (
            f"CPIO '{cpio_name}' subarea '{subarea}' "
            f"(Ord. {ordinance}) fully identified. "
            f"Document must be retrieved and parsed for development standards."
        )
    elif has_name and has_subarea:
        resolution.status = ResolutionStatus.IDENTIFIER_PARTIAL
        resolution.next_step = (
            f"CPIO '{cpio_name}' subarea '{subarea}' identified. "
            f"Look up ordinance number, then retrieve document."
        )
    elif has_name:
        resolution.status = ResolutionStatus.IDENTIFIER_PARTIAL
        resolution.next_step = (
            f"CPIO '{cpio_name}' identified but subarea unknown. "
            f"Determine subarea from CPIO district map or ZIMAS parcel profile."
        )
    else:
        resolution.status = ResolutionStatus.IDENTIFIED_ONLY
        resolution.next_step = (
            "CPIO presence detected but name and subarea unknown. "
            "Check ZIMAS parcel profile for CPIO district name and subarea."
        )

    return resolution


# ── Generic resolver (for types without specific logic) ──────────────


def _resolve_generic(control: SiteControl) -> ControlResolution:
    """Fallback resolver for control types without specific resolution logic."""
    resolution = ControlResolution(
        control=control,
        status=ResolutionStatus.IDENTIFIED_ONLY,
        missing=["resolution_logic_not_implemented"],
    )
    resolution.next_step = (
        f"Control type '{control.control_type.value}' discovered "
        f"but no specific resolution logic implemented for Phase 2A."
    )
    return resolution


# ── Shared helpers ───────────────────────────────────────────────────


def _check_ch1a_format(
    control: SiteControl,
    resolution: ControlResolution,
) -> None:
    """Add warning if control was discovered from Chapter 1A bracket-format zoning string.

    The bracket format (e.g. [LF1-WH1-5][P2-FA][CPIO]) is structurally different
    from Chapter 1 format and the existing zoning parser cannot handle it.
    Discovery from this format may be incomplete or unreliable.
    """
    if control.zimas_layer_id == _CH1A_LAYER_ID:
        resolution.source_format_unreliable = True
        resolution.source_format_warning = (
            f"Control discovered from Chapter 1A zoning layer (layer {_CH1A_LAYER_ID}). "
            f"The bracket-delimited zoning format used by Chapter 1A is not fully supported "
            f"by the current zoning parser. D/Q detection and supplemental district extraction "
            f"may be incomplete. Verify against ZIMAS parcel profile."
        )
        resolution.warnings.append(resolution.source_format_warning)
