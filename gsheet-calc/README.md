# KFA G-Sheet Calc Tool V1

Zoning feasibility analysis tool for Los Angeles multifamily projects. Produces traceable, authority-backed calculations for base zoning entitlements and advisory pathway screening.

## Setup

```bash
cd gsheet-calc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

### Full analysis

```bash
python main.py run "1234 Main St, Los Angeles, CA 90012" --project-json project.json --output-dir output/
```

### Ingest only (geocode + ZIMAS query)

```bash
python main.py ingest "1234 Main St, Los Angeles, CA 90012"
```

### Project JSON format

Create a JSON file with project assumptions:

```json
{
  "project_name": "My Project",
  "total_units": 25,
  "unit_mix": [
    {"label": "Studio", "count": 8, "habitable_rooms": 2, "bedrooms": 0},
    {"label": "1BR", "count": 12, "habitable_rooms": 3, "bedrooms": 1},
    {"label": "2BR", "count": 5, "habitable_rooms": 4, "bedrooms": 2}
  ],
  "parking_spaces_total": 40,
  "parking_assigned": 25,
  "parking_unassigned": 15
}
```

## Output

- **Excel workbook** (12 tabs): Project Summary, Site Data, Data Sources, Assumptions, Issue Register, Area Calculations, Density + FAR, Parking Summary, Open Space + Loading, Advisory Screens, Code References, Raw Source Log
- **JSON export**: Complete structured data with all calculations, scenarios, issues, and provenance

## Architecture

### Deterministic calculations
Area chains, density, FAR, height, parking (auto/accessible/bike/EV), open space, loading. Each produces `CalcResult` objects with full traceability (formula, inputs, code section, authority ID).

### Advisory screens
TOC, State Density Bonus, 100% Affordable, SB 423, AB 2011, Adaptive Reuse. Each produces `ScenarioResult` with `determinism: "advisory"`. Never claims deterministic confidence.

### Issue register
Every ambiguous, missing, or low-confidence value generates a `ReviewIssue` with severity, suggested review role, and blocking status.

## Running tests

```bash
cd gsheet-calc
python -m pytest validation/tests/ -v
```

## Known limitations

- Setback area deduction is simplified (assumes rectangular lot geometry)
- Accessible parking calculated per project, not per parking facility
- EVCS accessible scoping requires manual charging configuration input
- Commercial bike parking ratios are placeholder (authority gap)
- Chapter 1A zone tables not implemented (generates review issue)
- No live ZIMAS web scraping — uses ArcGIS REST API with local caching
- Geocoding via Nominatim (prototype quality — replace for production)

## Code cycle

- 2025 California Building Code
- LAMC Revision 7
- 2025 CALGreen

## Disclaimer

Preliminary internal analysis only. Not for final code determination without professional review.
