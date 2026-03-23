"""Workbook styling and layout constants."""

from datetime import datetime

from config.code_version import CODE_CYCLE, TOOL_VERSION
from config.settings import DISCLAIMER

FOOTER_TEXT = (
    f"{DISCLAIMER} | "
    f"Code cycle: {CODE_CYCLE['label']} | "
    f"Tool version: {TOOL_VERSION}"
)


def get_footer_with_date() -> str:
    """Return the standard footer with current date."""
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"Preliminary internal analysis only. Generated {date_str} | Code cycle: {CODE_CYCLE['label']} | Tool version: {TOOL_VERSION} | Not for final code determination without professional review."


# Style constants for openpyxl
HEADER_FILL_HEX = "2F5496"
HEADER_FONT_COLOR = "FFFFFF"
ADVISORY_FILL_HEX = "FFF2CC"
MANUAL_REVIEW_FILL_HEX = "FCE4EC"
DETERMINISTIC_FILL_HEX = "E8F5E9"
