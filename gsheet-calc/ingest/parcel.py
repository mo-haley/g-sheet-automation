"""GeoHub parcel data extraction from ZIMAS identify results."""

from models.issue import ReviewIssue
from models.site import DataSource


def extract_parcel_data(
    identify_results: dict,
) -> tuple[dict, list[DataSource], list[ReviewIssue]]:
    """Extract parcel attributes from ZIMAS identify response.

    Returns:
        Tuple of (parcel_attrs dict, data_sources list, issues list).
    """
    attrs: dict = {}
    sources: list[DataSource] = []
    issues: list[ReviewIssue] = []

    results = identify_results.get("results", [])

    # Find parcel layer result (layer 105)
    parcel_results = [r for r in results if r.get("layerId") == 105]

    if not parcel_results:
        issues.append(
            ReviewIssue(
                id="INGEST-PARCEL-001",
                category="ingest",
                severity="high",
                title="No parcel data returned from ZIMAS",
                description="ZIMAS identify did not return parcel layer (105) results at this location.",
                affected_fields=["lot_area_sf", "apn", "parcel_geometry"],
                suggested_review_role="planner",
                blocking=True,
            )
        )
        return attrs, sources, issues

    if len(parcel_results) > 1:
        issues.append(
            ReviewIssue(
                id="INGEST-PARCEL-002",
                category="ingest",
                severity="critical",
                title="Multiple parcels matched at query point",
                description=(
                    f"{len(parcel_results)} parcel features returned. "
                    "Cannot determine which parcel is the project site without manual confirmation."
                ),
                affected_fields=["lot_area_sf", "apn", "parcel_geometry", "multiple_parcels"],
                suggested_review_role="planner",
                blocking=True,
            )
        )
        attrs["multiple_parcels"] = True
        attrs["parcel_count"] = len(parcel_results)

    parcel = parcel_results[0]
    parcel_attrs = parcel.get("attributes", {})

    # Extract APN
    for key in ("AIN", "APN", "PARCEL_ID", "PIN"):
        if key in parcel_attrs and parcel_attrs[key]:
            attrs["apn"] = str(parcel_attrs[key])
            sources.append(
                DataSource(
                    field="apn",
                    source="ZIMAS parcel layer 105",
                    raw_reference=key,
                    confidence="auto_review",
                )
            )
            break

    # Extract lot area
    for key in ("SHAPE_Area", "Shape_Area", "LOT_AREA", "AREA_SF"):
        if key in parcel_attrs and parcel_attrs[key]:
            attrs["lot_area_sf"] = float(parcel_attrs[key])
            sources.append(
                DataSource(
                    field="lot_area_sf",
                    source="ZIMAS parcel layer 105",
                    raw_reference=key,
                    confidence="auto_review",
                    notes="ZIMAS-reported lot area. Verify against survey if available.",
                )
            )
            break

    # Extract geometry if present
    geometry = parcel.get("geometry")
    if geometry:
        attrs["parcel_geometry"] = geometry

    return attrs, sources, issues
