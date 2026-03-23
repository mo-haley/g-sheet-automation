from __future__ import annotations

"""Flask web interface for the KFA G-Sheet Calc Tool."""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

# Ensure gsheet-calc root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, render_template, request, send_file, session, redirect, url_for

from analysis.app_orchestrator import run_app, build_zimas_input_from_site
from analysis.issue_register import IssueRegister
from analysis.scenarios import build_base_zoning_scenario
from calc.areas import calculate_areas
from calc.density import calculate_density
from calc.far import calculate_far
from calc.height import calculate_height
from calc.loading import calculate_loading
from calc.open_space import calculate_open_space
from calc.parking import calculate_parking
from ingest.geocoder import Geocoder
from ingest.multi_parcel import resolve_multi_parcel_site
from ingest.parser import parse_zimas_response
from ingest.zimas import ZIMASClient
from models.project import AffordabilityPlan, OccupancyArea, Project, UnitType
from output.excel import generate_workbook
from web.snapshot_view import build_snapshot_view
from rules.advisory.adaptive_reuse_stub import screen_adaptive_reuse
from rules.advisory.affordable_housing_screen import screen_100pct_affordable
from rules.advisory.density_bonus_screen import screen_density_bonus
from rules.advisory.streamlining_screen import screen_ab2011, screen_sb423
from rules.advisory.toc_screen import screen_toc

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
    )


def _get_result(results: list, name: str):
    """Find a CalcResult by name from a list."""
    for r in results:
        if r.name == name:
            return r
    return None


def _get_policy_path(form) -> str:
    """Determine the selected policy path from the form."""
    return form.get("policy_path", "base_zoning")


def _build_governing_logic(site, project, area_results, density_results, far_results, parking_results, os_results):
    """Build the governing logic narrative section."""
    logic = []

    # Density
    density_r = _get_result(density_results, "base_density")
    if density_r:
        logic.append({
            "topic": "Density",
            "governing_path": "Base Zoning (LAMC 12.22)",
            "explanation": density_r.formula,
            "steps": density_r.intermediate_steps,
            "code_section": density_r.code_section or "LAMC 12.22",
            "confidence": density_r.confidence,
            "assumptions": density_r.assumptions,
            "source": "derived",
        })
    else:
        logic.append({
            "topic": "Density",
            "governing_path": "Unresolved",
            "explanation": "Density calculation did not produce a result. Zone may not have a mapped density factor.",
            "steps": [],
            "code_section": "LAMC 12.22",
            "confidence": "low",
            "assumptions": [],
            "source": "unresolved",
        })

    # Parking
    parking_total = _get_result(parking_results, "total_parking_required")
    res_parking = _get_result(parking_results, "residential_parking_required")
    com_parking = _get_result(parking_results, "commercial_parking_required")
    if parking_total:
        parking_steps = []
        if res_parking:
            parking_steps += res_parking.intermediate_steps
        if com_parking:
            parking_steps += com_parking.intermediate_steps
        parking_steps += parking_total.intermediate_steps
        logic.append({
            "topic": "Parking",
            "governing_path": "LAMC 12.21-A.4 (base auto parking)",
            "explanation": parking_total.formula,
            "steps": parking_steps,
            "code_section": parking_total.code_section or "LAMC 12.21-A.4",
            "confidence": parking_total.confidence,
            "assumptions": parking_total.assumptions,
            "source": "derived",
        })

    # Open Space
    os_r = _get_result(os_results, "open_space_required")
    if os_r:
        logic.append({
            "topic": "Open Space",
            "governing_path": "LAMC 12.21-G",
            "explanation": os_r.formula,
            "steps": os_r.intermediate_steps,
            "code_section": os_r.code_section or "LAMC 12.21-G",
            "confidence": os_r.confidence,
            "assumptions": os_r.assumptions,
            "source": "derived",
        })

    # FAR
    far_r = _get_result(far_results, "allowable_far") or _get_result(far_results, "far_ratio")
    if far_r:
        logic.append({
            "topic": "FAR",
            "governing_path": far_r.formula or "See calc detail",
            "explanation": f"FAR ratio: {far_r.value}" if far_r.value else "FAR unresolved",
            "steps": far_r.intermediate_steps,
            "code_section": far_r.code_section or "LAMC Table 12.22-A",
            "confidence": far_r.confidence,
            "assumptions": far_r.assumptions,
            "source": "derived" if far_r.confidence != "low" else "unresolved",
        })
    else:
        logic.append({
            "topic": "FAR",
            "governing_path": "Unresolved",
            "explanation": "FAR could not be resolved. Counted floor area may not have been provided, or the zone table was not matched.",
            "steps": [],
            "code_section": "LAMC Table 12.22-A",
            "confidence": "low",
            "assumptions": ["FAR compliance requires counted floor area input"],
            "source": "unresolved",
        })

    # Lot Area adjustments
    gross = _get_result(area_results, "gross_lot_area")
    net = _get_result(area_results, "net_lot_area")
    effective = _get_result(area_results, "effective_density_area")
    if gross and net:
        adj_steps = []
        if gross:
            adj_steps += gross.intermediate_steps
        if net:
            adj_steps += net.intermediate_steps
        if effective:
            adj_steps += effective.intermediate_steps
        logic.append({
            "topic": "Lot Area Adjustments",
            "governing_path": "LAMC 12.03 (lot area definitions)",
            "explanation": f"Gross: {gross.value:,.0f} sf -> Net: {net.value:,.0f} sf" + (f" -> Effective density area: {effective.value:,.0f} sf" if effective else ""),
            "steps": adj_steps,
            "code_section": "LAMC 12.03",
            "confidence": gross.confidence,
            "assumptions": (gross.assumptions or []) + (net.assumptions or []),
            "source": "derived",
        })

    return logic


def _build_calc_ledger(area_results, density_results, far_results, parking_results, os_results, load_results, height_results):
    """Build a step-by-step calculation ledger for the detail section."""
    sections = []

    # Areas
    area_items = []
    for r in area_results:
        area_items.append({
            "label": r.name.replace("_", " ").title(),
            "value": f"{r.value:,.0f}" if isinstance(r.value, (int, float)) else str(r.value),
            "unit": r.unit,
            "formula": r.formula,
            "steps": r.intermediate_steps,
            "confidence": r.confidence,
            "code": r.code_section or "",
        })
    if area_items:
        sections.append({"title": "Lot Area Chain", "items": area_items})

    # Density
    density_items = []
    for r in density_results:
        density_items.append({
            "label": r.name.replace("_", " ").title(),
            "value": f"{r.value:,.0f}" if isinstance(r.value, (int, float)) else str(r.value),
            "unit": r.unit,
            "formula": r.formula,
            "steps": r.intermediate_steps,
            "confidence": r.confidence,
            "code": r.code_section or "",
        })
    if density_items:
        sections.append({"title": "Density", "items": density_items})

    # FAR
    far_items = []
    for r in far_results:
        val = r.value
        if isinstance(val, (int, float)):
            far_items.append({
                "label": r.name.replace("_", " ").title(),
                "value": f"{val:,.2f}" if val < 100 else f"{val:,.0f}",
                "unit": r.unit,
                "formula": r.formula,
                "steps": r.intermediate_steps,
                "confidence": r.confidence,
                "code": r.code_section or "",
            })
        else:
            far_items.append({
                "label": r.name.replace("_", " ").title(),
                "value": str(val),
                "unit": r.unit,
                "formula": r.formula,
                "steps": r.intermediate_steps,
                "confidence": r.confidence,
                "code": r.code_section or "",
            })
    if far_items:
        sections.append({"title": "Floor Area Ratio", "items": far_items})

    # Height
    height_items = []
    for r in height_results:
        height_items.append({
            "label": r.name.replace("_", " ").title(),
            "value": f"{r.value:,.0f}" if isinstance(r.value, (int, float)) else str(r.value),
            "unit": r.unit,
            "formula": r.formula,
            "steps": r.intermediate_steps,
            "confidence": r.confidence,
            "code": r.code_section or "",
        })
    if height_items:
        sections.append({"title": "Height", "items": height_items})

    # Parking
    parking_items = []
    for r in parking_results:
        if isinstance(r.value, (int, float)):
            parking_items.append({
                "label": r.name.replace("_", " ").title(),
                "value": f"{r.value:,.0f}" if r.value == int(r.value) else f"{r.value:,.1f}",
                "unit": r.unit,
                "formula": r.formula,
                "steps": r.intermediate_steps,
                "confidence": r.confidence,
                "code": r.code_section or "",
            })
    if parking_items:
        sections.append({"title": "Parking", "items": parking_items})

    # Open Space
    os_items = []
    for r in os_results:
        if isinstance(r.value, (int, float)):
            os_items.append({
                "label": r.name.replace("_", " ").title(),
                "value": f"{r.value:,.0f}",
                "unit": r.unit,
                "formula": r.formula,
                "steps": r.intermediate_steps,
                "confidence": r.confidence,
                "code": r.code_section or "",
            })
    if os_items:
        sections.append({"title": "Open Space", "items": os_items})

    # Loading
    load_items = []
    for r in load_results:
        load_items.append({
            "label": r.name.replace("_", " ").title(),
            "value": str(r.value),
            "unit": r.unit,
            "formula": r.formula,
            "steps": r.intermediate_steps,
            "confidence": r.confidence,
            "code": r.code_section or "",
        })
    if load_items:
        sections.append({"title": "Loading", "items": load_items})

    return sections


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", error=None)


@app.route("/run", methods=["POST"])
def run_analysis():
    address = request.form.get("address", "").strip()
    if not address:
        return render_template("index.html", error="Address is required.")

    try:
        project = _build_project_from_form(request.form)
    except Exception as e:
        return render_template("index.html", error=f"Invalid project inputs: {e}")

    issue_register = IssueRegister()

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
        issue_register.add_all(ingest_issues)
    except Exception as e:
        return render_template("index.html", error=f"ZIMAS query failed: {e}")

    # Apply lot area override if provided — replaces ZIMAS value so it governs all downstream calcs
    lot_area_override = _float_or(request.form.get("lot_area_override")) or None
    if lot_area_override:
        site.survey_lot_area_sf = lot_area_override
        site.lot_area_sf = lot_area_override

    # Calculations
    area_results, area_issues = calculate_areas(site, project)
    issue_register.add_all(area_issues)

    density_results, density_issues = calculate_density(site, project)
    issue_register.add_all(density_issues)

    far_results, far_issues = calculate_far(site, project)
    issue_register.add_all(far_issues)

    height_results, height_issues = calculate_height(site, project)
    issue_register.add_all(height_issues)

    parking_results, parking_issues = calculate_parking(site, project)
    issue_register.add_all(parking_issues)

    os_results, os_issues = calculate_open_space(site, project)
    issue_register.add_all(os_issues)

    load_results, load_issues = calculate_loading(site, project)
    issue_register.add_all(load_issues)

    # Advisory screens
    all_det_results = area_results + density_results + far_results + height_results
    scenarios = [
        build_base_zoning_scenario(all_det_results, issue_register.get_all()),
        screen_toc(site, project),
        screen_density_bonus(site, project),
        screen_100pct_affordable(site, project),
        screen_sb423(site, project),
        screen_ab2011(site, project),
        screen_adaptive_reuse(site, project),
    ]
    for sc in scenarios:
        issue_register.add_all(sc.issues)

    all_issues = issue_register.get_all()

    # --- Build results context ---
    policy_path = _get_policy_path(request.form)
    policy_labels = {
        "base_zoning": "Base Zoning Only",
        "toc": "Transit Oriented Communities (TOC)",
        "density_bonus": "State Density Bonus",
        "affordable_100": "100% Affordable Housing",
    }

    # Key results
    density_r = _get_result(density_results, "base_density")
    parking_total = _get_result(parking_results, "total_parking_required")
    os_r = _get_result(os_results, "open_space_required")
    height_r = _get_result(height_results, "height_limit_ft")
    loading_r = _get_result(load_results, "loading_required")

    key_results = {
        "allowable_units": density_r.value if density_r else "Unresolved",
        "allowable_units_confidence": density_r.confidence if density_r else "low",
        "density_governing": density_r.formula if density_r else "Not resolved",
        "required_parking": parking_total.value if parking_total else "Unresolved",
        "parking_confidence": parking_total.confidence if parking_total else "low",
        "open_space_required": os_r.value if os_r else "Unresolved",
        "open_space_confidence": os_r.confidence if os_r else "low",
        "height_limit": height_r.value if height_r else "Unresolved",
        "height_confidence": height_r.confidence if height_r else "low",
        "far_status": "See governing logic",
        "loading_required": loading_r.value if loading_r else "Unresolved",
        "loading_confidence": loading_r.confidence if loading_r else "low",
    }

    # Flags/triggers
    flags = []
    blocking_issues = [i for i in all_issues if i.blocking]
    if blocking_issues:
        for bi in blocking_issues:
            flags.append(f"BLOCKING: {bi.title}")
    if site.specific_plan:
        flags.append(f"Specific Plan applies: {site.specific_plan}")
    if site.q_conditions:
        flags.append(f"Q Conditions: {', '.join(site.q_conditions)}")
    if site.d_limitations:
        flags.append(f"D Limitations: {', '.join(site.d_limitations)}")
    if site.hillside_area:
        flags.append("Hillside area — additional regulations apply")
    if site.coastal_zone:
        flags.append("Coastal zone — CCC jurisdiction")

    # Governing logic
    governing_logic = _build_governing_logic(site, project, area_results, density_results, far_results, parking_results, os_results)

    # Calc ledger
    calc_ledger = _build_calc_ledger(area_results, density_results, far_results, parking_results, os_results, load_results, height_results)

    # Warnings / assumptions
    warnings = []
    for issue in all_issues:
        warnings.append({
            "severity": issue.severity,
            "title": issue.title,
            "description": issue.description,
            "category": issue.category,
            "blocking": issue.blocking,
            "status": issue.status,
        })

    # Overlays display
    overlays = []
    if site.overlay_zones:
        overlays.extend(site.overlay_zones)
    if site.specific_plan:
        overlays.append(f"SP: {site.specific_plan}")
    if site.q_conditions:
        overlays.extend([f"Q: {q}" for q in site.q_conditions])
    if site.d_limitations:
        overlays.extend([f"D: {d}" for d in site.d_limitations])

    context = {
        "site": site,
        "project": project,
        "address": address,
        "policy_path_label": policy_labels.get(policy_path, policy_path),
        "tool_version": TOOL_VERSION,
        "code_cycle": CODE_CYCLE,
        "overlays": overlays,
        "key_results": key_results,
        "flags": flags,
        "governing_logic": governing_logic,
        "calc_ledger": calc_ledger,
        "warnings": warnings,
        "scenarios": scenarios,
        "blocking_count": len(blocking_issues),
        "total_issues": len(all_issues),
    }

    # Cache results for Excel download
    run_id = hashlib.md5(f"{address}-{id(context)}".encode()).hexdigest()[:12]
    _results_cache[run_id] = {
        "site": site,
        "project": project,
        "area_results": area_results,
        "density_results": density_results,
        "far_results": far_results,
        "height_results": height_results,
        "parking_results": parking_results,
        "os_results": os_results,
        "load_results": load_results,
        "scenarios": scenarios,
        "all_issues": all_issues,
        "address": address,
    }
    context["run_id"] = run_id

    return render_template("results.html", **context)


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

    address = cached["address"]
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in address)[:60]
    tmp_dir = Path(tempfile.mkdtemp())
    excel_path = tmp_dir / f"{safe_name}.xlsx"

    generate_workbook(
        site=cached["site"],
        project=cached["project"],
        area_results=cached["area_results"],
        density_results=cached["density_results"],
        far_results=cached["far_results"],
        height_results=cached["height_results"],
        parking_results=cached["parking_results"],
        open_space_results=cached["os_results"],
        loading_results=cached["load_results"],
        scenarios=cached["scenarios"],
        issues=cached["all_issues"],
        output_path=excel_path,
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
