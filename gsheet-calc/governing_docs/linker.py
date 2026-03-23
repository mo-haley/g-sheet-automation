"""Control-to-authority linking with disambiguation.

Links discovered site controls (D, Q, CPIO) to specific parcel-profile
authority items (ordinances, ZI items, overlay fields).

Conservative: never claims deterministic when evidence is only probable.
Preserves all candidates and explains the rationale.

Disambiguation strategy for D/Q (where no structural label exists):
1. Remove SA-suffixed ordinances (CPIO/subarea ordinances)
2. Remove ordinances referenced by ZI item profile text (accounted for by other controls)
3. Remove ordinances that match known CPIO implementation ordinances (from overlay reference)
4. Remove ordinances declared in ZI document headers for non-D/Q ZI items (document-derived)
5. If one ordinance remains → probable
6. If multiple remain → candidate_set with full provenance
7. If none remain → unlinked

Evidence priority for elimination (strongest first):
  - ZI document DIRECT_HEADER ordinance (document-derived, structured)
  - Known CPIO overlay reference (official source, static)
  - SA suffix (structural pattern)
  - ZI item profile text mention (profile text pattern)
  - BODY_MENTION from ZI documents: NOT used for elimination (too ambiguous)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from governing_docs.models import (
    AuthorityLink,
    AuthorityLinkType,
    ControlLinkResult,
    ControlType,
    LinkConfidence,
    ParcelAuthorityItem,
    ParcelProfileData,
    SiteControl,
    SiteControlRegistry,
)
from governing_docs.overlay_reference import is_known_cpio_ordinance

if TYPE_CHECKING:
    from governing_docs.zi_extractor import ZIExtractionResult


# Ordinances with -SA suffix are CPIO/specific-area ordinances
_SA_SUFFIX_PATTERN = re.compile(r"^ORD-\d+-SA", re.IGNORECASE)

# Pattern to extract ordinance numbers from ZI item text
_ORD_NUM_IN_TEXT = re.compile(r"(?:Ordinance|Ord\.?\s*#?)\s*(\d{5,7})", re.IGNORECASE)


def link_registry(
    registry: SiteControlRegistry,
    profile: ParcelProfileData | None,
    zi_extractions: list[ZIExtractionResult] | None = None,
) -> list[ControlLinkResult]:
    """Link all controls in a registry to profile authority items.

    Args:
        registry: Deduplicated site controls from discovery.
        profile: Optional ZIMAS parcel profile data.
        zi_extractions: Optional list of ZI PDF extraction results.
            When available, DIRECT_HEADER ordinance declarations from ZI
            documents are used to strengthen CPIO links and eliminate
            ordinances from D/Q candidate pools.

    Returns one ControlLinkResult per control, even if unlinked.
    """
    results = []
    for control in registry.controls:
        if profile and profile.has_authority_items:
            result = _link_control(control, profile, zi_extractions)
        else:
            result = ControlLinkResult(
                control=control,
                links=[AuthorityLink(
                    control_type=control.control_type,
                    confidence=LinkConfidence.UNLINKED,
                    rationale="No parcel profile data available.",
                )],
            )
        results.append(result)
    return results


def _link_control(
    control: SiteControl,
    profile: ParcelProfileData,
    zi_extractions: list[ZIExtractionResult] | None = None,
) -> ControlLinkResult:
    if control.control_type == ControlType.CPIO:
        return _link_cpio(control, profile, zi_extractions)
    elif control.control_type == ControlType.D_LIMITATION:
        return _link_d_limitation(control, profile, zi_extractions)
    elif control.control_type == ControlType.Q_CONDITION:
        return _link_q_condition(control, profile, zi_extractions)
    else:
        return ControlLinkResult(
            control=control,
            links=[AuthorityLink(
                control_type=control.control_type,
                confidence=LinkConfidence.UNLINKED,
                rationale=f"No linking logic for control type {control.control_type.value}.",
            )],
        )


# ── CPIO linking ─────────────────────────────────────────────────────


def _link_cpio(
    control: SiteControl,
    profile: ParcelProfileData,
    zi_extractions: list[ZIExtractionResult] | None = None,
) -> ControlLinkResult:
    """Link a CPIO control to profile authority items.

    Link types possible:
    1. DETERMINISTIC: the dedicated CPIO overlay row (openDataLink('CPIO', ...))
    2. DETERMINISTIC: ZI item with CPO/CPIO keyword (e.g. ZI-2478)
    3. DETERMINISTIC: ZI document header declares CPIO ordinance (document-derived)
    4. PROBABLE: ordinances with -SA suffix (CPIO/subarea ordinances)
    """
    result = ControlLinkResult(control=control)

    # 0. ZI document header evidence (strongest for ordinance number)
    if zi_extractions:
        cpio_zi_ords = _collect_zi_document_cpio_ordinances(zi_extractions, profile)
        if cpio_zi_ords:
            for ord_num, zi_code, zi_title in cpio_zi_ords:
                result.links.append(AuthorityLink(
                    control_type=ControlType.CPIO,
                    confidence=LinkConfidence.DETERMINISTIC,
                    zi_code=zi_code,
                    ordinance_number=ord_num,
                    rationale=(
                        f"ZI document {zi_code} declares ORDINANCE NO. {ord_num} "
                        f"in its header. ZI title: '{zi_title}'. "
                        f"Document-derived evidence (DIRECT_HEADER)."
                    ),
                ))

    # 1. Dedicated CPIO overlay items
    cpio_overlay_items = [
        item for item in profile.authority_items
        if item.mapped_control_type == ControlType.CPIO
        and item.link_type == AuthorityLinkType.OVERLAY_DISTRICT
    ]

    if cpio_overlay_items:
        primary = cpio_overlay_items[0]
        result.links.append(AuthorityLink(
            control_type=ControlType.CPIO,
            confidence=LinkConfidence.DETERMINISTIC,
            linked_items=cpio_overlay_items,
            primary_item=primary,
            overlay_name=primary.overlay_name,
            subarea=primary.subarea,
            rationale="CPIO overlay row in parcel profile (structurally labeled).",
        ))

    # 2. ZI items with CPIO/CPO keyword
    cpio_zi_items = [
        item for item in profile.zi_items
        if item.mapped_control_type == ControlType.CPIO
    ]

    if cpio_zi_items:
        primary_zi = cpio_zi_items[0]
        result.links.append(AuthorityLink(
            control_type=ControlType.CPIO,
            confidence=LinkConfidence.DETERMINISTIC,
            linked_items=cpio_zi_items,
            primary_item=primary_zi,
            zi_code=primary_zi.zi_code,
            overlay_name=primary_zi.overlay_name,
            rationale=f"ZI item {primary_zi.zi_code} references CPIO.",
        ))

    # 3. Ordinances with -SA suffix → probable CPIO ordinances
    sa_ordinances = [
        item for item in profile.authority_items
        if item.link_type == AuthorityLinkType.ORDINANCE
        and item.raw_text
        and _SA_SUFFIX_PATTERN.search(item.raw_text)
    ]

    if sa_ordinances:
        warnings = []
        if len(sa_ordinances) > 1:
            warnings.append(
                f"Multiple -SA ordinances found ({len(sa_ordinances)}). "
                f"Later ordinances may amend earlier ones."
            )
        result.links.append(AuthorityLink(
            control_type=ControlType.CPIO,
            confidence=LinkConfidence.PROBABLE,
            linked_items=sa_ordinances,
            primary_item=sa_ordinances[0],
            ordinance_number=sa_ordinances[0].ordinance_number,
            rationale=(
                "Ordinance(s) with -SA (subarea) suffix on a CPIO parcel. "
                "SA-suffixed ordinances are typically CPIO/specific-area ordinances."
            ),
            warnings=warnings,
        ))

    if not result.links:
        result.links.append(AuthorityLink(
            control_type=ControlType.CPIO,
            confidence=LinkConfidence.UNLINKED,
            rationale="No CPIO-related items found in parcel profile.",
        ))

    return result


# ── D Limitation linking ─────────────────────────────────────────────


def _link_d_limitation(
    control: SiteControl,
    profile: ParcelProfileData,
    zi_extractions: list[ZIExtractionResult] | None = None,
) -> ControlLinkResult:
    """Link a D limitation to profile authority items.

    D limitations have NO structural label in the parcel profile.
    The profile lists ordinances but doesn't say which is the D ordinance.

    Disambiguation strategy:
    1. Check ZI items for explicit "D Limitation" reference → deterministic
    2. Remove SA-suffixed ordinances (CPIO/subarea)
    3. Remove ordinances referenced by ZI items (accounted for elsewhere)
    4. If one survivor → probable
    5. If multiple survivors → candidate_set
    6. If none → unlinked
    """
    result = ControlLinkResult(control=control)

    # 1. Check ZI items for D limitation reference
    d_zi_items = [
        item for item in profile.zi_items
        if item.mapped_control_type == ControlType.D_LIMITATION
    ]

    if d_zi_items:
        primary = d_zi_items[0]
        result.links.append(AuthorityLink(
            control_type=ControlType.D_LIMITATION,
            confidence=LinkConfidence.DETERMINISTIC,
            linked_items=d_zi_items,
            primary_item=primary,
            ordinance_number=primary.ordinance_number,
            zi_code=primary.zi_code,
            rationale=f"ZI item {primary.zi_code} explicitly references D limitation.",
        ))
        return result

    # 2-4. Disambiguate ordinances (including ZI document evidence)
    candidates, eliminated, warnings = _disambiguate_ordinances(profile, zi_extractions)

    if not candidates:
        all_ordinances = [
            item for item in profile.authority_items
            if item.link_type == AuthorityLinkType.ORDINANCE
        ]
        if all_ordinances:
            result.links.append(AuthorityLink(
                control_type=ControlType.D_LIMITATION,
                confidence=LinkConfidence.UNLINKED,
                linked_items=all_ordinances,
                rationale=(
                    f"All {len(all_ordinances)} ordinance(s) eliminated by disambiguation "
                    f"(SA-suffixed or ZI-referenced). None are D limitation candidates. "
                    f"Eliminated: {', '.join(eliminated)}"
                ),
            ))
        else:
            result.links.append(AuthorityLink(
                control_type=ControlType.D_LIMITATION,
                confidence=LinkConfidence.UNLINKED,
                rationale="No ordinances found in parcel profile.",
            ))
        return result

    # 4/5. Assess remaining candidates
    if len(candidates) == 1:
        item = candidates[0]
        rationale_parts = [
            f"Single surviving ordinance (ORD-{item.ordinance_number}) after disambiguation."
        ]
        if eliminated:
            rationale_parts.append(f"Eliminated: {', '.join(eliminated)}.")
        result.links.append(AuthorityLink(
            control_type=ControlType.D_LIMITATION,
            confidence=LinkConfidence.PROBABLE,
            linked_items=candidates,
            primary_item=item,
            ordinance_number=item.ordinance_number,
            rationale=" ".join(rationale_parts),
            warnings=warnings + [
                "This is an inference — the parcel profile does not label "
                "which ordinance is the D limitation."
            ],
        ))
    else:
        cand_str = ", ".join(
            "ORD-" + (i.ordinance_number or "?") for i in candidates
        )
        rationale_parts = [
            f"{len(candidates)} ordinances remain after disambiguation."
        ]
        if eliminated:
            rationale_parts.append(f"Eliminated: {', '.join(eliminated)}.")
        rationale_parts.append(f"Candidates: {cand_str}")
        result.links.append(AuthorityLink(
            control_type=ControlType.D_LIMITATION,
            confidence=LinkConfidence.CANDIDATE_SET,
            linked_items=candidates,
            rationale=" ".join(rationale_parts),
            warnings=warnings + [
                "Multiple candidate ordinances. Manual review required to identify "
                "which ordinance is the D limitation."
            ],
        ))

    return result


# ── Q Condition linking ──────────────────────────────────────────────


def _link_q_condition(
    control: SiteControl,
    profile: ParcelProfileData,
    zi_extractions: list[ZIExtractionResult] | None = None,
) -> ControlLinkResult:
    """Link a Q condition to profile authority items.

    Same structural problem as D: no explicit label in the profile.
    Uses the same disambiguation filters.

    Strategy:
    1. Check ZI items for explicit "Q Condition" reference → deterministic
    2. Disambiguate ordinances (remove SA, remove ZI-referenced)
    3. If one survivor → probable
    4. If multiple → candidate_set (note D+Q overlap warning)
    5. If none → unlinked
    """
    result = ControlLinkResult(control=control)

    # 1. Check ZI items for Q condition reference
    q_zi_items = [
        item for item in profile.zi_items
        if item.mapped_control_type == ControlType.Q_CONDITION
    ]

    if q_zi_items:
        primary = q_zi_items[0]
        result.links.append(AuthorityLink(
            control_type=ControlType.Q_CONDITION,
            confidence=LinkConfidence.DETERMINISTIC,
            linked_items=q_zi_items,
            primary_item=primary,
            ordinance_number=primary.ordinance_number,
            zi_code=primary.zi_code,
            rationale=f"ZI item {primary.zi_code} explicitly references Q condition.",
        ))
        return result

    # 2-4. Disambiguate ordinances (same filter as D, including ZI document evidence)
    candidates, eliminated, warnings = _disambiguate_ordinances(profile, zi_extractions)

    if not candidates:
        result.links.append(AuthorityLink(
            control_type=ControlType.Q_CONDITION,
            confidence=LinkConfidence.UNLINKED,
            rationale=(
                "No non-SA, non-ZI-referenced ordinances found "
                "to serve as Q condition candidates."
            ),
        ))
        return result

    # Q always gets candidate_set (never probable) when D also exists on the parcel,
    # because D and Q share the same pool and we can't split them.
    # When Q is alone, single-candidate → probable is safe.
    extra_warnings = list(warnings)
    extra_warnings.append(
        "Q condition ordinance cannot be auto-identified from parcel profile. "
        "If D limitation also exists on this parcel, D and Q share the same "
        "candidate pool — manual review required to assign each."
    )

    if len(candidates) == 1:
        item = candidates[0]
        result.links.append(AuthorityLink(
            control_type=ControlType.Q_CONDITION,
            confidence=LinkConfidence.PROBABLE,
            linked_items=candidates,
            primary_item=item,
            ordinance_number=item.ordinance_number,
            rationale=(
                f"Single surviving ordinance (ORD-{item.ordinance_number}) after disambiguation."
                + (f" Eliminated: {', '.join(eliminated)}." if eliminated else "")
            ),
            warnings=extra_warnings,
        ))
    else:
        cand_str = ", ".join(
            "ORD-" + (i.ordinance_number or "?") for i in candidates
        )
        result.links.append(AuthorityLink(
            control_type=ControlType.Q_CONDITION,
            confidence=LinkConfidence.CANDIDATE_SET,
            linked_items=candidates,
            rationale=(
                f"{len(candidates)} ordinances remain after disambiguation. "
                f"Candidates: {cand_str}"
                + (f" Eliminated: {', '.join(eliminated)}." if eliminated else "")
            ),
            warnings=extra_warnings,
        ))

    return result


# ── Shared disambiguation logic ──────────────────────────────────────


def _disambiguate_ordinances(
    profile: ParcelProfileData,
    zi_extractions: list[ZIExtractionResult] | None = None,
) -> tuple[list[ParcelAuthorityItem], list[str], list[str]]:
    """Filter the ordinance pool to remove items that can be safely attributed
    to other controls, leaving only D/Q candidates.

    Returns:
        (candidates, eliminated_descriptions, warnings)
    """
    all_ordinances = [
        item for item in profile.authority_items
        if item.link_type == AuthorityLinkType.ORDINANCE
    ]

    if not all_ordinances:
        return [], [], []

    eliminated: list[str] = []
    warnings: list[str] = []

    # Step 1: Remove SA-suffixed ordinances (CPIO/subarea)
    sa_items = [
        item for item in all_ordinances
        if item.raw_text and _SA_SUFFIX_PATTERN.search(item.raw_text)
    ]
    remaining = [item for item in all_ordinances if item not in sa_items]
    for item in sa_items:
        eliminated.append(f"ORD-{item.ordinance_number or '?'} (SA suffix → CPIO)")

    # Step 2: Remove ordinances referenced by ZI item text
    # If a ZI item mentions a specific ordinance number, that ordinance is
    # accounted for by the ZI item's context and can be safely excluded.
    zi_referenced_ords = _collect_zi_referenced_ordinance_numbers(profile)
    if zi_referenced_ords:
        zi_eliminated = []
        kept = []
        for item in remaining:
            if item.ordinance_number and item.ordinance_number in zi_referenced_ords:
                zi_eliminated.append(item)
                zi_context = zi_referenced_ords[item.ordinance_number]
                eliminated.append(
                    f"ORD-{item.ordinance_number} (referenced by {zi_context})"
                )
            else:
                kept.append(item)
        remaining = kept
        if zi_eliminated:
            warnings.append(
                f"{len(zi_eliminated)} ordinance(s) eliminated because they are "
                f"explicitly referenced by ZI items (not D/Q-specific)."
            )

    # Step 3: Remove ordinances that match known CPIO implementation ordinances
    # These are confirmed from official planning.lacity.gov overlay pages.
    # NOTE: removing a CPIO ordinance from the D pool does NOT mean D provisions
    # aren't in that ordinance — they often are. It means the ordinance is
    # primarily the CPIO ordinance, and D provisions should be sought there.
    cpio_eliminated = []
    kept = []
    for item in remaining:
        if item.ordinance_number:
            cpio_district = is_known_cpio_ordinance(item.ordinance_number)
            if cpio_district:
                cpio_eliminated.append(item)
                eliminated.append(
                    f"ORD-{item.ordinance_number} (known CPIO ordinance for {cpio_district})"
                )
            else:
                kept.append(item)
        else:
            kept.append(item)
    remaining = kept
    if cpio_eliminated:
        warnings.append(
            f"{len(cpio_eliminated)} ordinance(s) identified as known CPIO implementation "
            f"ordinance(s) and removed from D/Q candidate pool. NOTE: D/Q provisions "
            f"may be embedded in the CPIO ordinance — check the CPIO document."
        )

    # Step 4: Remove ordinances declared in ZI document headers for non-D/Q ZI items
    # This is the strongest document-derived evidence: the ZI PDF itself declares
    # "ORDINANCE NO. {num}" in its header, identifying that ordinance as belonging
    # to the ZI's control domain. Only DIRECT_HEADER confidence is used.
    if zi_extractions:
        zi_doc_ords = _collect_zi_document_header_ordinances(zi_extractions, profile)
        if zi_doc_ords:
            doc_eliminated = []
            kept = []
            for item in remaining:
                if item.ordinance_number and item.ordinance_number in zi_doc_ords:
                    doc_eliminated.append(item)
                    zi_desc = zi_doc_ords[item.ordinance_number]
                    eliminated.append(
                        f"ORD-{item.ordinance_number} (declared in ZI document header: {zi_desc})"
                    )
                else:
                    kept.append(item)
            remaining = kept
            if doc_eliminated:
                warnings.append(
                    f"{len(doc_eliminated)} ordinance(s) eliminated by ZI document header "
                    f"evidence (DIRECT_HEADER). These ordinances are declared as belonging "
                    f"to specific ZI items, not D/Q controls. NOTE: D/Q provisions may "
                    f"still be embedded in these ordinances."
                )

    if len(eliminated) > 0 and len(remaining) < len(all_ordinances):
        warnings.append(
            f"Disambiguation: {len(all_ordinances)} total ordinances → "
            f"{len(eliminated)} eliminated → {len(remaining)} candidate(s) remaining."
        )

    return remaining, eliminated, warnings


def _collect_zi_referenced_ordinance_numbers(
    profile: ParcelProfileData,
) -> dict[str, str]:
    """Scan ZI item texts for explicit ordinance number references.

    Returns a dict mapping ordinance_number → ZI description (for provenance).

    Only collects ordinance numbers that appear in ZI items that are NOT
    mapped to D_LIMITATION or Q_CONDITION (those are the controls we're
    trying to disambiguate, so their references should not eliminate candidates).
    """
    result: dict[str, str] = {}

    for zi_item in profile.zi_items:
        # Skip ZI items that are mapped to D or Q — those references
        # are exactly what we're looking for, not what we want to eliminate
        if zi_item.mapped_control_type in (
            ControlType.D_LIMITATION,
            ControlType.Q_CONDITION,
        ):
            continue

        text = zi_item.raw_text or zi_item.zi_title or ""
        for m in _ORD_NUM_IN_TEXT.finditer(text):
            ord_num = m.group(1)
            zi_desc = f"ZI {zi_item.zi_code or '?'}: {zi_item.zi_title or text[:50]}"
            result[ord_num] = zi_desc

    return result


def _collect_zi_document_header_ordinances(
    zi_extractions: list[ZIExtractionResult],
    profile: ParcelProfileData,
) -> dict[str, str]:
    """Collect ordinance numbers declared in ZI document headers.

    Only uses DIRECT_HEADER confidence (the "ORDINANCE NO." line in the header).
    Only collects from ZI items that are NOT mapped to D_LIMITATION or Q_CONDITION.

    Returns a dict mapping ordinance_number → description (for provenance).
    """
    result: dict[str, str] = {}

    # Build a lookup of ZI code → mapped control type from profile
    zi_control_map: dict[str, ControlType | None] = {}
    if profile:
        for zi_item in profile.zi_items:
            if zi_item.zi_code:
                code = zi_item.zi_code.replace("ZI-", "").replace("ZI", "")
                zi_control_map[code] = zi_item.mapped_control_type

    for extraction in zi_extractions:
        code = extraction.zi_code.replace("ZI", "")
        mapped_type = zi_control_map.get(code)

        # Skip ZI items that are mapped to D or Q
        if mapped_type in (ControlType.D_LIMITATION, ControlType.Q_CONDITION):
            continue

        # Only use header-declared ordinance numbers
        if extraction.header_ordinance_number:
            ord_num = extraction.header_ordinance_number
            zi_title = extraction.header_title or extraction.zi_code
            desc = (
                f"ZI document {extraction.zi_code} header declares "
                f"ORDINANCE NO. {ord_num} ('{zi_title}')"
            )
            result[ord_num] = desc

    return result


def _collect_zi_document_cpio_ordinances(
    zi_extractions: list[ZIExtractionResult],
    profile: ParcelProfileData,
) -> list[tuple[str, str, str]]:
    """Find ordinance numbers from ZI documents that are CPIO-mapped.

    Returns list of (ordinance_number, zi_code, zi_title) tuples
    for ZI items that are mapped to CPIO and declare an ordinance
    in their document header.
    """
    results: list[tuple[str, str, str]] = []

    cpio_zi_codes: set[str] = set()
    if profile:
        for zi_item in profile.zi_items:
            if zi_item.mapped_control_type == ControlType.CPIO and zi_item.zi_code:
                code = zi_item.zi_code.replace("ZI-", "").replace("ZI", "")
                cpio_zi_codes.add(code)

    for extraction in zi_extractions:
        code = extraction.zi_code.replace("ZI", "")
        if code in cpio_zi_codes and extraction.header_ordinance_number:
            zi_title = extraction.header_title or extraction.zi_code
            results.append((
                extraction.header_ordinance_number,
                extraction.zi_code,
                zi_title,
            ))

    return results
