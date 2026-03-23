---
name: KFA real-world FAR reference projects
description: Validated FAR patterns from actual KFA project sheets — 327 North Harbor, 417 Alvarado, TCC Beacon. Use as ground truth for FAR module testing.
type: project
---

Three real KFA projects serve as ground truth for FAR calculations:

**327 North Harbor (San Pedro)**
- Zone: C2-2D-CPIO, APNs: 7449-014-013 + 7449-014-014 (lot tie)
- CPIO Ord #185539, Subarea E: max FAR = 4.0:1
- Area basis: lot area (24,197 SF per survey), NOT buildable
- Allowable: 24,197 x 4.0 = 96,788 SF (per CPIO §III-2.B.4)
- Proposed: 46,765 SF, FAR 1.93:1
- Uses LAMC 12.03 floor area definition
- Two construction types: Type V-A upper, Type I-A ground level

**417 Alvarado Senior Housing**
- Zone: C2-1, APN: 5154-031-006 (and -005, -004)
- Baseline FAR: 1.5:1 (C/M in HD1), with DB increase to 3.0:1
- Area basis: lot area = buildable area = 22,495 SF (no dedications net)
- Baseline allowable: 22,495 x 1.5 = 33,743 SF
- DB allowable: 22,495 x 3.0 = 67,485 SF (with DB increase per AB1287)
- Proposed: 80,834 SF, FAR 3.59:1 (exceeds baseline, within DB allowance)
- Floor area measured per LAMC 12.03
- 109 units, 7-story, senior housing

**TCC Beacon (San Pedro)**
- Zone: C2-2D-CPIO, lot area 56,341 SF (1.293 acres)
- Buildable: 55,825 SF (1.281 acres) after dedications
- Governed by DIR-2020-2595-HCA-M1 (entitlement)
- Allowable FAR per DIR: 4.11:1 FAR base -> 229,097 SF max
- Proposed: 228,882 SF, FAR 4.0824:1
- 8-story, 281 units, mixed-use (R-2 residential, B commercial, S-2 parking)
- Floor area per LAMC 12.03

**Why:** These three projects cover the main FAR authority patterns: baseline-only, CPIO-override, D-limitation, DB incentive, and DIR entitlement.
**How to apply:** Use as validation targets when testing FAR module changes. The numbers in the screenshots are architect-verified.
