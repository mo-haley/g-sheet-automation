from __future__ import annotations

"""Flask web interface for the KFA G-Sheet Calc Tool."""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

# Ensure gsheet-calc root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, render_template, request, send_file, redirect, url_for

from analysis.app_orchestrator import run_app
from ingest.geocoder import Geocoder
from ingest.multi_parcel import resolve_multi_parcel_site
from ingest.parser import parse_zimas_response
from ingest.zimas import ZIMASClient
from models.issue import ReviewIssue
from models.project import AffordabilityPlan, OccupancyArea, Project, UnitType
from output.excel import generate_workbook
from web.snapshot_view import build_snapshot_view

TOOL_VERSION = "1.1.0-demo"
CODE_CYCLE = "2025 CBC / LAMC Rev. 7"

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "kfa-demo-key-not-for-production")

# In-memory store for recent analysis results (keyed by run_id)
_results_cache: dict[str, dict] = {}


def _int_or(val, default=0):
    try:
        return int(val) if val else default
    except (ValueError, TypeError):
        return default


def _float_or(val, default=0.0):
    try:
        return float(val) if val else default
    except (ValueError, TypeError):
        return default


def _int_or_none(val):
    try:
        return int(val) if val else None
    except (ValueError, TypeError):
        return None


def _build_project_from_form(form) -> Project:
    """Parse form data into a Project model."""
    studios = _int_or(form.get("studios"))
    one_br = _int_or(form.get("one_br"))
    two_br = _int_or(form.get("two_br"))
    three_br = _int_or(form.get("three_br"))
    total_units = studios + one_br + two_br + three_br

    unit_mix = []
    if studios > 0:
        unit_mix.append(UnitType(label="Studio", count=studios, habitable_rooms=2, bedrooms=0, avg_area_sf=450))
    if one_br > 0:
        unit_mix.append(UnitType(label="1BR", count=one_br, habitable_rooms=3, bedrooms=1, avg_area_sf=650))
    if two_br > 0:
        unit_mix.append(UnitType(label="2BR", count=two_br, habitable_rooms=4, bedrooms=2, avg_area_sf=850))
    if three_br > 0:
        unit_mix.append(UnitType(label="3BR", count=three_br, habitable_rooms=5, bedrooms=3, avg_area_sf=1100))

    occupancy_areas = []
    com_type = form.get("com_type", "")
    com_area = _float_or(form.get("com_area"))
    if com_type and com_area > 0:
        occ_group_map = {"retail": "M", "office": "B", "restaurant": "A-2", "medical_office": "B"}
        occupancy_areas.append(OccupancyArea(
            occupancy_group=occ_group_map.get(com_type, "M"),
            use_description=com_type,
            area_sf=com_area,
            floor_level=form.get("com_floor", "1"),
        ))

    eli = _float_or(form.get("eli_pct"))
    vli = _float_or(form.get("vli_pct"))
    li = _float_or(form.get("li_pct"))
    moderate = _float_or(form.get("moderate_pct"))
    has_affordability = (eli + vli + li + moderate) > 0
    affordability = AffordabilityPlan(
        eli_pct=eli, vli_pct=vli, li_pct=li, moderate_pct=moderate,
        market_pct=max(0, 100 - eli - vli - li - moderate),
    ) if has_affordability else None

    # Parking strategy
    parking_strategy = form.get("parking_strategy", "none")
    parking_subterranean = parking_strategy == "subterranean"

    hundred_pct_affordable = True if form.get("hundred_pct_affordable") else False

    return Project(
        project_name=form.get("project_name") or f"Web Analysis: {form.get('address', '')}",
        application_date=form.get("application_date", ""),
        total_units=total_units,
        unit_mix=unit_mix,
        occupancy_areas=occupancy_areas,
        parking_spaces_total=_int_or_none(form.get("parking_total")),
        parking_subterranean=parking_subterranean or None,
        dedication_street_ft=_float_or(form.get("ded_street")),
        dedication_alley_ft=_float_or(form.get("ded_alley")),
        corner_cuts_sf=_float_or(form.get("corner_cuts")),
        alley_adjacent=form.get("alley_adjacent") == "on",
        alley_width_ft=_float_or(form.get("alley_width")),
        alley_frontage_length_ft=_float_or(form.get("alley_frontage")),
        affordability=affordability,
        prevailing_wage_committed=True if form.get("prevailing_wage") == "true" else None,
        commercial_corridor_frontage=True if form.get("commercial_corridor") == "true" else None,
        hundred_pct_affordable=hundred_pct_affordable,
    )


def _get_policy_path(form) -> str:
    """Determine the selected policy path from the form."""
    return form.get("policy_path", "base_zoning")


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", error=None)


@app.route("/run", methods=["POST"])
def run_analysis():
    """Full feasibility analysis using the modular pipeline.

    Runs the same modular pipeline as /run-address (density, FAR, parking,
    setback, ED1, zimas_linked_docs) but with full project inputs (unit mix,
    affordability, policy path).

    Legacy features intentionally deferred to a future pass:
      - Calc ledger (step-by-step calculation detail)
      - Governing logic narrative
      - Excel export
    """
    address = request.form.get("address", "").strip()
    if not address:
        return render_template("index.html", error="Address is required.")

    try:
        project = _build_project_from_form(request.form)
    except Exception as e:
        return render_template("index.html", error=f"Invalid project inputs: {e}")

    # Geocode
    try:
        geocoder = Geocoder()
        coords = geocoder.geocode(address)
    except Exception as e:
        return render_template("index.html", error=f"Geocoding failed: {e}")

    if coords is None:
        return render_template("index.html", error=f"Could not geocode address: '{address}'. Try a more specific LA City address.")

    # ZIMAS
    try:
        zimas = ZIMASClient()
        identify_data = zimas.identify(coords[0], coords[1])
        site, zoning_parse, ingest_issues = parse_zimas_response(
            address, identify_data, coordinates=coords, pull_timestamp=zimas.pull_timestamp
        )
    except Exception as e:
        return render_template("index.html", error=f"ZIMAS query failed: {e}")

    # Apply lot area override if provided
    lot_area_override = _float_or(request.form.get("lot_area_override")) or None
    if lot_area_override:
        site.survey_lot_area_sf = lot_area_override
        site.lot_area_sf = lot_area_override

    # Policy path label
    policy_path = _get_policy_path(request.form)
    policy_labels = {
        "base_zoning": "Base Zoning Only",
        "toc": "Transit Oriented Communities (TOC)",
        "density_bonus": "State Density Bonus",
        "affordable_100": "100% Affordable Housing",
    }

    # Run the modular pipeline (density, FAR, parking, setback, ED1, zimas_linked_docs)
    project_id = hashlib.md5(f"{address}-{id(project)}".encode()).hexdigest()[:12]
    app_result = run_app(site, project, project_id=project_id)

    # Cache results for Excel export
    _results_cache[project_id] = {
        "address": address,
        "site": site,
        "project": project,
        "app_result": app_result,
    }

    # Build view model with project context
    vm = build_snapshot_view(
        site, app_result,
        project=project,
        policy_path_label=policy_labels.get(policy_path, policy_path),
    )

    # Debug trace — only in dev mode with ?debug=1
    debug_trace = None
    if app.debug and request.args.get("debug") == "1":
        from web.snapshot_debug import build_debug_trace
        debug_trace = build_debug_trace(site, app_result)

    return render_template(
        "snapshot.html",
        vm=vm,
        run_id=project_id,
        tool_version=TOOL_VERSION,
        code_cycle=CODE_CYCLE,
        debug_trace=debug_trace,
    )


@app.route("/run-address", methods=["POST"])
def run_address_only():
    """Address-only feasibility snapshot using the new modular pipeline."""
    address = request.form.get("address", "").strip()
    if not address:
        return render_template("index.html", error="Address is required.")

    # Parse optional APN list
    apn_raw = request.form.get("apn_list", "").strip()
    apn_list = [a.strip() for a in apn_raw.split(",") if a.strip()] if apn_raw else []

    zimas = ZIMASClient()
    ingest_issues = []

    if apn_list:
        # Multi-parcel path: resolve site from user-supplied APNs
        try:
            site, multi_issues = resolve_multi_parcel_site(apn_list, address, zimas)
            ingest_issues = multi_issues
        except Exception as e:
            return render_template("index.html", error=f"Multi-parcel ZIMAS query failed: {e}")
    else:
        # Standard address-only path
        try:
            geocoder = Geocoder()
            coords = geocoder.geocode(address)
        except Exception as e:
            return render_template("index.html", error=f"Geocoding failed: {e}")

        if coords is None:
            return render_template("index.html", error=f"Could not geocode address: '{address}'. Try a more specific LA City address.")

        try:
            identify_data = zimas.identify(coords[0], coords[1])
            site, zoning_parse, ingest_issues = parse_zimas_response(
                address, identify_data, coordinates=coords, pull_timestamp=zimas.pull_timestamp
            )

            # Surface identify-level warnings
            if not zimas.identify_status.critical_layers_resolved:
                ingest_issues.append(ReviewIssue(
                    id="INGEST-IDENTIFY-001",
                    category="ingest",
                    severity="critical",
                    title="ZIMAS parcel/zoning layers unresolved after retry",
                    description=(
                        "ZIMAS identify did not return parcel or zoning data even at wide "
                        "search tolerance (~100 ft). Downstream calculations that depend on "
                        "zone, lot area, or parcel geometry will be missing or unreliable. "
                        "Verify the address is within LA City ZIMAS coverage."
                    ),
                    affected_fields=["zone", "lot_area_sf", "apn", "parcel_geometry"],
                    suggested_review_role="planner",
                    blocking=True,
                ))
            elif zimas.identify_status.used_wide_tolerance:
                ingest_issues.append(ReviewIssue(
                    id="INGEST-IDENTIFY-002",
                    category="ingest",
                    severity="medium",
                    title="ZIMAS data resolved via wide search tolerance",
                    description=(
                        "Standard tolerance (~50 ft) missed parcel/zoning layers. Data was "
                        "resolved at wider tolerance (~100 ft). This commonly occurs for "
                        "addresses on major arterials. The matched parcel should be verified."
                    ),
                    affected_fields=["zone", "lot_area_sf", "apn"],
                    suggested_review_role="planner",
                ))
        except Exception as e:
            return render_template("index.html", error=f"ZIMAS query failed: {e}")

    # Build a minimal empty project for the pipeline
    project = Project(
        project_name=f"Address Screening: {address}",
        total_units=0,
    )

    # Run the modular pipeline
    project_id = hashlib.md5(address.encode()).hexdigest()[:12]

    app_result = run_app(site, project, project_id=project_id)

    # Build view model
    vm = build_snapshot_view(site, app_result)

    # Debug trace — only in dev mode with ?debug=1
    debug_trace = None
    if app.debug and request.args.get("debug") == "1":
        from web.snapshot_debug import build_debug_trace
        debug_trace = build_debug_trace(site, app_result)

    return render_template(
        "snapshot.html",
        vm=vm,
        tool_version=TOOL_VERSION,
        code_cycle=CODE_CYCLE,
        debug_trace=debug_trace,
    )


@app.route("/export/<run_id>", methods=["GET"])
def export_excel(run_id):
    """Download Excel workbook for a completed analysis run."""
    cached = _results_cache.get(run_id)
    if not cached:
        return redirect(url_for("index"))

    site = cached["site"]
    project = cached["project"]
    app_result = cached["app_result"]
    address = cached["address"]

    # Check if the parking module used a default unit mix assumption
    parking_default_mix = False
    parking_mr = next(
        (mr for mr in app_result.module_results if mr.module == "parking"), None
    )
    if parking_mr:
        baseline = parking_mr.module_payload.get("full_output", {}).get("baseline_parking", {})
        parking_default_mix = bool(baseline.get("used_default_unit_mix_assumption", False))

    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in address)[:60]
    tmp_dir = Path(tempfile.mkdtemp())
    excel_path = tmp_dir / f"{safe_name}.xlsx"

    generate_workbook(
        site=site,
        project=project,
        area_results=[],
        density_results=[],
        far_results=[],
        height_results=[],
        parking_results=[],
        open_space_results=[],
        loading_results=[],
        scenarios=[],
        issues=[],
        output_path=excel_path,
        parking_used_default_unit_mix=parking_default_mix,
    )

    return send_file(
        str(excel_path),
        as_attachment=True,
        download_name=f"{safe_name}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=True, host="0.0.0.0", port=port)
