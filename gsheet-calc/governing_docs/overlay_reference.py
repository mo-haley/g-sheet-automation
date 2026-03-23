"""Static overlay reference data from official LA City Planning sources.

Maps CPIO district names to their known ordinance numbers, sourced from:
    https://planning.lacity.gov/plans-policies/overlays/{district-slug}

This is a manually-maintained lookup. It is NOT a database query.
Entries are added only when confirmed from official planning.lacity.gov pages.

Purpose: if an ordinance from a parcel's candidate pool matches a known CPIO
ordinance, it can be reclassified from D/Q candidate to CPIO ordinance,
narrowing the disambiguation pool.

IMPORTANT: A single ordinance can contain BOTH CPIO provisions AND D limitation
provisions. Removing it from the D candidate pool means "this ordinance is
primarily the CPIO ordinance" — not "this ordinance has nothing to do with D."
The D provisions may be embedded in the same document. This distinction matters
for downstream document retrieval: the D-relevant content is in the CPIO
ordinance, not in a separate D-only ordinance.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CPIOReference:
    """Known CPIO district reference from official sources."""
    district_name: str
    ordinance_number: str | None
    source_url: str
    subareas: list[str]
    notes: str | None = None


# ── Known CPIO districts ─────────────────────────────────────────────
# Sourced from https://planning.lacity.gov/plans-policies/overlays
# Ordinance numbers confirmed from individual overlay pages where available.

KNOWN_CPIO_DISTRICTS: dict[str, CPIOReference] = {
    "san pedro": CPIOReference(
        district_name="San Pedro",
        ordinance_number="185539",
        source_url="https://planning.lacity.gov/plans-policies/overlays/san-pedro",
        subareas=[
            "Central Commercial",
            "Coastal Commercial",
            "Industrial",
            "Multi-Family Residential",
            "Regional Commercial",
        ],
        notes="Confirmed from overlay page: 'Ordinance No. 185539'",
    ),
    "downtown": CPIOReference(
        district_name="Downtown",
        ordinance_number=None,  # Not listed on overlay page
        source_url="https://planning.lacity.gov/plans-policies/overlays/downtown-cpio",
        subareas=[
            "Community Benefits Program",
            "Bunker Hill",
            "Civic Center",
            "Historic Preservation",
        ],
        notes="Ordinance number not shown on overlay page. Ordinance PDF available but not parsed.",
    ),
    "hollywood": CPIOReference(
        district_name="Hollywood",
        ordinance_number=None,  # Not yet verified
        source_url="https://planning.lacity.gov/plans-policies/overlays/hollywood-cpio",
        subareas=[
            "Regional Center",
            "Corridors",
            "Multi-Family Residential",
            "Character Residential",
        ],
        notes="Ordinance number not yet verified from overlay page.",
    ),
    # Additional districts exist but ordinance numbers not yet confirmed:
    # - South Los Angeles
    # - Southeast Los Angeles
    # - Sylmar
    # - West Adams-Baldwin Hills-Leimert
    # - Westchester-Playa del Rey
}


def lookup_cpio_ordinance(cpio_name: str) -> CPIOReference | None:
    """Look up a CPIO district by name.

    Matches case-insensitively against the district name.
    Returns None if the district is not in the known reference set.
    """
    key = cpio_name.lower().replace(" cpio", "").strip()
    return KNOWN_CPIO_DISTRICTS.get(key)


def is_known_cpio_ordinance(ordinance_number: str) -> str | None:
    """Check if an ordinance number is a known CPIO implementation ordinance.

    Returns the CPIO district name if matched, None otherwise.
    """
    for ref in KNOWN_CPIO_DISTRICTS.values():
        if ref.ordinance_number and ref.ordinance_number == ordinance_number:
            return ref.district_name
    return None
