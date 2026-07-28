# CNC Machinability Checker

Upload an **STL model** or a **photo** and find out whether the part can be cut on a
**3-axis** or **4-axis** CNC router — or whether it needs **5-axis** because of
undercuts.

- **STL path** — a rigorous, geometry-based accessibility (undercut) analysis. This is
  the definitive answer.
- **Photo path** — a quick qualitative AI pre-screen from a single image (a photo can't
  prove undercuts, so it points you back to the STL for certainty).

## How the STL analysis works

A router's tool approaches the part along its **tool axis**. A surface region is
machinable from an approach direction `d` only if the tool can reach it without passing
through other material — i.e. the region is **visible along `d`**. Undercuts are the
regions not visible from the directions a given machine class allows:

| Class    | Allowed approach directions                          |
|----------|------------------------------------------------------|
| 3-axis   | straight down `+Z` (and `-Z` if you flip the stock)  |
| 4-axis   | any direction perpendicular to one rotary axis (X/Y) |
| 5-axis   | (almost) any direction on the sphere                 |

For every triangle the engine runs a cheap **normal-facing filter** then a **ray-cast
occlusion test** against the allowed directions, testing the cheap classes first and
only re-testing the facets that failed. It reports a verdict, the machinable surface-area
% per class, the best rotary axis, and flags for enclosed cavities and vertical walls.
The colored result mesh (green = 3-axis, amber = needs 4-axis, red = undercut/5-axis,
black = enclosed) is shown in the 3D viewer.

Alongside the verdict it also reports:

- **Orientation search** — tries many "up" orientations (bounding-box + convex-hull
  normals) and tells you if re-orienting the stock lowers the required axis count.
- **3-axis setup plan** — greedy set-cover of single tool directions: the minimum number
  of 3-axis setups (flips) that cover the part, and how much (if any) no single setup can
  reach (true 5-axis).
- **Tooling guidance** — the largest cutter that still reaches essentially all the detail
  (binary search over tool diameter), so you get "use a tool ≤ X" without having to guess.

**What it does _not_ model:** finite tool *length*, shank/holder collision, or exact flute
geometry — so a region shown as reachable may still need a long/slender tool. The verdict
is for the model's current up-orientation (the orientation search explores alternatives).

Core engine: [`backend/services/machinability.py`](backend/services/machinability.py)
(pure, unit-tested). Uses `trimesh`; ray casting via `embreex` (fast) with an automatic
pure-Python fallback.

## Share it / deploy

To send this to someone as a link (Render backend + Vercel frontend), follow
[DEPLOY.md](DEPLOY.md). The bundled sample models ship with the frontend, so they can try
it without their own STL.

## Run it

Two processes: the FastAPI backend (port 8000) and the Vite frontend (port 5174, which
proxies `/api` → 8000).

### Backend

```bash
cd AI_CAM_Recognize/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # optional: add OPENAI_API_KEY for the photo path
.venv/bin/python -m uvicorn main:app --port 8000
```

### Frontend

```bash
cd AI_CAM_Recognize/frontend
npm install
npm run dev                     # http://localhost:5174
```

Then open http://localhost:5174 and drop in an STL or a photo. Ready-made test shapes
live in [`frontend/public/samples/`](frontend/public/samples/) (`box`, `cross_drilled`,
`tilted_pocket`, `hollow_sphere`, `mushroom.png`).

> The `.claude/launch.json` at the repo root has `cam-backend` / `cam-frontend` entries.
> `cam-frontend` works with the in-app preview; the backend is best started from a
> terminal as above (the preview sandbox can't read the venv).

## Tests

```bash
cd AI_CAM_Recognize/backend
.venv/bin/python -m pytest tests/ -q
```

Known-answer meshes (built procedurally, no external files): box & sphere → 3-axis,
cross-drilled cylinder → 4-axis, diagonally-tilted blind pocket → 5-axis, hollow sphere →
enclosed cavity flagged.

## API

| Method | Path                              | Body            | Returns                              |
|--------|-----------------------------------|-----------------|--------------------------------------|
| POST   | `/api/analyze/stl`                | multipart `file`| report + `glb_url` (colored mesh)    |
| POST   | `/api/analyze/photo`              | multipart `file`| qualitative verdict + confidence     |
| GET    | `/api/files/{session_id}/{name}`  | —               | the colored analysis GLB             |
| GET    | `/api/health`                     | —               | status + whether OpenAI is configured|

## Possible next steps

- Full tool-collision modeling (finite length + holder), beyond the current diameter check.
- Continuous orientation optimization (search all of SO(3), not just candidate up-axes).
- Suggest *where* to split a "5-axis" part into 3-axis-machinable pieces.
