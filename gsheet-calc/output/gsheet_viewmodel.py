"""G010 Project Information viewmodel.

Transforms Site + Project + AppResult into the structured dict consumed
by gsheet.html. Gracefully handles missing data — returns "—" / "Pending"
rather than raising.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from models.project import Project
from models.result_common import AppResult, ModuleResult
from models.site import Site
from rules.deterministic.setbacks import get_setback_results

_CODES_PATH = Path(__file__).resolve().parents[1] / "data" / "building_codes.json"


# ── helpers ──────────────────────────────────────────────────────────────────

def _or(val: Any, fallback: str = "—") -> str:
    if val is None or val == "" or val == []:
        return fallback
    if isinstance(val, float):
        return f"{val:,.2f}"
    return str(val)


def _int_or(val: Any, fallback: str = "—") -> str:
    if val is None:
        return fallback
    try:
        return str(int(val))
    except (TypeError, ValueError):
        return fallback


def _sf(val: Any, fallback: str = "Not entered") -> str:
    if val is None:
        return fallback
    try:
        return f"{float(val):,.0f} sf"
    except (TypeError, ValueError):
        return fallback


def _by_module(app: AppResult, name: str) -> ModuleResult | None:
    for mr in app.module_results:
        if mr.module == name:
            return mr
    return None


def _load_building_codes() -> list[dict]:
    try:
        with open(_CODES_PATH) as f:
            return json.load(f).get("codes", [])
    except Exception:
        return []


# ── section builders ──────────────────────────────────────────────────────────

def _build_project_info(site: Site, project: Project) -> dict:
    path_labels = {
        "base_zoning": "Base Zoning",
        "density_bonus": "State Density Bonus Law",
        "affordable_100": "100% Affordable (ED1 / Track B)",
    }
    path_label = path_labels.get(project.selected_path or "", "Not selected")

    return {
        "address": _or(site.address),
        "apn": _or(site.apn),
        "zone": _or(site.zone),
        "height_district": _or(site.height_district),
        "community_plan": _or(site.community_plan_area),
        "general_plan": _or(site.general_plan_land_use),
        "specific_plan": _or(site.specific_plan),
        "lot_area_sf": _sf(site.lot_area_sf),
        "toc_tier": _int_or(site.toc_tier, fallback="N/A"),
        "ab2097_area": "Yes" if site.ab2097_area else ("No" if site.ab2097_area is False else "—"),
        "overlay_zones": ", ".join(site.overlay_zones) if site.overlay_zones else "—",
        "q_conditions": ", ".join(site.q_conditions) if site.q_conditions else "—",
        "d_limitations": ", ".join(site.d_limitations) if site.d_limitations else "—",
        "entitlement_path": path_label,
        "jurisdiction": "City of Los Angeles",
    }


def _build_unit_count(project: Project) -> dict:
    rows = []
    total = 0
    total_bedrooms = 0
    for u in project.unit_mix:
        rows.append({
            "type": u.label,
            "count": u.count,
            "bedrooms": u.bedrooms,
            "avg_sf": f"{u.avg_area_sf:,.0f}" if u.avg_area_sf else "—",
        })
        total += u.count
        total_bedrooms += u.count * u.bedrooms
    if not rows and project.total_units:
        rows.append({
            "type": "Total (mix not entered)",
            "count": project.total_units,
            "bedrooms": "—",
            "avg_sf": "—",
        })
        total = project.total_units
    return {
        "rows": rows,
        "total_units": total or "Not entered",
        "total_bedrooms": total_bedrooms or "—",
    }


def _build_far(app: AppResult, site: Site) -> dict:
    far_mr = _by_module(app, "far")
    if not far_mr:
        return {"status": "Module not run", "governing_ratio": "—", "allowable_sf": "—",
                "proposed_sf": "—", "proposed_ratio": "—", "compliance": "—", "floor_entries": []}

    p = far_mr.module_payload
    gov = p.get("governing_far", {})
    proposed = p.get("proposed", {})
    allowable = p.get("allowable", {})

    ratio = gov.get("applicable_ratio")
    ratio_str = f"{ratio}:1" if ratio is not None else "—"
    state = gov.get("state", "—")

    allowable_sf = allowable.get("governing_floor_area_sf")
    proposed_sf = proposed.get("numerator_sf")
    proposed_ratio = proposed.get("far_ratio")
    compliant = proposed.get("compliant")

    if compliant is True:
        compliance_str = "Compliant"
    elif compliant is False:
        compliance_str = "Non-compliant"
    else:
        compliance_str = "Pending"

    # Per-floor entries (if provided)
    floor_entries = []
    for entry in (p.get("proposed", {}).get("floor_entries") or []):
        floor_entries.append({
            "floor": entry.get("floor_level", ""),
            "gross_sf": f"{entry.get('gross_area_sf', 0):,.0f}",
            "counted_sf": f"{entry.get('counted_area_sf', 0):,.0f}",
            "category": entry.get("category", ""),
            "exclusion": entry.get("exclusion_reason", ""),
        })

    return {
        "governing_ratio": ratio_str,
        "governing_state": state,
        "allowable_sf": _sf(allowable_sf),
        "proposed_sf": _sf(proposed_sf, fallback="Not entered"),
        "proposed_ratio": f"{proposed_ratio:.2f}:1" if proposed_ratio is not None else "Not entered",
        "compliance": compliance_str,
        "floor_entries": floor_entries,
        "area_basis": _sf(site.lot_area_sf),
    }


def _build_open_space(app: AppResult, project: Project) -> dict:
    # Pull from calc/open_space results via module_payload if present,
    # otherwise surface what we know from the project model.
    required_sf: float | None = None
    basis_str = "—"

    # Some versions store open space in module_payload from the far/density module
    # For now, surface project-supplied provided value.
    provided_sf = project.open_space_provided_sf

    # Try to extract from module findings
    for mr in app.module_results:
        payload = mr.module_payload
        if "open_space" in payload:
            os_data = payload["open_space"]
            required_sf = os_data.get("required_sf")
            basis_str = os_data.get("basis", "—")
            break

    if required_sf is not None and provided_sf is not None:
        if provided_sf >= required_sf:
            status = "compliant"
        else:
            status = "non_compliant"
    elif required_sf is not None:
        status = "pending"
    else:
        status = "not_calculated"

    return {
        "required_sf": _sf(required_sf, fallback="Not calculated"),
        "provided_sf": _sf(provided_sf, fallback="Not entered"),
        "basis": basis_str,
        "status": status,
    }


def _build_parking(app: AppResult, project: Project, site: Site) -> dict:
    parking_mr = _by_module(app, "parking")

    res_required: float | None = None
    com_required: float | None = None
    lane = "—"
    governing_min: float | None = None

    if parking_mr:
        payload = parking_mr.module_payload
        full = payload.get("full_output", payload)  # some versions wrap, some don't
        baseline = full.get("baseline_parking", {})
        res_required = baseline.get("residential_total")
        com_total = baseline.get("commercial_total")
        if com_total:
            com_required = com_total
        parking_lane_data = full.get("parking_lane", {})
        lane_raw = parking_lane_data.get("selected", "unresolved")
        lane_labels = {
            "none": "Base Zoning (LAMC)",
            "ab2097": "AB 2097 (transit area)",
            "toc": "TOC",
            "state_db": "State Density Bonus",
            "unresolved": "Unresolved",
        }
        lane = lane_labels.get(lane_raw, lane_raw)
        governing_min = parking_lane_data.get("governing_minimum")

    accessible_mr = None
    accessible_required: float | None = None
    for mr in app.module_results:
        if mr.module_payload.get("accessible_parking_required") is not None:
            accessible_required = mr.module_payload["accessible_parking_required"]

    return {
        "lane": lane,
        "res_required": _int_or(res_required, fallback="Not calculated"),
        "com_required": _int_or(com_required, fallback="N/A"),
        "governing_minimum": _int_or(governing_min, fallback="Not calculated"),
        "auto_provided": _int_or(project.parking_auto_provided, fallback="Not entered"),
        "accessible_required": _int_or(accessible_required, fallback="Not calculated"),
        "accessible_provided": _int_or(project.parking_accessible_provided, fallback="Not entered"),
        "loading_basis": "See loading section",
    }


def _build_bike_parking(app: AppResult, project: Project) -> dict:
    long_required: int | None = None
    short_required: int | None = None

    for mr in app.module_results:
        p = mr.module_payload
        if "bike_long_term_required" in p:
            long_required = p["bike_long_term_required"]
        if "bike_short_term_required" in p:
            short_required = p["bike_short_term_required"]
        # Also check nested full_output
        full = p.get("full_output", {})
        if "bike_long_term_required" in full:
            long_required = full["bike_long_term_required"]
        if "bike_short_term_required" in full:
            short_required = full["bike_short_term_required"]

    return {
        "long_term_required": _int_or(long_required, fallback="Not calculated"),
        "long_term_provided": _int_or(project.bike_long_term_provided, fallback="Not entered"),
        "short_term_required": _int_or(short_required, fallback="Not calculated"),
        "short_term_provided": _int_or(project.bike_short_term_provided, fallback="Not entered"),
    }


def _build_ev(app: AppResult, project: Project) -> dict:
    receptacles_required: int | None = None
    evse_required: int | None = None

    for mr in app.module_results:
        p = mr.module_payload
        full = p.get("full_output", p)
        if "ev_receptacles_required" in full:
            receptacles_required = full["ev_receptacles_required"]
        if "ev_evse_required" in full:
            evse_required = full["ev_evse_required"]

    return {
        "receptacles_required": _int_or(receptacles_required, fallback="Not calculated"),
        "receptacles_provided": _int_or(project.ev_receptacles_provided, fallback="Not entered"),
        "evse_required": _int_or(evse_required, fallback="Not calculated"),
        "evse_provided": _int_or(project.ev_evse_provided, fallback="Not entered"),
        "authority": "CalGreen 2022 / LAMC",
    }


def _build_loading(app: AppResult, site: Site, project: Project) -> dict:
    loading_required: bool | None = None
    basis = "—"

    for mr in app.module_results:
        p = mr.module_payload
        full = p.get("full_output", p)
        if "loading_required" in full:
            loading_required = full["loading_required"]
            basis = full.get("loading_basis", "LAMC 12.21 A.6")
        elif "loading_required" in p:
            loading_required = p["loading_required"]
            basis = p.get("loading_basis", "LAMC 12.21 A.6")

    if loading_required is True:
        req_str = "Required"
    elif loading_required is False:
        req_str = "Not required"
    else:
        req_str = "Not calculated"

    return {
        "required": req_str,
        "basis": basis,
    }


def _build_setbacks(site: Site, project: Project) -> list[dict]:
    zone = site.zone
    alley_adjacent = project.alley_adjacent if hasattr(project, "alley_adjacent") else None

    calc_results = get_setback_results(
        zone=zone,
        project=project,
        lot_type="unknown",
        alley_adjacent=alley_adjacent if alley_adjacent else None,
    )

    rows = []
    for cr in calc_results:
        v = cr.value if isinstance(cr.value, dict) else {}
        edge = v.get("edge", cr.name.replace("setback_", "")).title()
        req = v.get("required_ft")
        prov = v.get("provided_ft")
        status = v.get("status", "unresolved")

        if status == "compliant":
            indicator = "compliant"
        elif status == "non_compliant":
            indicator = "non_compliant"
        elif status in ("provisional", "required_only"):
            indicator = "provisional"
        elif status == "not_entered":
            indicator = "not_entered"
        else:
            indicator = "unresolved"

        rows.append({
            "edge": edge,
            "required": f"{req}′" if req is not None else "—",
            "provided": f"{prov}′" if prov is not None else "Not entered",
            "indicator": indicator,
            "notes": cr.assumptions[0] if cr.assumptions else "",
        })

    return rows


def _build_density(app: AppResult, site: Site) -> dict:
    density_mr = _by_module(app, "density")
    if not density_mr:
        return {"baseline_units": "—", "allowed_units": "—", "proposed_units": "—",
                "lane": "—", "status": "Module not run"}

    payload = density_mr.module_payload
    full = payload.get("full_output", payload)

    # Baseline
    baseline = full.get("baseline_density", {})
    baseline_units = baseline.get("baseline_units")

    # Lane + result
    density_result = full.get("density_result", {})
    claimed = density_result.get("claimed_density_units")
    claimed_unlimited = density_result.get("claimed_density_is_unlimited", False)
    lane_raw = density_result.get("active_density_lane", "unresolved")
    lane_labels = {
        "none": "Base Zoning",
        "toc": "TOC Density Bonus",
        "state_db": "State Density Bonus",
        "unresolved": "Unresolved",
    }
    lane = lane_labels.get(lane_raw, lane_raw)

    allowed_str: str
    if claimed_unlimited:
        allowed_str = "Unlimited (100% affordable)"
    elif claimed is not None:
        allowed_str = str(claimed)
    elif baseline_units is not None:
        allowed_str = f"{baseline_units} (base zoning)"
    else:
        allowed_str = "Not calculated"

    # Candidate routes for comparison table
    candidate_routes = payload.get("candidate_routes") or []
    comparisons = []
    for c in candidate_routes:
        if not isinstance(c, dict):
            continue
        c_lane = c.get("lane", "")
        c_units = c.get("units")
        label = lane_labels.get(c_lane, c_lane.replace("_", " ").title())
        if c.get("unlimited"):
            c_units_str = "Unlimited"
        elif c_units is not None:
            c_units_str = str(c_units)
        else:
            c_units_str = "—"
        comparisons.append({"path": label, "units": c_units_str})

    return {
        "baseline_units": _int_or(baseline_units, fallback="Not calculated"),
        "allowed_units": allowed_str,
        "lane": lane,
        "comparisons": comparisons,
        "lot_area_used": _sf(baseline.get("lot_area_used")),
        "sf_per_du": _int_or(baseline.get("sf_per_du_used"), fallback="—"),
    }


def _build_incentives(app: AppResult) -> list[str]:
    items: list[str] = []

    density_mr = _by_module(app, "density")
    if density_mr:
        payload = density_mr.module_payload
        full = payload.get("full_output", payload)
        density_result = full.get("density_result", {})
        lane_raw = density_result.get("active_density_lane", "")
        if lane_raw == "toc":
            toc = full.get("toc_density", {})
            pct = toc.get("percentage_increase")
            if pct:
                items.append(f"TOC density bonus: +{pct:.0f}% density")
        elif lane_raw == "state_db":
            sdb = full.get("state_db_density", {})
            pct = sdb.get("bonus_percentage")
            if pct:
                items.append(f"State Density Bonus: +{pct:.0f}% density")
            if sdb.get("ab1287_eligible"):
                stack_pct = sdb.get("ab1287_stack_bonus_pct")
                if stack_pct:
                    items.append(f"AB 1287 stacking bonus: +{stack_pct:.0f}% additional density")

        # Incentives/waivers from module interpretations
        for mr in app.module_results:
            for finding in mr.findings:
                if "incentive" in finding.message.lower() or "waiver" in finding.message.lower():
                    items.append(finding.message)

    parking_mr = _by_module(app, "parking")
    if parking_mr:
        payload = parking_mr.module_payload
        full = payload.get("full_output", payload)
        lane_data = full.get("parking_lane", {})
        selected = lane_data.get("selected", "")
        if selected == "ab2097":
            items.append("AB 2097: parking minimums waived (transit area)")
        elif selected == "toc":
            items.append("TOC parking reduction applies")
        elif selected == "state_db":
            items.append("State Density Bonus parking reduction applies")

    return items if items else ["No incentives identified at this stage"]


def _collect_flags(
    site: Site,
    project: Project,
    app: AppResult,
    setback_rows: list[dict],
    far_data: dict,
    parking_data: dict,
    open_space_data: dict,
) -> list[dict]:
    """Collect all open items, missing inputs, and provisional assumptions."""
    flags: list[dict] = []

    def _flag(what: str, affects: str, action: str) -> None:
        flags.append({"what": what, "affects": affects, "action": action})

    # Site-level
    if not site.lot_area_sf:
        _flag("Lot area not resolved from ZIMAS", "Density, FAR, open space calculations",
              "Confirm lot area with survey or assessor record")
    if not site.zone:
        _flag("Zone not resolved", "All calculations",
              "Confirm zone from ZIMAS or LADBS counter")
    if site.specific_plan:
        _flag(f"Specific plan detected: {site.specific_plan}",
              "FAR, density, parking, setbacks",
              "Review specific plan for override provisions")
    if site.overlay_zones:
        _flag(f"Overlay zones detected: {', '.join(site.overlay_zones)}",
              "FAR, density, parking",
              "Review overlay provisions for any standard modifications")

    # Entitlement path
    if not project.selected_path:
        _flag("Entitlement path not selected",
              "Density, parking, ED1 eligibility",
              "Select Base Zoning, Density Bonus, or 100% Affordable path")

    # FAR
    if far_data.get("proposed_sf") in ("Not entered", "—"):
        _flag("Counted floor area not entered",
              "FAR compliance check",
              "Architect to provide total counted floor area per governing FAR definition")

    # Setbacks
    provisional_edges = [r["edge"] for r in setback_rows if r["indicator"] == "provisional"]
    if provisional_edges:
        _flag(f"Setback values provisional for: {', '.join(provisional_edges)}",
              "Setback compliance",
              "Confirm lot type (interior/corner/through), frontage conditions, and alley adjacency")
    not_entered_edges = [r["edge"] for r in setback_rows if r["provided"] == "Not entered"]
    if not_entered_edges:
        _flag(f"Provided setbacks not entered for: {', '.join(not_entered_edges)}",
              "Setback compliance check",
              "Architect to enter proposed setback dimensions for each edge")

    # Open space
    if open_space_data.get("required_sf") == "Not calculated":
        _flag("Open space requirement not calculated",
              "Open space compliance",
              "Enter unit count and unit mix to enable open space calculation")
    if open_space_data.get("provided_sf") == "Not entered":
        _flag("Open space provided area not entered",
              "Open space compliance check",
              "Architect to enter proposed open space area")

    # Parking
    if parking_data.get("auto_provided") == "Not entered":
        _flag("Parking spaces provided not entered",
              "Parking compliance check",
              "Enter proposed parking count to verify against requirement")
    if parking_data.get("accessible_provided") == "Not entered":
        _flag("Accessible parking spaces provided not entered",
              "Accessibility compliance",
              "Enter proposed accessible parking count")

    # Module-level issues
    for mr in app.module_results:
        for iss in mr.issues:
            if iss.blocking or iss.needs_authority_confirmation:
                _flag(iss.message,
                      mr.module.replace("_", " ").title(),
                      iss.details.get("action_required", "Review required"))

    return flags


# ── public API ────────────────────────────────────────────────────────────────

def build_g010_viewmodel(
    site: Site,
    project: Project,
    app: AppResult,
) -> dict:
    """Build the G010 viewmodel dict consumed by gsheet.html.

    Gracefully handles missing data throughout — never raises on absent fields.
    """
    setback_rows = _build_setbacks(site, project)
    far_data = _build_far(app, site)
    open_space_data = _build_open_space(app, project)
    parking_data = _build_parking(app, project, site)

    flags = _collect_flags(site, project, app, setback_rows, far_data, parking_data, open_space_data)

    path_labels = {
        "base_zoning": "Base Zoning",
        "density_bonus": "State Density Bonus Law",
        "affordable_100": "100% Affordable (ED1 / Track B)",
    }

    return {
        "project_info": _build_project_info(site, project),
        "unit_count": _build_unit_count(project),
        "far": far_data,
        "open_space": open_space_data,
        "parking": parking_data,
        "bike_parking": _build_bike_parking(app, project),
        "ev": _build_ev(app, project),
        "loading": _build_loading(app, site, project),
        "setbacks": setback_rows,
        "density": _build_density(app, site),
        "incentives": _build_incentives(app),
        "building_codes": _load_building_codes(),
        "flags": flags,
        "entitlements": project.entitlements_text or "—",
        "legal_description": project.legal_description or "—",
        "metadata": {
            "firm_name": project.firm_name or "—",
            "project_number": project.project_number or "—",
            "issue_date": project.issue_date or "—",
            "selected_path": path_labels.get(project.selected_path or "", "Not selected"),
            "application_date": project.application_date or "—",
        },
    }
