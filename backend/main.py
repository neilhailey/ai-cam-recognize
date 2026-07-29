"""
FastAPI backend for the CNC Machinability Checker.

Endpoints
  POST /api/analyze/stl    multipart 'file' (.stl/.obj/.ply/.glb) -> report + colored GLB
  POST /api/analyze/photo  multipart 'file' (image) -> qualitative verdict
  GET  /api/files/{sid}/{name}  serve a per-session artifact (the colored GLB)
  GET  /api/health
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from services import machinability as mac
from services import vision

load_dotenv(Path(__file__).parent / ".env")

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

MESH_EXTS = {".stl", ".obj", ".ply", ".glb", ".off", ".3mf"}
MAX_UPLOAD_MB = 150

app = FastAPI(title="CNC Machinability Checker")

# Comma-separated origins via CORS_ORIGINS (set to your Vercel URL in production);
# defaults to "*" so local dev and a quick demo just work.
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Bound heavy CPU/RAM work to one analysis at a time (Render-OOM lesson from the demo).
_analysis_sem = asyncio.Semaphore(1)


# Analysis resolution. Tuned for Render's free tier (512 MB RAM / 0.1 CPU); a
# bigger instance can afford far more detail, which matters because coarse
# meshes lose small undercuts and can misreport 4-axis parts as 5-axis.
# Rough peak RAM per analysis: ~250 MB @ 30k faces, ~700 MB @ 190k, ~2 GB @ 2M.
#   free/starter (512 MB): 30_000     standard (2 GB): 400_000
#   pro (4 GB):            2_000_000  (effectively "no decimation")
MAX_ANALYSIS_FACES = int(os.environ.get("MAX_ANALYSIS_FACES", 30_000))
# Cap for the coarse orientation/setup/tool searches, which run many passes.
SEARCH_FACES = int(os.environ.get("SEARCH_FACES", 6_000))


def _run_stl_analysis(stl_path: str, glb_path: str, pre_simplified: bool = False,
                      flip: bool = False) -> dict:
    """Blocking analysis — runs in a thread executor."""
    mesh = mac.load_mesh(stl_path, max_faces=MAX_ANALYSIS_FACES)

    # Stand the part on its mounting face first, so "+Z" really is straight down
    # onto the workholding and the bed we draw matches what was analysed.
    mesh, mounting = mac.orient_for_machining(mesh, flip=flip)

    quality = mac.mesh_quality(mesh)
    report, face_class = mac.analyze(mesh)
    if bool(mesh.metadata.get("approximate")) or pre_simplified:
        report.caveats.insert(0, "This model was very high-poly, so it was simplified to "
                                 f"~{MAX_ANALYSIS_FACES:,} triangles for analysis. Small "
                                 "undercuts can be lost that way — treat the percentages "
                                 "as the more reliable signal.")

    # For 4-axis, the part is mounted along its longest axis, so lay it down that
    # way for display (a rigid rotation, so the per-face results still apply).
    # The chuck grips waste stock left on each end, not the model itself, so the
    # model keeps its machinability colours all over — the viewer draws the stock.
    display_mesh, chuck_axis = mesh, None
    if report.verdict == mac.VERDICT_4AXIS and report.best_rotary_axis:
        display_mesh = mac.lay_down_along(mesh, report.best_rotary_axis)
        chuck_axis = "x"
    mac.colorize(display_mesh, face_class).export(glb_path)

    # Orientation search / setup planning / tool search run many passes, so use a
    # decimated copy for speed (falls back to full res if the simplifier is absent).
    search_mesh = mac.decimate(mesh, SEARCH_FACES)

    orientation = mac.find_best_orientation(search_mesh)
    setups = mac.plan_setups(search_mesh)
    tooling = mac.max_tool_diameter(search_mesh)

    extents = [round(float(x), 3) for x in display_mesh.extents]
    bounds = [[round(float(v), 3) for v in row] for row in display_mesh.bounds]
    return {
        "report": report.to_dict(),
        "orientation": orientation,
        "setups": setups,
        "tooling": tooling,
        "mounting": mounting,
        "mesh_quality": quality,
        "rotary": {
            # 'axis' is the axis in the DISPLAYED frame: for 4-axis the part is
            # laid down along X, so that is what the viewer draws the chuck on.
            "axis": chuck_axis,
            "model_axis": report.best_rotary_axis,
            "length": round(report.rotary_length, 3),
            "grip_frac": 0.12,
        },
        # Units are whatever the STL used. Below ~10 the file is almost certainly
        # normalised/unitless rather than millimetres, so the UI can stop saying "mm".
        "dimensions": {"extents": extents, "bounds": bounds,
                       "looks_like_mm": bool(max(extents) >= 10.0)},
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "openai_configured": bool(os.environ.get("OPENAI_API_KEY"))}


@app.post("/api/analyze/stl")
async def analyze_stl(file: UploadFile = File(...), pre_simplified: bool = Form(False),
                      flip: bool = Form(False)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in MESH_EXTS:
        raise HTTPException(400, f"Unsupported mesh type '{ext}'. Use one of {sorted(MESH_EXTS)}.")

    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_MB} MB.")

    session_id = uuid.uuid4().hex
    sdir = OUTPUT_DIR / session_id
    sdir.mkdir(parents=True, exist_ok=True)
    stl_path = sdir / f"model{ext}"
    glb_path = sdir / "analysis.glb"
    stl_path.write_bytes(data)

    async with _analysis_sem:
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, _run_stl_analysis, str(stl_path), str(glb_path), pre_simplified, flip
            )
        except Exception as exc:
            raise HTTPException(422, f"Could not analyze mesh: {type(exc).__name__}: {exc}")

    return {
        "session_id": session_id,
        "report": result["report"],
        "orientation": result["orientation"],
        "setups": result["setups"],
        "tooling": result["tooling"],
        "mounting": result["mounting"],
        "mesh_quality": result["mesh_quality"],
        "rotary": result["rotary"],
        "dimensions": result["dimensions"],
        "glb_url": f"/api/files/{session_id}/analysis.glb",
        "legend": {
            "3axis": "green - reachable straight down (3-axis)",
            "4axis": "amber - needs a rotary/radial approach (4-axis)",
            "5axis": "red - undercut, needs a tilted approach (5-axis)",
            "enclosed": "black - fully enclosed, no external tool can reach",
            "fixture": "grey - mounting face, clamped (excluded)",
        },
    }


@app.post("/api/analyze/photo")
async def analyze_photo(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_MB} MB.")
    mime = file.content_type or "image/png"
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, vision.assess_image, data, mime)
    return {"result": result}


@app.get("/api/files/{session_id}/{filename}")
def get_file(session_id: str, filename: str):
    # guard against path traversal
    if "/" in session_id or "/" in filename or ".." in session_id or ".." in filename:
        raise HTTPException(400, "Invalid path.")
    path = OUTPUT_DIR / session_id / filename
    if not path.is_file():
        raise HTTPException(404, "Not found.")
    media = "model/gltf-binary" if filename.endswith(".glb") else None
    return FileResponse(path, media_type=media)
