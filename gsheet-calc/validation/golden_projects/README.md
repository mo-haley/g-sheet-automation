# Golden Projects

Placeholder directory for validated project data.

Golden projects are real or carefully constructed reference projects where
the correct zoning analysis has been verified by a licensed architect or
zoning consultant. They serve as regression baselines.

**Do not fabricate project data.** Only add projects here that have been
independently verified against actual entitlement outcomes.

## Adding a golden project

1. Create a subdirectory with the project name
2. Include `site.json`, `project.json`, and `expected_results.json`
3. Document the verification source and date
4. Add a corresponding test in `validation/tests/`
