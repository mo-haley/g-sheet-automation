"""Phase 1 registry: merge, deduplicate, and normalize discovered site controls.

Takes observations from all discovery sources, deduplicates by (control_type, key),
preserves raw evidence, and surfaces conflicts rather than silently collapsing them.
"""

from __future__ import annotations

from governing_docs.models import (
    ControlType,
    RegistryConflict,
    SiteControl,
    SiteControlRegistry,
)


def build_registry(
    observations: list[SiteControl],
    parcel_id: str | None = None,
) -> SiteControlRegistry:
    """Build a deduplicated SiteControlRegistry from raw discovery observations.

    Deduplication rules:
    - Group by dedupe_key (control_type::normalized_value).
    - Within a group, pick a canonical representative (prefer raw ZIMAS > parse > site model).
    - Merge metadata: collect all ordinance numbers, subareas, warnings.
    - If conflicting non-null values exist for key fields, create a RegistryConflict.
    """
    registry = SiteControlRegistry(
        parcel_id=parcel_id,
        all_observations=list(observations),
    )

    if not observations:
        return registry

    # Group by dedupe_key
    groups: dict[str, list[SiteControl]] = {}
    for obs in observations:
        key = obs.dedupe_key
        groups.setdefault(key, []).append(obs)

    for dedupe_key, group in groups.items():
        canonical, conflicts = _merge_group(dedupe_key, group)
        registry.controls.append(canonical)
        registry.conflicts.extend(conflicts)

    # Registry-level warnings
    _check_registry_warnings(registry)

    return registry


# Source priority: higher = preferred as canonical
_SOURCE_PRIORITY = {
    "raw_zimas_identify": 3,
    "zoning_parse_result": 2,
    "site_model": 1,
}


def _merge_group(
    dedupe_key: str, group: list[SiteControl]
) -> tuple[SiteControl, list[RegistryConflict]]:
    """Merge a group of observations with the same dedupe_key into one canonical SiteControl."""
    conflicts: list[RegistryConflict] = []

    # Sort by source priority (highest first)
    sorted_group = sorted(
        group,
        key=lambda sc: _SOURCE_PRIORITY.get(sc.source_type.value, 0),
        reverse=True,
    )

    # Start from the highest-priority observation
    canonical = _copy_control(sorted_group[0])

    # Merge fields from other observations
    all_warnings: list[str] = []
    all_source_details: list[str] = []

    for obs in sorted_group:
        all_source_details.append(obs.source_detail)
        all_warnings.extend(obs.warnings)

        # Merge ordinance_number
        if obs.ordinance_number and not canonical.ordinance_number:
            canonical.ordinance_number = obs.ordinance_number

        # Merge subarea
        if obs.subarea and not canonical.subarea:
            canonical.subarea = obs.subarea

        # Merge normalized_name
        if obs.normalized_name and not canonical.normalized_name:
            canonical.normalized_name = obs.normalized_name

        # resolution_notes: keep the most specific
        if obs.resolution_notes and not canonical.resolution_notes:
            canonical.resolution_notes = obs.resolution_notes

    # Check for conflicting ordinance numbers
    ord_numbers = [
        (obs.ordinance_number, obs.source_detail)
        for obs in sorted_group
        if obs.ordinance_number
    ]
    unique_ords = set(o for o, _ in ord_numbers)
    if len(unique_ords) > 1:
        conflicts.append(RegistryConflict(
            dedupe_key=dedupe_key,
            field_name="ordinance_number",
            values=list(unique_ords),
            source_details=[s for _, s in ord_numbers],
            resolution="unresolved",
        ))

    # Check for conflicting subareas
    subareas = [
        (obs.subarea, obs.source_detail)
        for obs in sorted_group
        if obs.subarea
    ]
    unique_subareas = set(s for s, _ in subareas)
    if len(unique_subareas) > 1:
        conflicts.append(RegistryConflict(
            dedupe_key=dedupe_key,
            field_name="subarea",
            values=list(unique_subareas),
            source_details=[s for _, s in subareas],
            resolution="unresolved",
        ))

    # Deduplicate warnings
    canonical.warnings = list(dict.fromkeys(all_warnings))

    # Add provenance note
    if len(sorted_group) > 1:
        canonical.resolution_notes = (
            f"Merged from {len(sorted_group)} sources: "
            + "; ".join(all_source_details)
            + (f". {canonical.resolution_notes}" if canonical.resolution_notes else "")
        )

    return canonical, conflicts


def _copy_control(sc: SiteControl) -> SiteControl:
    """Create a shallow copy of a SiteControl."""
    return SiteControl(
        control_type=sc.control_type,
        raw_value=sc.raw_value,
        source_type=sc.source_type,
        source_detail=sc.source_detail,
        normalized_name=sc.normalized_name,
        ordinance_number=sc.ordinance_number,
        subarea=sc.subarea,
        parcel_id=sc.parcel_id,
        document_resolution_likely_required=sc.document_resolution_likely_required,
        resolution_notes=sc.resolution_notes,
        zimas_layer_id=sc.zimas_layer_id,
        zimas_layer_name=sc.zimas_layer_name,
        raw_field_name=sc.raw_field_name,
        warnings=list(sc.warnings),
    )


def _check_registry_warnings(registry: SiteControlRegistry) -> None:
    """Add registry-level warnings for suspicious patterns."""
    types = registry.control_types_present

    # Warn if D limitation found but no ordinance number anywhere
    d_controls = registry.get_controls_by_type(ControlType.D_LIMITATION)
    for dc in d_controls:
        if not dc.ordinance_number:
            registry.warnings.append(
                f"D limitation discovered but no ordinance number available. "
                f"Document resolution required. (key: {dc.dedupe_key})"
            )

    # Warn if Q condition found but no ordinance number
    q_controls = registry.get_controls_by_type(ControlType.Q_CONDITION)
    for qc in q_controls:
        if not qc.ordinance_number:
            registry.warnings.append(
                f"Q condition discovered but no ordinance number available. "
                f"Document resolution required. (key: {qc.dedupe_key})"
            )

    # Warn if CPIO found without specific subarea
    cpio_controls = registry.get_controls_by_type(ControlType.CPIO)
    for cc in cpio_controls:
        if not cc.subarea:
            registry.warnings.append(
                f"CPIO discovered but subarea not identified. "
                f"Subarea is required for document lookup. (key: {cc.dedupe_key})"
            )

    if registry.has_unresolved_conflicts:
        registry.warnings.append(
            f"{len([c for c in registry.conflicts if c.resolution == 'unresolved'])} "
            f"unresolved conflict(s) across discovery sources."
        )
