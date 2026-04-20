from __future__ import annotations

from enum import Enum


class EntitlementPath(str, Enum):
    BASE_ZONING = "base_zoning"
    DENSITY_BONUS = "density_bonus"
    AFFORDABLE_100 = "affordable_100"
