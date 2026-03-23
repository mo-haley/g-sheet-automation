from __future__ import annotations

"""CLI entry point for the KFA G-Sheet Calc Tool."""

import json
import sys
from pathlib import Path

import click

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
from ingest.parser import parse_zimas_response
from ingest.zimas import ZIMASClient
from models.project import Project
from output.excel import generate_workbook
from output.json_export import export_json
from rules.advisory.adaptive_reuse_stub import screen_adaptive_reuse
from rules.advisory.affordable_housing_screen import screen_100pct_affordable
from rules.advisory.density_bonus_screen import screen_density_bonus
from rules.advisory.streamlining_screen import screen_ab2011, screen_sb423
from rules.advisory.toc_screen import screen_toc


@click.group()
def cli():
    """KFA G-Sheet Calc Tool - Zoning feasibility analysis for LA multifamily projects."""


@cli.command()
@click.argument("address")
@click.option("--project-json", type=click.Path(exists=True), help="Path to project assumptions JSON file")
@click.option("--output-dir", type=click.Path(), default="output", help="Output directory")
@click.option("--skip-ingest", is_flag=True, help="Skip ZIMAS API queries (use cached data only)")
def run(address: str, project_json: str | None, output_dir: str, skip_ingest: bool):
    """Run a full feasibility analysis for an address."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    issue_register = IssueRegister()

    # --- Load project assumptions ---
    if project_json:
        raw = json.loads(Path(project_json).read_text())
        project = Project(**raw)
    else:
        project = Project(project_name=f"Analysis: {address}")
    click.echo(f"Project: {project.project_name}")

    # --- Ingest ---
    click.echo(f"Geocoding: {address}")
    geocoder = Geocoder()
    coords = geocoder.geocode(address)
    if coords is None:
        click.echo("ERROR: Could not geocode address.", err=True)
        sys.exit(1)
    click.echo(f"Coordinates: {coords[0]:.6f}, {coords[1]:.6f}")

    click.echo("Querying ZIMAS...")
    zimas = ZIMASClient()
    identify_data = zimas.identify(coords[0], coords[1])

    site, zoning_parse, ingest_issues = parse_zimas_response(
        address, identify_data, coordinates=coords, pull_timestamp=zimas.pull_timestamp
    )
    issue_register.add_all(ingest_issues)
    click.echo(f"Zone: {site.zone} | HD: {site.height_district} | TOC: {site.toc_tier}")

    # --- Deterministic calculations ---
    click.echo("Running calculations...")
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

    # --- Advisory screens ---
    click.echo("Running advisory screens...")
    all_det_results = area_results + density_results + far_results + height_results
    all_det_issues = issue_register.get_all()

    scenarios = [
        build_base_zoning_scenario(all_det_results, all_det_issues),
        screen_toc(site, project),
        screen_density_bonus(site, project),
        screen_100pct_affordable(site, project),
        screen_sb423(site, project),
        screen_ab2011(site, project),
        screen_adaptive_reuse(site, project),
    ]

    # Collect scenario issues
    for sc in scenarios:
        issue_register.add_all(sc.issues)

    all_issues = issue_register.get_all()

    # --- Output ---
    click.echo("Generating outputs...")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in address)[:60]

    excel_path = output_path / f"{safe_name}.xlsx"
    generate_workbook(
        site=site,
        project=project,
        area_results=area_results,
        density_results=density_results,
        far_results=far_results,
        height_results=height_results,
        parking_results=parking_results,
        open_space_results=os_results,
        loading_results=load_results,
        scenarios=scenarios,
        issues=all_issues,
        output_path=excel_path,
    )
    click.echo(f"Excel: {excel_path}")

    json_path = output_path / f"{safe_name}.json"
    export_json(
        site=site,
        project=project,
        area_results=area_results,
        density_results=density_results,
        far_results=far_results,
        height_results=height_results,
        parking_results=parking_results,
        open_space_results=os_results,
        loading_results=load_results,
        scenarios=scenarios,
        issues=all_issues,
        output_path=json_path,
    )
    click.echo(f"JSON: {json_path}")

    # Summary
    blocking = issue_register.get_blocking()
    click.echo(f"\nIssues: {issue_register.count} total, {len(blocking)} blocking")
    if blocking:
        click.echo("\nBlocking issues:")
        for bi in blocking:
            click.echo(f"  [{bi.severity}] {bi.id}: {bi.title}")

    click.echo("\nDone.")


@cli.command()
@click.argument("address")
def ingest(address: str):
    """Run ingest only (geocode + ZIMAS query) for an address."""
    geocoder = Geocoder()
    coords = geocoder.geocode(address)
    if coords is None:
        click.echo("ERROR: Could not geocode address.", err=True)
        sys.exit(1)
    click.echo(f"Coordinates: {coords[0]:.6f}, {coords[1]:.6f}")

    zimas = ZIMASClient()
    identify_data = zimas.identify(coords[0], coords[1])

    site, zoning_parse, issues = parse_zimas_response(address, identify_data, coordinates=coords)
    click.echo(f"Zone: {site.zone}")
    click.echo(f"Height District: {site.height_district}")
    click.echo(f"TOC Tier: {site.toc_tier}")
    click.echo(f"Community Plan: {site.community_plan_area}")
    click.echo(f"Lot Area: {site.lot_area_sf}")
    click.echo(f"Issues: {len(issues)}")
    for issue in issues:
        click.echo(f"  [{issue.severity}] {issue.id}: {issue.title}")


if __name__ == "__main__":
    cli()
