"""Application settings and API endpoint configuration."""

from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_CACHE_DIR = PROJECT_ROOT / "ingest" / "raw_cache"

# ZIMAS ArcGIS REST API
ZIMAS_BASE_URL = "https://zimas.lacity.org/arcgis/rest/services/zma/zimas/MapServer"
ZIMAS_SPATIAL_REF = 2229  # CA State Plane Zone 5 (feet)

# ZIMAS layer IDs
ZIMAS_LAYERS = {
    "zoning": 1102,
    "zoning_ch1a": 1101,
    "gp_land_use": 1202,
    "gp_ch1a": 1201,
    "toc": 1400,
    "ab2097": 1500,
    "coastal": 1600,
    "dedication_waiver": 1800,
    "community_plan": 103,
    "council_district": 102,
    "parcels": 105,
}

# Geocoding
GEOCODER_USER_AGENT = "KFA-GSheet-Calc/1.0"
GEOCODER_MIN_DELAY_SEC = 1.0  # Nominatim requires max 1 req/sec

# Tool metadata
TOOL_VERSION = "1.0.0"
DISCLAIMER = (
    "Preliminary internal analysis only. "
    "Not for final code determination without professional review."
)
