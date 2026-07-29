# Test Report — 12 CNC project models

Run against the current engine (commit `f4c5bbd` + the single-setup fix). Each model was
loaded, auto-oriented onto its mounting face, and analysed.

## Independent ground truth

To check the tool rather than trust it, each model was also probed with a **heightfield
test**: fire a grid of rays straight down and count surface crossings per column. A part
that is fully cuttable from the top gives exactly **2 hits** (top surface + bottom). More
than 2 means material genuinely overhangs something, i.e. 3-axis-from-the-top cannot
finish it. This is independent of the machinability engine.

## Results

| # | Model | Tool verdict | Setups | 3-axis | 4-axis | Heightfield probe | Assessment |
|---|-------|--------------|--------|--------|--------|-------------------|------------|
| 1 | gear | **3-axis, single setup** | 1 | 100 % | – | 2.00, 0 % overhang | ✅ correct |
| 2 | drone_part | **3-axis, single setup** | 1 | 100 % | – | 2.00, 0 % overhang | ✅ correct |
| 3 | One_heart_two_halves | **3-axis, single setup** | 2 | 100 % | – | 2.00, 0 % overhang | ✅ verdict correct (setup-count quibble below) |
| 4 | pig | 4-axis | 2 | 78.0 % | 100 % | 2.29, 10.5 % overhang | ✅ plausible — real overhangs |
| 5 | penguin | 4-axis | 2 | 94.8 % | 100 % | – | ✅ plausible |
| 6 | cat | 4-axis | 2 | 66.3 % | 100 % | – | ✅ plausible |
| 7 | dog | 4-axis | 2 | 65.3 % | 100 % | – | ✅ plausible |
| 8 | bunny | 4-axis | 2 | 57.9 % | 100 % | – | ✅ plausible |
| 9 | **girl_3d_relief** | 4-axis | 2 | 92.4 % | 99.8 % | **2.00, 0 % overhang** | ❌ **WRONG — is a perfect heightfield, must be 3-axis** |
| 10 | **Angel_girl_3d_relief** | 4-axis | 1 | 95.2 % | 98.8 % | – | ❌ **unreliable — mesh has inconsistent winding** |
| 11 | jaycar_twotrees_logo | 5-axis | 2 | 77.8 % | 77.8 % | 2.09, 9.4 % overhang | ⚠️ suspicious for a logo plate |
| 12 | wave_3d_relief_pattern | 5-axis | 2 | 22.7 % | 56.4 % | **6.93 hits/col, 100 % overhang** | ⚠️ mesh is not a heightfield at all |

## Confirmed bug: decimation destroys shallow reliefs

`girl_3d_relief` is a **perfect heightfield** — every ray column crosses exactly twice, so
there is no overhang anywhere and it must be 3-axis in one setup. Analysed at different
resolutions:

| Faces analysed | Verdict | 3-axis |
|----------------|---------|--------|
| 1,556,840 (full) | **3-axis, single setup** ✅ | 99.7 % |
| 200,000 | **3-axis, single setup** ✅ | 99.5 % |
| 30,000 | 4-axis ❌ | 93.2 % |
| 12,960 (what the app uses) | 4-axis ❌ | 92.4 % |

**Cause:** relief detail is shallow relative to the footprint — this part is 0.073 × 0.103
wide but only 0.02 deep. The vertex-clustering grid is isotropic, so at 30k faces the Z
direction gets very few cells; the surface is quantised into stair-steps, and each step
reads as a tiny overhang. Reliefs need far more faces than a solid figurine of the same
size. The animals survive decimation because their features are deep.

## Second issue: mesh quality is not checked

Only 3 of 12 models are watertight solids, and **`Angel_girl_3d_relief` has inconsistent
winding** — its face normals do not agree on which way is "out". Analysed at full
resolution it returns **0 % 3-axis / 5-axis required**, which is meaningless. The tool
currently accepts such meshes silently and reports a confident-looking number.

`wave_3d_relief_pattern` averages **6.93 crossings per column (max 12)** — it is a
multi-layered surface, not a relief heightfield. Either it genuinely has curling
undercuts, or the mesh contains stacked/duplicated surfaces. Its verdict may be
defensible for the geometry as supplied, but not for what the filename implies.

## Minor: setup count vs verdict

`One_heart_two_halves` reports **verdict = single setup** but the **setup plan says 2**.
The verdict asks "can +Z alone finish it" while the planner greedily covers from a wider
candidate set and can pick a different first direction. They should agree.

## Scale note

Every model is **0.03–1.0 units** across, so none are in millimetres — they are normalised
exports. The UI already flags this and reports sizes in file units, but they must be
scaled before cutting.

## Suggested fixes, in priority order

1. **Detect 2.5D relief parts** (heightfield probe is cheap and reliable) and either skip
   decimation or decimate anisotropically, preserving Z. This alone fixes model 9 and
   likely 10.
2. **Validate mesh quality on upload** — warn on non-watertight or inconsistent winding
   rather than reporting a confident wrong answer.
3. Reconcile the setup planner with the verdict so their setup counts always agree.
