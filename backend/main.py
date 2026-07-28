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
from fastapi import FastAPI, File, HTTPException, UploadFile
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


SEARCH_FACES = 6_000   # decimation cap for the (multi-run) orientation/setup search


def _run_stl_analysis(stl_path: str, glb_path: str) -> dict:
    """Blocking analysis — runs in a thread executor."""
    mesh = mac.load_mesh(stl_path)

    # Very high-poly meshes are simplified by vertex-clustering to fit memory;
    # that adds small surface noise, so use a looser area tolerance and flag it.
    approximate = bool(mesh.metadata.get("approximate"))
    report, face_class = mac.analyze(mesh, area_tol=0.02 if approximate else 0.005)
    if approximate:
        report.caveats.insert(0, "This model was very high-poly, so it was simplified "
                                 "for analysis — the verdict is approximate and the "
                                 "percentages are the more reliable signal. For an exact "
                                 "result, upload a mesh under ~30k triangles.")
    mac.colorize(mesh, face_class).export(glb_path)

    # Orientation search / setup planning / tool search run many passes, so use a
    # decimated copy for speed (falls back to full res if the simplifier is absent).
    search_mesh = mac.decimate(mesh, SEARCH_FACES)

    orientation = mac.find_best_orientation(search_mesh)
    setups = mac.plan_setups(search_mesh)
    tooling = mac.max_tool_diameter(search_mesh)

    return {
        "report": report.to_dict(),
        "orientation": orientation,
        "setups": setups,
        "tooling": tooling,
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "openai_configured": bool(os.environ.get("OPENAI_API_KEY"))}


@app.post("/api/analyze/stl")
async def analyze_stl(file: UploadFile = File(...)):
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
                None, _run_stl_analysis, str(stl_path), str(glb_path)
            )
        except Exception as exc:
            raise HTTPException(422, f"Could not analyze mesh: {type(exc).__name__}: {exc}")

    return {
        "session_id": session_id,
        "report": result["report"],
        "orientation": result["orientation"],
        "setups": result["setups"],
        "tooling": result["tooling"],
        "glb_url": f"/api/files/{session_id}/analysis.glb",
        "legend": {
            "3axis": "green - reachable straight down (3-axis)",
            "4axis": "amber - needs a rotary/radial approach (4-axis)",
            "5axis": "red - undercut, needs a tilted approach (5-axis)",
            "enclosed": "black - fully enclosed, no external tool can reach",
            "fixture": "grey - bottom / bed-contact face (excluded)",
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
