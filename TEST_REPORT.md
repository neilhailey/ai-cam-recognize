# Test Report — 12 CNC project models

Models from `Project Display Box/project_display_box_models/CNC_Projects/`.
Expectations were confirmed by Neil; the engine was then fixed until it matched.

**Result: 12 / 12 match (was 6 / 12).** Fixes are in commit `b6a356b`.

## Final results

| Model | Verdict | Setups | Expected | |
|-------|---------|--------|----------|---|
| girl_3d_relief | 3-axis relief | 1 | 1-sided 3-axis | ✅ |
| Angel_girl_3d_relief | 3-axis relief | 1 | 1-sided 3-axis | ✅ |
| jaycar_twotrees_logo | 3-axis relief | 1 | 1-sided 3-axis | ✅ |
| gear | 3-axis relief | 1 | 1-sided 3-axis | ✅ |
| drone_part | 3-axis relief | 1 | 1-sided 3-axis | ✅ |
| One_heart_two_halves | 3-axis relief | 1 | 1-sided 3-axis | ✅ |
| pig | 4-axis, single rotary setup | 1 | 4-axis, 1 setup | ✅ |
| penguin | 4-axis, single rotary setup | 1 | 4-axis, 1 setup | ✅ |
| cat | 4-axis, single rotary setup | 1 | 4-axis, 1 setup | ✅ |
| dog | 4-axis, single rotary setup | 1 | 4-axis, 1 setup | ✅ |
| bunny | 4-axis, single rotary setup | 1 | 4-axis, 1 setup | ✅ |
| wave_3d_relief_pattern | 5-axis required | – | flagged as suspect | ⚠️ see below |

## What was wrong, and why

### 1. Reliefs read as 4/5-axis

A relief is a height map: nothing overhangs, so it is carved from the top in one
setup. The per-face ray test failed on them for two reasons that only appear on real
STLs:

* **Decimation stair-steps.** These parts are wide and shallow (0.10 × 0.07 × 0.02).
  Reducing 1.5 M faces to 30 k quantised the depth into steps, and every step read as a
  tiny overhang. Measured on `girl_3d_relief` — 0 % overhanging columns in the original,
  2.8–7.2 % after decimation, which was enough to fail the verdict.
* **Untrustworthy normals.** Only 3 of the 12 meshes are watertight solids, and
  `Angel_girl_3d_relief` has inconsistent winding — at full resolution it returned
  *0 % 3-axis*, which is meaningless.

**Fix:** `relief_probe()` counts **ray-column crossings** instead of testing faces. A
height map gives exactly two (top, then bottom); a third means real overhang. It uses
neither normals nor fine geometry, so it survives both failure modes. Threshold: ≥ 85 %
of columns clean, and the part must be plate-like (depth < 45 % of width).

Measured separation — clean-column fraction:

| plate-like models | | figurines | |
|---|---|---|---|
| drone_part, gear, One_heart | 1.00 | pig, cat, dog, bunny, penguin | 0.00 |
| girl_3d_relief | 0.96 | | |
| Angel_girl_3d_relief | 0.92 | | |
| jaycar_twotrees_logo | 0.91 | | |
| **wave_3d_relief_pattern** | **0.00** | | |

### 2. 4-axis was reported as two setups

Wrong model of the process: a part held between chuck and tailstock **turns to present
every side, so it is one setup** — only the end tabs remain, trimmed off afterwards. The
`sides` count was 3-axis flip logic leaking into rotary work. The verdict, setup card and
caveats now all say "single rotary setup".

### 3. Mesh defects were silent

`mesh_quality()` now warns on non-watertight or inconsistent-winding meshes instead of
returning a confident wrong number.

### 4. Impractical orientation advice

The tool suggested rotating the pig to an *oblique* orientation to reach "3-axis
(2-sided)" — unclampable, and a worse deal than one rotary setup. Oblique suggestions are
suppressed, as is trading one rotary setup for two 3-axis ones.

## Outstanding: wave_3d_relief_pattern

Averages **6.93 surface crossings per column (max 12)** — every single column. That is not
a relief; it is a multi-layered surface. Either it genuinely has curling undercuts, or the
file contains stacked/duplicate geometry. The 5-axis verdict is defensible for the
geometry as supplied, but the file is worth opening in CAM to check.

## Scale note

All 12 models are 0.03–1.0 units across, so **none are in millimetres** — they are
normalised exports and must be scaled before cutting. The UI reports sizes in file units
and says so.
