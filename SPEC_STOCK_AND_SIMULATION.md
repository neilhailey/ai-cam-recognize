# Spec — stock recommendation & machining simulation

Status: **proposal, nothing implemented.** Ordered so each phase ships value on its own
and can be stopped after.

## Why

Today the tool answers *"can this be cut, and on what machine"*. The natural next
question a customer asks is *"so what do I buy, and what will it look like being cut"*.
Phases 1–3 answer that. Phase 4 is a real CAM system and is called out as out of scope.

---

## Phase 0 — Model scale (prerequisite, ~half a day)

**Blocking problem.** None of the 12 test models carry real units — they measure
0.03–1.0 across, i.e. normalised exports. Every stock number would be meaningless, and
the tooling figure already says "⌀0 units" on some of them.

**Change**

- Frontend: a small control on the result view — *"Longest dimension is ___ mm"*
  (prefilled from the file if it already looks like mm, i.e. max extent ≥ 10), plus a
  unit selector (mm / cm / inch).
- Backend: `scale` (float, model-units → mm) on `/api/analyze/stl`. Multiply extents,
  `rotary_length` and `max_tool_diameter` by it. Nothing else in the engine changes —
  the analysis is scale-invariant.
- Everything downstream then reports real millimetres.

**Reuses:** `dimensions.looks_like_mm` already in the API response.

---

## Phase 1 — Stock recommendation (~1–2 days)

Everything needed is already computed: oriented bbox, mounting face, rotary axis and
length, and the relief flag.

### Rules

**3-axis / relief → rectangular blank**

| Dimension | Rule |
|-----------|------|
| Length / width | model + `2 × side_margin` (default 5 mm, min 10 % on small parts) |
| Thickness | model height + `facing_allowance` (1.5 mm) + `hold_down` (6 mm if screwed through a waste border, 0 if vacuum/tape) |

Then snap **up** to a stocked size (board thicknesses 12/19/25/32/38/50 mm).

**4-axis → round bar**

| Dimension | Rule |
|-----------|------|
| Diameter | max cross-section ⟂ rotary axis × 1.10, min +6 mm |
| Length | part length + `2 × waste_length`, default 30 mm/end (chuck grip + tailstock centre) |

Snap up to stocked bar diameters (40/50/60/75/100 mm). The waste stubs are already drawn
in the viewer — this just puts numbers on them.

**Also cheap to add:** stock volume vs part volume → *"~72 % of the blank becomes chips"*,
and a rough cost line if a price-per-volume is supplied.

### Output

```jsonc
"stock": {
  "type": "round_bar" | "rectangular",
  "dimensions_mm": { "diameter": 65, "length": 220 },   // or l/w/t
  "raw": { "diameter": 61.4, "length": 214 },           // pre-snap
  "waste_each_end_mm": 30,
  "removed_fraction": 0.72,
  "note": "Ø65 × 220 mm bar — part is 160 mm, 30 mm waste at each end"
}
```

Plus a translucent stock box/cylinder in the viewer, toggleable, so the part is seen
inside its blank.

**Decision needed from Neil:** the snap tables. What do TwoTrees customers actually
stock — metric only? Which thicknesses and bar diameters? Everything else is mechanical.

---

## Phase 2 — Reachability sweep animation (~2–3 days)

The cheapest thing that *looks* like a simulation and is honest about what we know.

We already classify every face by which approach direction reaches it. So: step the tool
axis through the rotation (4-axis) or across the setups (3-axis) and progressively light
up the faces that pass become reachable, with a tool-axis indicator sweeping.

- No toolpath engine, no new geometry maths — pure playback of existing per-face data.
- Visualises exactly the analysis the verdict is based on, so it can't over-promise.
- Deliverable: a play/scrub control under the viewer.

**Risk:** users may read it as a real toolpath. Label it *"reachability sweep"*, not
"simulation".

---

## Phase 3 — Real stock-removal preview, reliefs first (~1–2 weeks)

Reliefs are the tractable case, and they are also the most common TwoTrees project.

**Why reliefs are easy:** a relief is a height map `z(x, y)` — we already prove this with
`relief_probe()`. A finishing toolpath is then a raster scan, and the cut result is a
closed-form image operation, not a solid-modelling problem:

1. Sample the model to a height grid (one downward ray per cell — the probe already does
   this).
2. Choose a ball-nose of diameter `d` (default: the `max_tool_diameter` we already
   report).
3. Cut simulation = **grayscale morphological erosion** of the height map by the tool
   profile. Each pass, the tool centre can descend to `min` over its footprint.
4. Render the eroded height map as a mesh next to the target → shows scallop height,
   detail the tool cannot reach, and the effect of stepover.

That gives a genuine, defensible "what will it actually look like" for reliefs, including
*"a ⌀6 ball nose will lose this eyelash detail"* — which is real value, and it reuses the
tooling analysis already built.

**Full 3D / 4-axis stock removal** needs voxels: voxelise the blank, sweep the tool
volume, boolean-subtract. Doable (~2–3 weeks) but memory-heavy — recall the free tier is
512 MB and a 1.9 M-triangle model already peaks near 2 GB. Would need a paid instance and
probably client-side GPU rendering.

---

## Phase 4 — Actual CAM (out of scope)

Roughing/finishing strategies, stepover and stepdown, lead-ins, holder collision,
work-offset handling, post-processors per controller. This is a CAM product, months of
work, and per the project notes it is the intended IP moat — it should be a deliberate
product decision, not a feature bolted onto the checker.

---

## Suggested order & effort

| Phase | What | Effort | Ships value alone? |
|-------|------|--------|--------------------|
| 0 | Model scale input | ~0.5 day | Yes — fixes "⌀0 units" today |
| 1 | Stock recommendation | 1–2 days | Yes — "here's what to buy" |
| 2 | Reachability sweep | 2–3 days | Yes — demo-friendly |
| 3 | Relief cut preview | 1–2 weeks | Yes — real tooling insight |
| 3b | Voxel sim (full 3D/4-axis) | 2–3 weeks | Needs paid hosting |
| 4 | Real CAM | Months | Separate product decision |

**Recommendation:** do 0 + 1 now (≈2 days, unblocks real numbers and answers the customer's
actual next question), then decide between 2 (cheap demo polish) and 3 (real substance)
based on whether this is a sales demo or a tool people will cut from.

## Open questions

1. Stock snap tables — metric only? Which sizes?
2. Hold-down method for 3-axis (screws through waste border / vacuum / tape) — changes the
   thickness allowance.
3. Typical waste-stub length for your 4th axis — I assumed 30 mm/end.
4. Is the goal a **sales demo** or a **working shop tool**? That decides Phase 2 vs 3.
