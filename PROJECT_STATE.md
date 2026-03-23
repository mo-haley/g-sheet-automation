# Project State Snapshot

> This file records the project baseline after the first git checkpoint.
> It is a snapshot only — not an authority source. Verify against actual code and LAMC text before any permit use.

---

## 1. Current Baseline

| Item | Value |
|---|---|
| Git initialized | Yes |
| Branch | `main` |
| Checkpoint commit | `a947d21` |
| Snapshot date | 2026-03-22 |

---

## 2. Module Status

| Module | Status |
|---|---|
| `density/` | Stabilized — first-pass logic lock complete |
| `parking/` | Stabilized — first-pass logic lock complete |
| `setback/` | First-pass scaffold and full logic chain complete |
| `zimas_linked_docs/` | Recovery pass complete; trust-signaling work underway |
| ZIMAS PDF extraction plan | **Not yet reconstructed** — no dedicated plan document exists on disk |

---

## 3. Directories Confirmed in Checkpoint `a947d21`

- `gsheet-calc/density/` — 8 files
- `gsheet-calc/parking/` — 9 files
- `gsheet-calc/setback/` — 8 files
- `gsheet-calc/ingest/` — zimas API client, zoning parser, normalizer, raw cache
- `gsheet-calc/zimas_linked_docs/` — 13 files including tests
- Supporting: `models/`, `config/`, `data/`, `rules/`, `calc/`, `analysis/`, `output/`, `validation/`

---

## 4. Known Open Items and Caveats

- **No permit-use claim.** Legal and code assumptions in all modules require independent verification against published LAMC text before being used in permit applications or professional practice.
- **Provisional yard formulas.** Several setback formulas (story increment, alley reduction amount, R5 values, RD zone values) are explicitly provisional. See inline ASSUMPTION notes in `setback/setback_yard_family.py` and `setback/setback_authority.py`.
- **LAMC section citations unverified.** Yard-specific subsection references (e.g., 12.10-C) follow the density module's zone-table mapping and have not been independently confirmed against published LAMC text.
- **Linked-doc trust signaling.** The `zimas_linked_docs/` pipeline is functional but trust-signal hardening is still in progress. Output should not be treated as production-ready.
- **ZIMAS PDF extraction plan absent.** If a structured plan document for PDF extraction was discussed in prior sessions, it was not saved to disk. Needs reconstruction if required.
- **Excluded from checkpoint scope:** `.claude/`, `.venv/`, `gsheet-calc/governing_docs/`, `gsheet-calc/web/`. Contents of `governing_docs/` and `web/` are unknown and were not reviewed.

---

## 5. How to Use This File

Future work should be diffed against commit `a947d21` to understand what has changed since this baseline:

```
git diff a947d21
git diff a947d21 -- gsheet-calc/setback/
```

This file is a **project-state snapshot only**. It does not substitute for reading the code, the inline ASSUMPTION notes in each module, or the authoritative legal sources those modules reference.
