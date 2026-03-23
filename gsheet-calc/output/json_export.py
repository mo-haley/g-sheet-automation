"""JSON export of all calculation results, scenarios, and provenance."""

import json
from datetime import datetime
from pathlib import Path

from config.code_version import CODE_CYCLE, TOOL_VERSION
from config.settings import DISCLAIMER
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.scenario import ScenarioResult
from models.site import Site


def export_json(
    site: Site,
    project: Project,
    area_results: list[CalcResult],
    density_results: list[CalcResult],
    far_results: list[CalcResult],
    height_results: list[CalcResult],
    parking_results: list[CalcResult],
    open_space_results: list[CalcResult],
    loading_results: list[CalcResult],
    scenarios: list[ScenarioResult],
    issues: list[ReviewIssue],
    output_path: Path,
) -> Path:
    """Export all data as a structured JSON file."""
    export = {
        "metadata": {
            "tool_version": TOOL_VERSION,
            "code_cycle": CODE_CYCLE["label"],
            "generated": datetime.now().isoformat(),
            "disclaimer": DISCLAIMER,
        },
        "site": site.model_dump(mode="json"),
        "project": project.model_dump(mode="json"),
        "calculations": {
            "areas": [r.model_dump(mode="json") for r in area_results],
            "density": [r.model_dump(mode="json") for r in density_results],
            "far": [r.model_dump(mode="json") for r in far_results],
            "height": [r.model_dump(mode="json") for r in height_results],
            "parking": [r.model_dump(mode="json") for r in parking_results],
            "open_space": [r.model_dump(mode="json") for r in open_space_results],
            "loading": [r.model_dump(mode="json") for r in loading_results],
        },
        "scenarios": [s.model_dump(mode="json") for s in scenarios],
        "issues": [i.model_dump(mode="json") for i in issues],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(export, indent=2, default=str))
    return output_path
