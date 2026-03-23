"""App-level orchestrator — first conservative pass.

Runs migrated modules (zimas_linked_docs, far, density, parking, setback) and
aggregates their ModuleResult outputs into an AppResult.

Usage:
    from analysis.app_orchestrator import run_app, build_zimas_input_from_site

    result = run_app(site, project, project_id="proj-001")

    # With richer ZIMAS inputs (raw identify response, parsed zone confidence, etc.):
    result = run_app(site, project, project_id="proj-001", zimas_input=inp)

    # With explicit density lane selection (decision-grade density output):
    result = run_app(site, project, project_id="proj-001", density_selected_lane="toc")

    # With explicit parking lane selection:
    result = run_app(site, project, project_id="proj-001", parking_lane="none")

Note on setbacks: Site does not carry lot-edge geometry, so the setback module
runs in THIN mode from standard site data. Callers with edge geometry should
call run_setback_module() directly with a populated SetbackProjectInputs.
"""

from __future__ import annotations

from models.result_common import AppResult
from models.site import Site
from models.project import Project
from calc.far import calculate_far_module
from density.density_orchestrator import run_density_module
from ed1.models import ED1Input
from ed1.ed1_orchestrator import run_ed1_module
from parking.parking_orchestrator import run_parking_module
from setback.models import SetbackProjectInputs
from setback.setback_orchestrator import run_setback_module
from zimas_linked_docs.models import ZimasLinkedDocInput
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_module


def build_setback_inputs_from_site(site: Site) -> SetbackProjectInputs:
    """Build a minimal SetbackProjectInputs from Site fields.

    Site does not carry lot-edge geometry, so the returned inputs have no
    edges. The setback module will run in THIN mode (zone resolved, no edge
    values computed). Callers with edge geometry should build
    SetbackProjectInputs directly.
    """
    return SetbackProjectInputs(
        lot_type=getattr(site, "lot_type", "interior") or "interior",
    )


def build_zimas_input_from_site(site: Site) -> ZimasLinkedDocInput:
    """Map Site fields to ZimasLinkedDocInput.

    Covers the fields available from standard parcel ingest.
    Callers that have richer inputs (raw_zimas_identify, zoning_parse_confidence,
    raw_text_fragments, cpio_subarea) should build ZimasLinkedDocInput directly
    and pass it via the zimas_input parameter of run_app().
    """
    return ZimasLinkedDocInput(
        apn=site.apn,
        specific_plan=site.specific_plan,
        specific_plan_subarea=site.specific_plan_subarea,
        overlay_zones=site.overlay_zones,
        q_conditions=site.q_conditions,
        d_limitations=site.d_limitations,
    )


def run_app(
    site: Site,
    project: Project,
    project_id: str,
    zimas_input: ZimasLinkedDocInput | None = None,
    density_selected_lane: str | None = None,
    density_posture: str | None = None,
    parking_lane: str | None = None,
    ed1_overrides: ED1Input | None = None,
    run_ed1: bool = True,
) -> AppResult:
    """Run all migrated modules and return a standardized AppResult.

    If zimas_input is not provided, it is constructed from site fields.
    Pass zimas_input explicitly when you have richer inputs to supply
    (raw ZIMAS identify response, parsed zone confidence, etc.).

    Density runs in comparison mode by default (density_selected_lane=None),
    producing a posture-filtered route comparison rather than a single answer.
    Pass density_selected_lane="none"/"toc"/"state_db" for decision-grade output.

    Parking runs with no explicit lane by default (authority-based selection).
    Pass parking_lane="none"/"toc"/"state_db"/"ab2097" to force a lane.

    ED1 screening runs by default (run_ed1=True). Pass run_ed1=False to
    skip it. Pass ed1_overrides to supply explicit ED1-specific inputs
    that cannot be derived from Site/Project.
    """
    if zimas_input is None:
        zimas_input = build_zimas_input_from_site(site)

    zimas_result = run_zimas_linked_doc_module(zimas_input)
    far_result = calculate_far_module(site, project)
    density_result = run_density_module(
        site,
        project,
        development_posture=density_posture,
        selected_lane=density_selected_lane,
    )
    parking_result = run_parking_module(site, project, parking_lane=parking_lane)
    setback_result = run_setback_module(
        project_inputs=build_setback_inputs_from_site(site),
        raw_zone=site.zoning_string_raw or site.zone or "",
        base_zone=site.zone or "",
        height_district=site.height_district,
        specific_plan=site.specific_plan,
        cpio=next(iter(site.overlay_zones), None) if site.overlay_zones else None,
        d_limitation=next(iter(site.d_limitations), None) if site.d_limitations else None,
        q_condition=next(iter(site.q_conditions), None) if site.q_conditions else None,
    )

    module_results = [zimas_result, far_result, density_result, parking_result, setback_result]

    if run_ed1:
        ed1_result = run_ed1_module(site, project, ed1_overrides=ed1_overrides)
        module_results.append(ed1_result)

    return AppResult.from_module_results(
        project_id=project_id,
        module_results=module_results,
    )
