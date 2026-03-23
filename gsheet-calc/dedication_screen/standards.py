"""Street designation standards lookup — v0 convenience table.

Source: Los Angeles Mobility Plan 2035 / Complete Streets Design Guide.

IMPORTANT: This is a v0 convenience table with approximate standard
right-of-way widths for common Mobility Plan 2035 street designations.
Values should be verified against the published design guide before
permit-level use. Street name alone is NOT sufficient to determine
designation class in all cases — this table is for early screening only.

Entry format:
    designation_class -> StandardEntry(
        standard_row_ft,
        is_range,
        range_min_ft,  # only if is_range
        range_max_ft,  # only if is_range
    )

When is_range is True, standard_row_ft is the midpoint used for
screening. The range endpoints are preserved for disclosure.
"""

from __future__ import annotations

from dataclasses import dataclass


STANDARDS_TABLE_VERSION = "mobility_plan_2035_v0_2026-03"


@dataclass(frozen=True)
class StandardEntry:
    standard_row_ft: float
    is_range: bool = False
    range_min_ft: float | None = None
    range_max_ft: float | None = None


# -- Designation -> standard ROW width ----------------------------------------
#
# Approximate values from Mobility Plan 2035.
# Where the design guide specifies a range, midpoint is used and
# is_range=True signals reduced confidence.

DESIGNATION_STANDARDS: dict[str, StandardEntry] = {
    "Boulevard I": StandardEntry(standard_row_ft=100.0),
    "Boulevard II": StandardEntry(standard_row_ft=80.0),
    "Avenue I": StandardEntry(standard_row_ft=100.0),
    "Avenue II": StandardEntry(standard_row_ft=86.0),
    "Avenue III": StandardEntry(standard_row_ft=72.0),
    "Collector": StandardEntry(standard_row_ft=66.0),
    "Industrial Collector": StandardEntry(standard_row_ft=66.0),
    "Local Street - Standard": StandardEntry(standard_row_ft=60.0),
    "Local Street - Limited": StandardEntry(standard_row_ft=50.0),
    # Modified variants — reduced ROW
    "Boulevard II Modified": StandardEntry(
        standard_row_ft=76.0,
        is_range=True,
        range_min_ft=72.0,
        range_max_ft=80.0,
    ),
    "Avenue II Modified": StandardEntry(
        standard_row_ft=80.0,
        is_range=True,
        range_min_ft=76.0,
        range_max_ft=86.0,
    ),
    "Avenue III Modified": StandardEntry(
        standard_row_ft=66.0,
        is_range=True,
        range_min_ft=60.0,
        range_max_ft=72.0,
    ),
}


def lookup_standard(designation_class: str) -> StandardEntry | None:
    """Look up standard dimensions for a designation class.

    Returns None if designation is not in the v0 table.
    """
    return DESIGNATION_STANDARDS.get(designation_class)


# -- Street name convenience lookup -------------------------------------------
#
# v0 convenience only. Maps well-known LA street names to their Mobility
# Plan 2035 designation. This is NOT authoritative — designation can vary
# by segment, and this table covers only common unambiguous cases.
#
# Confidence for any match from this table is capped at MEDIUM.

STREET_NAME_DESIGNATIONS: dict[str, str] = {
    "Wilshire Blvd": "Boulevard I",
    "Sunset Blvd": "Boulevard I",
    "Santa Monica Blvd": "Boulevard I",
    "Olympic Blvd": "Boulevard II",
    "Venice Blvd": "Boulevard II",
    "Pico Blvd": "Boulevard II",
    "Washington Blvd": "Avenue I",
    "La Brea Ave": "Avenue II",
    "Western Ave": "Avenue II",
    "Vermont Ave": "Avenue II",
    "Normandie Ave": "Avenue III",
    "Hoover St": "Collector",
}


def lookup_designation_by_street_name(street_name: str) -> str | None:
    """Look up designation by street name from v0 convenience table.

    Returns None if street name is not in the table.
    Matches are case-sensitive and must be exact.
    """
    return STREET_NAME_DESIGNATIONS.get(street_name)
