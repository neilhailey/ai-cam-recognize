"""
CNC machinability (undercut / accessibility) analysis engine.

Given a triangle mesh, decide whether it can be cut on a 3-axis or 4-axis CNC
router, or whether it needs 5-axis because of undercuts.

Core idea
---------
A surface facet is machinable from an approach direction ``d`` (the tool comes
from ``+d`` and travels along ``-d`` into the part) iff:

  1. FACING     the facet is not clearly pointing away from the tool:
                ``n . d >= -eps_face``  (vertical walls, n.d ~= 0, are kept —
                they are cut by the side of the tool).
  2. VISIBILITY a ray from the facet centroid along ``+d`` escapes the part
                without hitting other material first (no shadowing / occlusion).

Machine classes differ only in the *set of allowed approach directions*:

  * 3-axis  : {+Z}            (single setup) or {+Z, -Z} if the stock is flipped
  * 4-axis  : every direction perpendicular to one rotary axis (X or Y)
  * 5-axis  : (almost) any direction on the sphere

A facet is machinable for a class iff it is machinable from *some* direction in
that class. We test the cheap classes first and only re-test the facets that
failed, so the expensive 5-axis sphere is only ever cast against the leftovers.

This models *tool-axis accessibility* — the dominant factor for undercuts. It
does NOT model tool diameter/length, shank/holder collision, or deep-narrow
pocket reach. Those limitations are surfaced to the caller as caveats.

The module is pure (no FastAPI import) so it can be unit-tested directly.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import trimesh

# ---------------------------------------------------------------------------
# Facet class labels (also used to pick colors for the exported mesh)
# ---------------------------------------------------------------------------
CLASS_3AXIS = 0      # reachable straight down (or up, if flip allowed)
CLASS_4AXIS = 1      # needs a rotary (radial) approach
CLASS_5AXIS = 2      # needs a tilted approach (true undercut)
CLASS_ENCLOSED = 3   # reachable from no direction at all (internal cavity)
CLASS_FIXTURE = 4    # bed-contact / bottom face, excluded from the verdict

CLASS_COLORS = {
    CLASS_3AXIS:    [ 76, 175,  80, 255],   # green
    CLASS_4AXIS:    [255, 193,   7, 255],   # amber
    CLASS_5AXIS:    [244,  67,  54, 255],   # red
    CLASS_ENCLOSED: [ 33,  33,  33, 255],   # near-black
    CLASS_FIXTURE:  [150, 150, 150, 255],   # grey
}

VERDICT_3AXIS = "3-axis"
VERDICT_4AXIS = "4-axis"
VERDICT_5AXIS = "5-axis"


@dataclass
class AnalysisReport:
    verdict: str                       # VERDICT_3AXIS / _4AXIS / _5AXIS
    verdict_label: str                 # human string, e.g. "3-axis (2-sided)"
    machinable_pct: dict               # {"3axis": .., "4axis": .., "5axis": ..} cumulative %
    best_rotary_axis: Optional[str]    # "x" / "y" / None
    enclosed_pct: float                # % area reachable from no direction
    vertical_wall_pct: float           # % area that is near-vertical (long-tool flag)
    face_area: float                   # total considered surface area (excl. fixture)
    n_faces: int
    ray_backend: str                   # "embree" or "trimesh"
    caveats: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "verdict_label": self.verdict_label,
            "machinable_pct": self.machinable_pct,
            "best_rotary_axis": self.best_rotary_axis,
            "enclosed_pct": round(self.enclosed_pct, 2),
            "vertical_wall_pct": round(self.vertical_wall_pct, 2),
            "n_faces": self.n_faces,
            "ray_backend": self.ray_backend,
            "caveats": self.caveats,
        }


# ---------------------------------------------------------------------------
# Ray backend (embree if available, else trimesh's pure-python caster)
# ---------------------------------------------------------------------------
def _make_intersector(mesh: trimesh.Trimesh):
    """Return (intersector, backend_name). Prefers embree for speed."""
    try:
        from trimesh.ray.ray_pyembree import RayMeshIntersector
        return RayMeshIntersector(mesh), "embree"
    except Exception:
        from trimesh.ray.ray_triangle import RayMeshIntersector
        return RayMeshIntersector(mesh), "trimesh"


# ---------------------------------------------------------------------------
# Direction sets
# ---------------------------------------------------------------------------
def _radial_directions(axis: str, step_deg: float) -> np.ndarray:
    """Directions perpendicular to a rotary ``axis`` ('x' or 'y'), one per step."""
    angles = np.deg2rad(np.arange(0.0, 360.0, step_deg))
    c, s = np.cos(angles), np.sin(angles)
    z = np.zeros_like(angles)
    if axis == "x":            # rotate in the Y-Z plane
        dirs = np.column_stack([z, c, s])
    elif axis == "y":          # rotate in the X-Z plane
        dirs = np.column_stack([c, z, s])
    else:
        raise ValueError(f"rotary axis must be 'x' or 'y', got {axis!r}")
    return dirs


def _fibonacci_sphere(n: int) -> np.ndarray:
    """``n`` roughly-uniform unit directions on the sphere."""
    i = np.arange(n) + 0.5
    phi = np.arccos(1.0 - 2.0 * i / n)
    golden = math.pi * (1.0 + math.sqrt(5.0))
    theta = golden * i
    return np.column_stack([
        np.sin(phi) * np.cos(theta),
        np.sin(phi) * np.sin(theta),
        np.cos(phi),
    ])


def _perp_basis(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors spanning the plane perpendicular to direction ``d``."""
    helper = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(d, helper)
    u /= (np.linalg.norm(u) or 1.0)
    v = np.cross(d, u)
    v /= (np.linalg.norm(v) or 1.0)
    return u, v


def _perp_basis_many(dirs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized :func:`_perp_basis` for an array of directions (n, 3)."""
    helper = np.where(
        (np.abs(dirs[:, 2]) < 0.9)[:, None],
        np.array([0.0, 0.0, 1.0]),
        np.array([1.0, 0.0, 0.0]),
    )
    u = np.cross(dirs, helper)
    u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-12)
    v = np.cross(dirs, u)
    v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
    return u, v


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def decimate(mesh: trimesh.Trimesh, target_faces: int) -> trimesh.Trimesh:
    """Reduce a mesh to ~``target_faces`` for speed, if it is larger.

    Calls the optional ``fast_simplification`` backend directly (trimesh's own
    ``simplify_quadric_decimation`` wrapper has a version-fragile arg mapping).
    If the backend is missing or fails, fall back to the full-resolution mesh —
    slower, but correct — rather than crashing.
    """
    if len(mesh.faces) <= target_faces:
        return mesh
    try:
        import fast_simplification as fs
        v, f = fs.simplify(
            np.asarray(mesh.vertices, dtype=np.float64),
            np.asarray(mesh.faces, dtype=np.int64),
            target_count=int(target_faces),
        )
        out = trimesh.Trimesh(vertices=v, faces=f, process=False)
        out.merge_vertices()
        out.fix_normals()
        return out
    except Exception:
        return mesh


def binary_stl_face_count(path: str) -> Optional[int]:
    """Return the triangle count if ``path`` is a binary STL, else None.

    A binary STL is exactly ``84 + 50 * n`` bytes, which both identifies the
    format and lets us decide, without loading, whether a file is too large.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            header = fh.read(84)
        if len(header) < 84:
            return None
        n = int(np.frombuffer(header[80:84], dtype="<u4")[0])
        return n if size == 84 + 50 * n else None
    except Exception:
        return None


def _cluster_reduce_binary_stl(path: str, target_faces: int, chunk: int = 200_000) -> trimesh.Trimesh:
    """Low-memory reduction of a big binary STL via streaming vertex clustering.

    Reads the file in chunks (never holding all vertices at once) and snaps
    vertices to a grid, so peak memory stays a few hundred MB regardless of file
    size — letting a 512 MB host process multi-million-triangle meshes that would
    otherwise OOM. Clustering is lower quality than quadric decimation but fine
    for accessibility analysis. Cell centres are used as representative vertices.
    """
    with open(path, "rb") as fh:
        n = int(np.frombuffer(fh.read(84)[80:84], dtype="<u4")[0])

        def _chunk_verts():
            fh.seek(84)
            remaining = n
            while remaining:
                c = min(chunk, remaining)
                buf = np.frombuffer(fh.read(c * 50), dtype=np.uint8).reshape(c, 50)
                yield np.frombuffer(buf[:, 12:48].tobytes(), dtype="<f4").reshape(c * 3, 3)
                remaining -= c

        # Pass 1: bounding box (streamed).
        mn = np.full(3, np.inf)
        mx = np.full(3, -np.inf)
        for v in _chunk_verts():
            mn = np.minimum(mn, v.min(0))
            mx = np.maximum(mx, v.max(0))
        diag = float(np.linalg.norm(mx - mn)) or 1.0
        res = diag / max(1.0, (target_faces ** 0.5) * 0.55)    # grid cell ~ target density
        span = (np.floor((mx - mn) / res).astype(np.int64) + 2)

        def _pack(v):
            g = np.floor((v - mn) / res).astype(np.int64)
            return (g[:, 0] * span[1] + g[:, 1]) * span[2] + g[:, 2]

        # Pass 2: collect all cell keys, reduce to the unique set (then freed).
        keys = np.empty(n * 3, dtype=np.int64)
        off = 0
        for v in _chunk_verts():
            k = _pack(v)
            keys[off:off + len(k)] = k
            off += len(k)
        uniq = np.unique(keys)
        del keys

        # Pass 3: map each face's 3 vertices to unique-cell indices (streamed).
        faces = np.empty((n, 3), dtype=np.int64)
        off = 0
        for v in _chunk_verts():
            c = len(v) // 3
            idx = np.searchsorted(uniq, _pack(v))
            faces[off:off + c] = idx.reshape(c, 3)
            off += c

    # Decode unique cell keys back to grid coords → cell-centre vertices.
    gz = uniq % span[2]
    t = uniq // span[2]
    gy = t % span[1]
    gx = t // span[1]
    rep = mn + (np.stack([gx, gy, gz], axis=1) + 0.5) * res

    keep = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    mesh = trimesh.Trimesh(vertices=rep, faces=faces[keep], process=False)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    mesh.fix_normals()
    mesh.metadata["approximate"] = True    # clustered: verdict uses a looser tolerance
    return mesh


def load_mesh(path: str, max_faces: int = 150_000) -> trimesh.Trimesh:
    """Load an STL/mesh file, weld it, fix normals, and cap face count.

    Large binary STLs take a low-memory vertex-clustering path so a small host
    can process them; everything else uses trimesh's loader + quadric decimation.
    """
    n_bin = binary_stl_face_count(path)
    if n_bin is not None and n_bin > max_faces:
        return _cluster_reduce_binary_stl(path, max_faces)

    loaded = trimesh.load(path, force="mesh")
    if isinstance(loaded, trimesh.Scene):
        loaded = loaded.dump(concatenate=True)
    mesh: trimesh.Trimesh = loaded
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    mesh.fix_normals()
    return decimate(mesh, max_faces)


# ---------------------------------------------------------------------------
# Core reachability test
# ---------------------------------------------------------------------------
# Upper bound on rays per intersects_any call. Ray casting is dominated by
# per-call overhead (very visible on throttled shared CPUs), so we batch many
# directions per call; this caps the temporary array size.
MAX_RAYS_PER_CALL = 1_500_000


def _reachable_from_any(
    intersector,
    centroids: np.ndarray,
    normals: np.ndarray,
    candidate_idx: np.ndarray,
    directions: np.ndarray,
    diagonal: float,
    eps_face: float,
    offset: float,
    tool_radius: float = 0.0,
    ring_k: int = 6,
    facing_min: Optional[float] = None,
) -> np.ndarray:
    """
    For the facets in ``candidate_idx``, return a boolean array (aligned to
    ``candidate_idx``) that is True where the facet is machinable from at least
    one of ``directions``.

    For each direction we (a) keep only facets whose normal passes the facing
    filter and (b) cast one ray per surviving facet (plus a ring of rays if
    ``tool_radius`` > 0) and mark those that escape.

    ``facing_min`` is the minimum ``n·d`` to consider a facet reached. Default
    ``-eps_face`` keeps grazing/vertical walls (correct for the axis verdict with
    an ideal side-cutting tool). A stricter positive value is used for the
    finite-tool check, where a grazing approach would put the tool body inside
    the wall.
    """
    if facing_min is None:
        facing_min = -eps_face
    reached = np.zeros(len(candidate_idx), dtype=bool)
    cand_normals = normals[candidate_idx]
    cand_centroids = centroids[candidate_idx]
    directions = np.atleast_2d(directions)

    # Ray casting is dominated by per-call overhead, so batch many directions
    # into each intersects_any call instead of one call per direction. Chunk the
    # directions so the flattened (face, direction) ray array stays bounded.
    n_cand = max(1, len(candidate_idx))
    per_chunk = max(1, min(len(directions), MAX_RAYS_PER_CALL // n_cand))
    lift = offset + tool_radius
    u_all, v_all = (_perp_basis_many(directions) if tool_radius > 0 else (None, None))

    for start in range(0, len(directions), per_chunk):
        todo = ~reached
        if not todo.any():
            break
        chunk = directions[start:start + per_chunk]

        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            facing = (cand_normals @ chunk.T) >= facing_min      # (n_cand, k)
        active = facing & todo[:, None]
        if not active.any():
            continue

        fi, di = np.nonzero(active)                              # face / direction pairs
        origins = cand_centroids[fi] + cand_normals[fi] * lift
        ray_dirs = chunk[di]

        blocked = np.asarray(
            intersector.intersects_any(ray_origins=origins, ray_directions=ray_dirs),
            dtype=bool,
        )
        if tool_radius > 0:
            u = u_all[start:start + per_chunk][di]
            v = v_all[start:start + per_chunk][di]
            for k in range(ring_k):
                if blocked.all():
                    break
                a = 2.0 * math.pi * k / ring_k
                off = tool_radius * (math.cos(a) * u + math.sin(a) * v)
                blocked |= np.asarray(
                    intersector.intersects_any(
                        ray_origins=origins + off, ray_directions=ray_dirs),
                    dtype=bool,
                )

        reached[fi[~blocked]] = True

    return reached


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def analyze(
    mesh: trimesh.Trimesh,
    allow_flip: bool = True,
    rotary_axes: tuple = ("x", "y"),
    radial_step_deg: float = 3.0,
    sphere_dirs: int = 400,
    area_tol: float = 0.005,          # 0.5% of area may be unreachable and still "pass"
    vertical_deg: float = 10.0,       # within this of vertical => "vertical wall"
    tool_diameter: float = 0.0,       # in model units; 0 = ideal point tool (visibility only)
) -> tuple[AnalysisReport, np.ndarray]:
    """
    Classify every facet and produce a verdict.

    Returns ``(report, face_class)`` where ``face_class`` is an int array of
    CLASS_* labels, one per mesh face (usable directly for colorizing).
    """
    intersector, backend = _make_intersector(mesh)

    normals = np.nan_to_num(np.asarray(mesh.face_normals, dtype=np.float64), posinf=0.0, neginf=0.0)
    centroids = np.asarray(mesh.triangles_center, dtype=np.float64)
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    n = len(normals)

    diagonal = float(np.linalg.norm(mesh.extents)) or 1.0
    offset = diagonal * 1e-4          # nudge ray origin off the surface
    eps_face = math.sin(math.radians(1.0))   # tolerate ~1 deg past vertical
    tool_radius = max(0.0, tool_diameter) / 2.0

    face_class = np.full(n, CLASS_ENCLOSED, dtype=np.int64)

    # --- fixturing: the bottom faces sit on the bed and are not machined ------
    z = centroids[:, 2]
    z_min = float(z.min())
    z_span = max(float(z.max()) - z_min, 1e-9)
    down = normals[:, 2] < -math.cos(math.radians(vertical_deg))   # points down
    near_floor = z < (z_min + 0.02 * z_span)
    fixture = down & near_floor
    face_class[fixture] = CLASS_FIXTURE

    considered = np.nonzero(~fixture)[0]
    total_area = float(areas[considered].sum()) or 1.0
    tol_area = area_tol * total_area

    def area_of(mask_idx) -> float:
        return float(areas[mask_idx].sum())

    # --- 3-axis: {+Z} (and -Z if flip allowed) --------------------------------
    z_dirs = np.array([[0, 0, 1.0]] + ([[0, 0, -1.0]] if allow_flip else []))
    ok3 = _reachable_from_any(intersector, centroids, normals, considered,
                              z_dirs, diagonal, eps_face, offset, tool_radius)
    three_idx = considered[ok3]
    face_class[three_idx] = CLASS_3AXIS

    remaining = considered[~ok3]

    # --- 4-axis: radial directions around the best rotary axis -----------------
    best_axis = None
    best_ok4 = None
    best_gained = -1.0
    if len(remaining):
        for axis in rotary_axes:
            dirs = _radial_directions(axis, radial_step_deg)
            ok4 = _reachable_from_any(intersector, centroids, normals, remaining,
                                      dirs, diagonal, eps_face, offset, tool_radius)
            gained = area_of(remaining[ok4])
            if gained > best_gained:
                best_gained, best_axis, best_ok4 = gained, axis, ok4

    if best_ok4 is not None:
        four_idx = remaining[best_ok4]
        face_class[four_idx] = CLASS_4AXIS
        remaining = remaining[~best_ok4]

    # --- 5-axis: full sphere on the leftovers ---------------------------------
    if len(remaining):
        dirs = _fibonacci_sphere(sphere_dirs)
        ok5 = _reachable_from_any(intersector, centroids, normals, remaining,
                                  dirs, diagonal, eps_face, offset, tool_radius)
        face_class[remaining[ok5]] = CLASS_5AXIS
        # whatever is still unreached stays CLASS_ENCLOSED

    # --- aggregate areas / verdict --------------------------------------------
    a3 = area_of(np.nonzero(face_class == CLASS_3AXIS)[0])
    a4 = area_of(np.nonzero(face_class == CLASS_4AXIS)[0])
    a5 = area_of(np.nonzero(face_class == CLASS_5AXIS)[0])
    aenc = area_of(np.nonzero(face_class == CLASS_ENCLOSED)[0])

    pct3 = 100.0 * a3 / total_area
    pct4 = 100.0 * (a3 + a4) / total_area          # cumulative: 4-axis machine can do 3-axis work
    pct5 = 100.0 * (a3 + a4 + a5) / total_area
    enclosed_pct = 100.0 * aenc / total_area

    if (total_area - a3) <= tol_area:
        verdict = VERDICT_3AXIS
    elif (total_area - a3 - a4) <= tol_area:
        verdict = VERDICT_4AXIS
    else:
        verdict = VERDICT_5AXIS

    # vertical-wall flag (long-tool hint) — near-vertical, non-fixture faces
    vertical = np.abs(normals[:, 2]) < math.sin(math.radians(vertical_deg))
    vertical_area = area_of(np.nonzero(vertical & (~fixture))[0])
    vertical_pct = 100.0 * vertical_area / total_area

    label = {
        VERDICT_3AXIS: "3-axis" + (" (2-sided)" if allow_flip and pct3 > 50 else ""),
        VERDICT_4AXIS: f"4-axis (rotary about {(best_axis or 'x').upper()})",
        VERDICT_5AXIS: "5-axis required",
    }[verdict]

    if tool_radius > 0:
        caveats = [
            f"Includes a first-order tool-diameter check (⌀{tool_diameter:g} model "
            "units): a region only counts as reachable if the tool body clears it. "
            "Still ignores finite tool length, holder collision, and exact flute geometry.",
            "Verdict is for the model's current up-orientation; re-orienting the "
            "stock can change the answer.",
        ]
    else:
        caveats = [
            "Models tool-axis accessibility with an ideal point tool. It does not "
            "check tool diameter/length, shank or holder collision, so a region shown "
            "as reachable may still need a long/slender tool. Set a tool diameter to "
            "include a first-order width check.",
            "Verdict is for the model's current up-orientation; re-orienting the "
            "stock can change the answer.",
        ]
    if allow_flip:
        caveats.append("Assumes the stock can be flipped once (2-sided setup) for "
                       "3-axis work; the bottom bed-contact face is excluded.")
    if enclosed_pct > 0.5:
        caveats.append(f"~{enclosed_pct:.0f}% of the surface bounds a fully enclosed "
                       "cavity that no external tool can reach without splitting the stock.")
    if vertical_pct > 10:
        caveats.append(f"~{vertical_pct:.0f}% of the surface is near-vertical wall — "
                       "machinable but may require long or thin tooling.")

    report = AnalysisReport(
        verdict=verdict,
        verdict_label=label,
        machinable_pct={"3axis": round(pct3, 1), "4axis": round(pct4, 1), "5axis": round(pct5, 1)},
        best_rotary_axis=best_axis if verdict != VERDICT_3AXIS else None,
        enclosed_pct=enclosed_pct,
        vertical_wall_pct=vertical_pct,
        face_area=total_area,
        n_faces=n,
        ray_backend=backend,
        caveats=caveats,
    )
    return report, face_class


# ---------------------------------------------------------------------------
# Colorized export
# ---------------------------------------------------------------------------
def colorize(mesh: trimesh.Trimesh, face_class: np.ndarray) -> trimesh.Trimesh:
    """Return a copy of ``mesh`` with per-face colors set from CLASS_COLORS."""
    colored = mesh.copy()
    colors = np.array([CLASS_COLORS[int(c)] for c in face_class], dtype=np.uint8)
    colored.visual.face_colors = colors
    return colored


def analyze_file(path: str, glb_out: Optional[str] = None, **kwargs) -> AnalysisReport:
    """Convenience: load, analyze, optionally write a colored GLB. Returns report."""
    mesh = load_mesh(path)
    report, face_class = analyze(mesh, **kwargs)
    if glb_out:
        colorize(mesh, face_class).export(glb_out)
    return report


# ---------------------------------------------------------------------------
# Candidate directions + human naming
# ---------------------------------------------------------------------------
_TIER = {VERDICT_3AXIS: 0, VERDICT_4AXIS: 1, VERDICT_5AXIS: 2}
_AXIS_DIRS = np.array([
    [1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1.0],
])
_AXIS_NAMES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]


def _name_dir(v: np.ndarray) -> str:
    """Human name for a direction: an axis label if close, else 'oblique'."""
    v = v / (np.linalg.norm(v) or 1.0)
    dots = _AXIS_DIRS @ v
    k = int(np.argmax(dots))
    return _AXIS_NAMES[k] if dots[k] > 0.966 else "oblique"   # within ~15 deg


def _candidate_up_directions(mesh: trimesh.Trimesh, max_n: int = 10) -> np.ndarray:
    """Axis-aligned directions plus the largest convex-hull face normals, deduped."""
    dirs = list(_AXIS_DIRS)
    try:
        hull = mesh.convex_hull
        order = np.argsort(-hull.area_faces)
        for i in order:
            dirs.append(np.asarray(hull.face_normals[i], dtype=np.float64))
    except Exception:
        pass

    kept: list = []
    for d in dirs:
        d = d / (np.linalg.norm(d) or 1.0)
        if all(float(d @ k) < 0.966 for k in kept):    # >15 deg from everything kept
            kept.append(d)
        if len(kept) >= max_n:
            break
    return np.array(kept)


def _prep(mesh: trimesh.Trimesh):
    intersector, backend = _make_intersector(mesh)
    normals = np.nan_to_num(np.asarray(mesh.face_normals, dtype=np.float64), posinf=0.0, neginf=0.0)
    centroids = np.asarray(mesh.triangles_center, dtype=np.float64)
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    diagonal = float(np.linalg.norm(mesh.extents)) or 1.0
    return intersector, normals, centroids, areas, diagonal, backend


# ---------------------------------------------------------------------------
# Orientation search — find the up-orientation needing the fewest axes
# ---------------------------------------------------------------------------
def find_best_orientation(
    mesh: trimesh.Trimesh,
    radial_step_deg: float = 15.0,
    sphere_dirs: int = 80,
) -> dict:
    """
    Try several 'up' orientations and return the one requiring the lowest axis
    class. ``mesh`` should already be decimated for speed. Returns a dict with the
    current verdict, the best verdict, and how to re-orient to reach it.
    """
    current, _ = analyze(mesh, radial_step_deg=radial_step_deg, sphere_dirs=sphere_dirs)
    candidates = _candidate_up_directions(mesh)

    results = []
    for up in candidates:
        R = trimesh.geometry.align_vectors(up, np.array([0.0, 0.0, 1.0]))
        rot = mesh.copy()
        rot.apply_transform(R)
        rep, _ = analyze(rot, radial_step_deg=radial_step_deg, sphere_dirs=sphere_dirs)
        results.append((up, rep))

    def rank(item):
        up, rep = item
        # lower tier is better; then more 3-axis area, then more 4-axis area
        return (_TIER[rep.verdict], -rep.machinable_pct["3axis"], -rep.machinable_pct["4axis"])

    results.sort(key=rank)
    best_up, best_rep = results[0]

    is_current = _name_dir(best_up) == "+Z"
    improved = _TIER[best_rep.verdict] < _TIER[current.verdict]

    if is_current or not improved:
        description = "The model is already oriented for the fewest axes."
    else:
        name = _name_dir(best_up)
        where = f"the model's {name} side" if name != "oblique" else "an oblique orientation"
        description = (f"Rotating so {where} faces up drops it from "
                       f"{current.verdict_label} to {best_rep.verdict_label}.")

    return {
        "current_verdict": current.verdict,
        "current_verdict_label": current.verdict_label,
        "best_verdict": best_rep.verdict,
        "best_verdict_label": best_rep.verdict_label,
        "best_up_vector": [round(float(x), 3) for x in best_up],
        "best_up_name": _name_dir(best_up),
        "improved": bool(improved),
        "description": description,
    }


# ---------------------------------------------------------------------------
# Setup planning — how many 3-axis setups (flips) cover the part?
# ---------------------------------------------------------------------------
def plan_setups(
    mesh: trimesh.Trimesh,
    coverage_target: float = 0.99,
    max_setups: int = 6,
) -> dict:
    """
    Greedy set-cover: the minimum number of single-direction 3-axis setups whose
    combined visibility covers (nearly) the whole surface. Whatever no single
    direction can reach needs true 5-axis motion (or splitting the stock).
    ``mesh`` should already be decimated for speed.
    """
    intersector, normals, centroids, areas, diagonal, _ = _prep(mesh)
    offset = diagonal * 1e-4
    eps_face = math.sin(math.radians(1.0))
    all_idx = np.arange(len(normals))
    total = float(areas.sum()) or 1.0

    # candidate setup directions: the up-candidates and their negatives
    ups = _candidate_up_directions(mesh)
    dirs = np.vstack([ups, -ups])
    deduped: list = []
    for d in dirs:
        if all(float(d @ k) < 0.999 for k in deduped):
            deduped.append(d)

    masks = [
        _reachable_from_any(intersector, centroids, normals, all_idx,
                            np.array([d]), diagonal, eps_face, offset)
        for d in deduped
    ]

    covered = np.zeros(len(all_idx), dtype=bool)
    chosen: list = []
    steps: list = []
    while len(chosen) < max_setups:
        best_i, best_gain = -1, 0.0
        for i, m in enumerate(masks):
            if i in [c[0] for c in chosen]:
                continue
            gain = float(areas[m & ~covered].sum())
            if gain > best_gain:
                best_gain, best_i = gain, i
        if best_i < 0 or best_gain <= total * 1e-4:
            break
        covered |= masks[best_i]
        chosen.append((best_i, deduped[best_i]))
        cov = float(areas[covered].sum()) / total
        steps.append({"direction": _name_dir(deduped[best_i]),
                      "vector": [round(float(x), 3) for x in deduped[best_i]],
                      "cumulative_coverage_pct": round(100.0 * cov, 1)})
        if cov >= coverage_target:
            break

    uncovered_pct = round(100.0 * float(areas[~covered].sum()) / total, 1)
    return {
        "n_setups": len(steps),
        "setups": steps,
        "uncoverable_pct": uncovered_pct,
        "fully_covered": uncovered_pct <= (1.0 - coverage_target) * 100.0 + 1e-6,
    }


# ---------------------------------------------------------------------------
# Tooling constraint — largest cutter that still reaches all the detail
# ---------------------------------------------------------------------------
def max_tool_diameter(
    mesh: trimesh.Trimesh,
    sphere_dirs: int = 48,
    tol: float = 0.05,
    iterations: int = 7,
) -> dict:
    """
    Estimate the largest tool diameter that still reaches essentially all the
    detail an ideal point tool can reach (within ``tol`` of its area) — the
    tooling limit the part's finest concave features impose. Answers "use a
    cutter around X or smaller" without the user needing to know their tool.

    ``tol`` is deliberately a few percent: a finite tool always leaves an uncut
    fillet in sharp internal corners, so demanding 100% coverage would report an
    unrealistically tiny tool. The result is approximate guidance, not a spec.
    ``mesh`` should already be decimated for speed. Coverage shrinks monotonically
    with diameter, so we binary-search.
    """
    intersector, normals, centroids, areas, diagonal, _ = _prep(mesh)
    offset = diagonal * 1e-4
    eps_face = math.sin(math.radians(1.0))
    all_idx = np.arange(len(normals))
    total = float(areas.sum()) or 1.0
    dirs = _fibonacci_sphere(sphere_dirs)
    # Only count head-on approaches for the finite-tool test (a grazing tool would
    # bury its body in the wall). ~cos(75 deg).
    facing_min = math.cos(math.radians(75.0))

    def covered_area(radius: float) -> float:
        m = _reachable_from_any(intersector, centroids, normals, all_idx,
                                dirs, diagonal, eps_face, offset, tool_radius=radius,
                                facing_min=facing_min)
        return float(areas[m].sum())

    base = covered_area(0.0)                       # everything a point tool can touch
    if base <= total * 1e-4:
        return {"max_tool_diameter": 0.0, "limited": True, "reachable_pct": 0.0}

    hi = float(np.sort(mesh.extents)[0])           # smallest bounding dimension
    target = base * (1.0 - tol)

    # If even the widest candidate tool keeps coverage, there is no fine-detail limit.
    if covered_area(hi / 2.0) >= target:
        return {
            "max_tool_diameter": round(hi, 2),
            "limited": False,
            "reachable_pct": round(100.0 * base / total, 1),
        }

    lo, high = 0.0, hi
    for _ in range(iterations):
        mid = 0.5 * (lo + high)
        if covered_area(mid / 2.0) >= target:
            lo = mid
        else:
            high = mid
    return {
        "max_tool_diameter": round(lo, 2),
        "limited": True,
        "reachable_pct": round(100.0 * base / total, 1),
    }
