# Address-Only Snapshot — Calibration Checklist

Run each site type through `/run-address?debug=1` (with `app.debug=True`).
Check whether the output makes sense for that site profile.

## Site Types to Test

### 1. Clean R4 interior lot, no overlays
- **What to look for:** Coverage=High, opportunity signal ("straightforward"),
  base-zoning-only scenario, setback thin (no edges), no constraint signal.
- **Example zone:** R4-1

### 2. C2 with TOC tier (tier 2 or 3)
- **What to look for:** Coverage=Moderate, opportunity signal mentions TOC,
  TOC scenario row included with tier cited in why_shown, comparison slot
  filled (base vs TOC), best-next-inputs includes affordability strategy.
- **Example zone:** C2-1 in a TOC tier 2 area

### 3. Site with specific plan + CPIO
- **What to look for:** Constraint signal names the specific plan,
  no filler scenario rows, authority flag if zimas module requires confirmation,
  dedication caveat included, governing-doc source shows limitation.
- **Example zone:** [Q]C2-1-CDO inside a specific plan area

### 4. Site with Q conditions and D limitations
- **What to look for:** Constraint signal lists counts ("2 Q condition(s),
  1 D limitation(s)"), no authority-confirmation unless zimas module actually
  requires it, coverage not forced to Interrupted from Q/D alone.
- **Example zone:** [Q]R3-1-D

### 5. Site in AB 2097 area (no TOC)
- **What to look for:** AB 2097 scenario row included (not transit proximity),
  opportunity signal mentions AB 2097, TOC row absent, comparison slot
  may be empty (single path).
- **Example zone:** R4-1 with ab2097_area=True

### 6. Site with thin density (unmapped zone)
- **What to look for:** Coverage=Moderate or Thin, uncertainty signal is
  density-specific, best-next-inputs still leads with unit count,
  density module card shows sensitivity=High.

### 7. Site with multiple density candidate routes
- **What to look for:** Comparison signal cites route count,
  State Density Bonus scenario row included (if state_db in candidates),
  best-next-inputs may include "entitlement path selection".

### 8. Majority-blocked run (simulated)
- **What to look for:** Coverage=Interrupted, summary sentence mentions
  inability to resolve, signals still attempt to fill available slots,
  caveats pull blocking issues.

### 9. Single module authority-confirmation (not majority)
- **What to look for:** Coverage is NOT Interrupted (should be Moderate or
  Thin based on other modules), authority_flags box appears in summary card,
  constraint signal references the authority issue.

### 10. Bare-minimum site (zone only, no lot area, no overlays)
- **What to look for:** Coverage=Thin or Moderate (depends on module count),
  multiple thin modules in debug trace, uncertainty signal names them,
  best-next-inputs emphasizes geometry.

## How to Read the Debug Panel

| Section | What to check |
|---|---|
| Coverage Label | Does the label match your intuition for this site? Check `reason` for the branch taken. |
| Signals | Are all 5 slots attempted? Do filled slots have sensible triggers? Are empty slots expected? |
| Scenarios | Are only triggered rows shown? Is `why_shown` traceable to a real site field? |
| Best Next Inputs | Do the top 2-3 match what an architect would actually enter next? |
| Caveats | Are the "always" caveats reasonable? Are conditional ones triggered correctly? |

## Quick Smoke Test

1. Run a known clean site (R4-1, interior, no overlays) — expect High/straightforward.
2. Run a known complex site (Q/D/SP/TOC) — expect Moderate, multiple signals, multiple scenarios.
3. Compare the two — the complex site should have more scenarios, more caveats, different signals.
4. Check that no scenario appears without a debug-traceable trigger.
5. Check that Best Next Inputs differ between the two (clean site won't suggest affordability).
