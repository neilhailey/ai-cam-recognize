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
CLASS_FIXTURE = 4    # bed-contact / mounting face, excluded from the verdict
CLASS_CHUCK = 5      # gripped by the rotary chuck (display only, see colorize)

CLASS_COLORS = {
    CLASS_3AXIS:    [ 76, 175,  80, 255],   # green
    CLASS_4AXIS:    [255, 193,   7, 255],   # amber
    CLASS_5AXIS:    [244,  67,  54, 255],   # red
    CLASS_ENCLOSED: [ 33,  33,  33, 255],   # near-black
    CLASS_FIXTURE:  [150, 150, 150, 255],   # grey
    CLASS_CHUCK:    [ 88, 166, 255, 255],   # blue — held in the chuck
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
    face_area: float                   # machinable surface area (excl. fixture + voids)
    n_faces: int
    ray_backend: str                   # "embree" or "trimesh"
    rotary_length: float = 0.0         # part length along the rotary axis
    is_relief: bool = False            # 2.5D plate carved from the top
    sides: int = 1                     # 3-axis setups needed (1, or 2 if flipped)
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
            "rotary_length": round(self.rotary_length, 2),
            "is_relief": self.is_relief,
            "sides": self.sides,
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
    """Directions perpendicular to a rotary ``axis``, one per step."""
    angles = np.deg2rad(np.arange(0.0, 360.0, step_deg))
    c, s = np.cos(angles), np.sin(angles)
    z = np.zeros_like(angles)
    if axis == "x":            # rotate in the Y-Z plane
        dirs = np.column_stack([z, c, s])
    elif axis == "y":          # rotate in the X-Z plane
        dirs = np.column_stack([c, z, s])
    elif axis == "z":          # rotate in the X-Y plane
        dirs = np.column_stack([c, s, z])
    else:
        raise ValueError(f"rotary axis must be 'x', 'y' or 'z', got {axis!r}")
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
# Mounting / workholding
# ---------------------------------------------------------------------------
def detect_mounting_face(mesh: trimesh.Trimesh, min_area_frac: float = 0.005) -> dict:
    """Find the surface the part would most naturally be mounted on.

    Takes the largest set of *coplanar* faces: quantize each face's plane
    (normal direction + offset along it) and sum area per plane. A printed or
    carved part almost always has a flat base, which wins easily. Purely
    organic models have no such plane, so we fall back to the largest facet of
    the convex hull — the flattest place the part can rest.

    Returns ``{normal, point, area_pct, source}``.
    """
    normals = np.nan_to_num(np.asarray(mesh.face_normals, dtype=np.float64),
                            posinf=0.0, neginf=0.0)
    centroids = np.asarray(mesh.triangles_center, dtype=np.float64)
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    total = float(areas.sum()) or 1.0
    diag = float(np.linalg.norm(mesh.extents)) or 1.0

    # Quantize the plane each face lies in: direction to ~5 deg, offset to ~1/200 diag.
    ndir = np.round(normals * 12.0).astype(np.int64)
    offs = np.round(np.einsum("ij,ij->i", normals, centroids) / (diag / 200.0)).astype(np.int64)
    keys = np.column_stack([ndir, offs[:, None]])
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    plane_area = np.bincount(inv, weights=areas, minlength=len(uniq))
    best = int(np.argmax(plane_area))

    if plane_area[best] >= min_area_frac * total:
        sel = np.nonzero(inv == best)[0]
        w = areas[sel]
        normal = (normals[sel] * w[:, None]).sum(0)
        normal /= (np.linalg.norm(normal) or 1.0)
        point = (centroids[sel] * w[:, None]).sum(0) / (w.sum() or 1.0)
        return {"normal": normal, "point": point,
                "area_pct": 100.0 * float(plane_area[best]) / total,
                "source": "flat-face"}

    # Fallback: the biggest face of the convex hull is the flattest resting spot.
    try:
        hull = mesh.convex_hull
        i = int(np.argmax(hull.area_faces))
        return {"normal": np.asarray(hull.face_normals[i], dtype=np.float64),
                "point": np.asarray(hull.triangles_center[i], dtype=np.float64),
                "area_pct": 100.0 * float(hull.area_faces[i]) / total,
                "source": "hull-fallback"}
    except Exception:
        return {"normal": np.array([0.0, 0.0, -1.0]),
                "point": np.asarray(mesh.centroid, dtype=np.float64),
                "area_pct": 0.0, "source": "default-down"}


def orient_for_machining(mesh: trimesh.Trimesh, flip: bool = False) -> tuple[trimesh.Trimesh, dict]:
    """Stand the part on its mounting face, so +Z means 'straight down onto it'.

    Rotates so the detected mounting face points -Z and drops the part to z=0.
    ``flip`` turns it 180 deg about X first, which is how the UI offers the
    opposite setup.
    """
    out = mesh.copy()
    face = detect_mounting_face(out)

    # Put the mounting face down first...
    out.apply_transform(
        trimesh.geometry.align_vectors(face["normal"], np.array([0.0, 0.0, -1.0])))
    # ...then, if asked, turn the part over so it rests on the opposite side.
    # (Order matters: flipping first would just be undone by the alignment.)
    if flip:
        out.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
    out.apply_translation([0.0, 0.0, -float(out.bounds[0][2])])   # sit on z=0

    # A relief has a flat back and a carved front, and both are "large flat faces",
    # so the plane search can land on the carved side and mount the part face-down.
    # The flat side is the one whose area sits almost entirely in one plane, so
    # compare the two and turn the part over if the smoother face ended up on top.
    nz = np.nan_to_num(np.asarray(out.face_normals, dtype=np.float64))[:, 2]
    zc = np.asarray(out.triangles_center, dtype=np.float64)[:, 2]
    a = np.asarray(out.area_faces, dtype=np.float64)
    height = max(float(out.extents[2]), 1e-9)
    flat_down = float(a[(nz < -0.9) & (zc < 0.05 * height)].sum())
    flat_up = float(a[(nz > 0.9) & (zc > 0.95 * height)].sum())
    if flat_up > flat_down * 1.15:
        out.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
        out.apply_translation([0.0, 0.0, -float(out.bounds[0][2])])

    # By construction the part now rests on the z=0 plane, so that is the mounting
    # plane; measure how much surface actually lies in it.
    centroids = np.asarray(out.triangles_center, dtype=np.float64)
    normals = np.nan_to_num(np.asarray(out.face_normals, dtype=np.float64),
                            posinf=0.0, neginf=0.0)
    areas = np.asarray(out.area_faces, dtype=np.float64)
    span = max(float(out.extents[2]), 1e-9)
    on_bed = (centroids[:, 2] < 0.02 * span) & (normals[:, 2] < -0.9)
    seated = 100.0 * float(areas[on_bed].sum()) / (float(areas.sum()) or 1.0)

    info = {
        "normal": [0.0, 0.0, -1.0],
        "point": [round(float(x), 3) for x in
                  (centroids[on_bed].mean(0) if on_bed.any() else out.centroid)],
        "area_pct": round(seated, 1),
        "source": face["source"] if not flip else "flipped-side",
        "flipped": bool(flip),
        "z": 0.0,
    }
    return out, info


def interior_void_faces(mesh: trimesh.Trimesh) -> np.ndarray:
    """Boolean mask of faces belonging to a shell sealed inside the outer surface.

    Hollow print-style models carry a second, inner shell. Cut from solid stock
    that interior simply does not exist, so it must not count against the
    verdict. Detected structurally — a separate connected surface component whose
    bounds sit inside the outermost one — rather than by reachability, so that a
    deep pocket or a region a fat tool cannot enter (both still part of the outer
    surface) keeps counting against the verdict, as it should.
    """
    n = len(mesh.faces)
    empty = np.zeros(n, dtype=bool)
    try:
        groups = trimesh.graph.connected_components(
            mesh.face_adjacency, nodes=np.arange(n), min_len=1)
    except Exception:
        return empty
    if len(groups) < 2:
        return empty

    tris = np.asarray(mesh.triangles)
    boxes = [(tris[g].reshape(-1, 3).min(0), tris[g].reshape(-1, 3).max(0)) for g in groups]
    outer = int(np.argmax([float(np.linalg.norm(hi - lo)) for lo, hi in boxes]))
    olo, ohi = boxes[outer]

    mask = empty.copy()
    for i, g in enumerate(groups):
        if i == outer:
            continue
        lo, hi = boxes[i]
        if bool((lo >= olo - 1e-9).all() and (hi <= ohi + 1e-9).all()):
            mask[np.asarray(g, dtype=np.int64)] = True
    return mask


def mesh_quality(mesh: trimesh.Trimesh) -> dict:
    """Flag mesh defects that make any verdict unreliable.

    Most real STLs are not clean solids. A mesh whose winding is inconsistent has
    face normals that disagree about which way is "out", and one that is not
    watertight has holes — both can silently skew an accessibility result, so the
    user should be told rather than handed a confident wrong number.
    """
    warnings: list = []
    try:
        watertight = bool(mesh.is_watertight)
        winding = bool(mesh.is_winding_consistent)
    except Exception:
        return {"watertight": None, "winding_consistent": None, "warnings": []}

    if not winding:
        warnings.append("This mesh has inconsistent face winding — its normals do not "
                        "agree on which side is outside. Results may be unreliable; "
                        "repairing the mesh in your CAD/CAM tool is recommended.")
    if not watertight:
        warnings.append("This mesh is not watertight (it has holes or is an open "
                        "surface). It was analysed as-is, but a sealed solid gives a "
                        "more trustworthy verdict.")
    return {"watertight": watertight, "winding_consistent": winding, "warnings": warnings}


def relief_probe(mesh: trimesh.Trimesh, grid: int = 56, intersector=None) -> dict:
    """Is this a 2.5D relief — a plate carved only from the top?

    Fires a grid of rays straight down and counts how many times each column
    crosses the surface. A plate whose top is a height map gives exactly two
    crossings (top, then bottom); a third crossing means material genuinely
    overhangs something.

    This deliberately ignores face normals and fine geometry, so it survives the
    two things that break the per-face test on real STLs: decimation stair-steps,
    and meshes whose winding is inconsistent (open or badly exported surfaces).
    """
    result = {"is_relief": False, "clean_fraction": 0.0, "flatness": 0.0}
    try:
        ext = np.asarray(mesh.extents, dtype=np.float64)
        if ext[2] <= 0 or max(ext[0], ext[1]) <= 0:
            return result
        # A relief is wide and shallow. Anything chunky is not a plate.
        flatness = float(ext[2] / max(ext[0], ext[1]))
        result["flatness"] = round(flatness, 3)
        if flatness > 0.45:
            return result

        if intersector is None:
            intersector, _ = _make_intersector(mesh)
        lo, hi = mesh.bounds
        xs = np.linspace(lo[0], hi[0], grid + 2)[1:-1]
        ys = np.linspace(lo[1], hi[1], grid + 2)[1:-1]
        X, Y = np.meshgrid(xs, ys)
        n = X.size
        origins = np.column_stack([X.ravel(), Y.ravel(),
                                   np.full(n, hi[2] + float(ext.max()))])
        dirs = np.tile([0.0, 0.0, -1.0], (n, 1))
        _tri, ray_idx = intersector.intersects_id(origins, dirs, multiple_hits=True)
        counts = np.bincount(np.asarray(ray_idx, dtype=np.int64), minlength=n)
        hit = counts[counts > 0]
        if not len(hit):
            return result
        clean = float((hit <= 2).mean())
        result["clean_fraction"] = round(clean, 3)
        result["is_relief"] = clean >= 0.85
        return result
    except Exception:
        return result


def excluded_faces(mesh: trimesh.Trimesh, vertical_deg: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """``(fixture, voids)`` — the faces that are never machined.

    ``fixture`` is the mounting face resting on the bed (clamped, so no tool
    reaches it and none needs to). ``voids`` are hollow-model interiors, which do
    not exist in solid stock. Shared by the verdict and the setup planner so both
    answer the same question about the same surface.
    """
    normals = np.nan_to_num(np.asarray(mesh.face_normals, dtype=np.float64),
                            posinf=0.0, neginf=0.0)
    z = np.asarray(mesh.triangles_center, dtype=np.float64)[:, 2]
    z_min = float(z.min())
    z_span = max(float(z.max()) - z_min, 1e-9)
    down = normals[:, 2] < -math.cos(math.radians(vertical_deg))
    fixture = down & (z < (z_min + 0.02 * z_span))
    return fixture, interior_void_faces(mesh) & (~fixture)


def rotary_axis_for(mesh: trimesh.Trimesh) -> tuple[str, float]:
    """Rotary axis for 4-axis work: the part's longest dimension.

    You mount a part in a 4th axis along its length. Reachability about an axis
    depends only on that axis's direction relative to the geometry, not on how
    the part happens to be sitting, so we are free to pick the longest axis even
    if it is currently vertical — the part simply gets laid down to be held
    (see ``lay_down_along``).
    """
    ext = np.asarray(mesh.extents, dtype=np.float64)
    k = int(np.argmax(ext))
    return "xyz"[k], float(ext[k])


def lay_down_along(mesh: trimesh.Trimesh, axis: str,
                   face_class: Optional[np.ndarray] = None) -> trimesh.Trimesh:
    """Rotate the part so ``axis`` runs along X, i.e. how it sits in the chuck.

    A rigid rotation, so per-face results computed beforehand stay valid — the
    faces keep their indices, only their positions change.
    """
    src = {"x": [1.0, 0, 0], "y": [0, 1.0, 0], "z": [0, 0, 1.0]}[axis]
    out = mesh.copy()
    if axis != "x":
        out.apply_transform(trimesh.geometry.align_vectors(
            np.array(src), np.array([1.0, 0.0, 0.0])))
    out.apply_translation([0.0, 0.0, -float(out.bounds[0][2])])
    if face_class is not None:
        out.metadata["face_class"] = face_class
    return out


def chuck_grip_mask(mesh: trimesh.Trimesh, axis: str, grip_frac: float = 0.12) -> np.ndarray:
    """Faces at the end of the part that the rotary chuck would clamp onto.

    The chuck grips one end along the rotary axis; we mark the outer
    ``grip_frac`` of the part's length there so it can be shown in the viewer.
    """
    k = 0 if axis == "x" else 1
    c = np.asarray(mesh.triangles_center, dtype=np.float64)[:, k]
    lo, hi = float(c.min()), float(c.max())
    return c <= lo + grip_frac * max(hi - lo, 1e-9)


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
    """Low-memory reduction of a big binary STL via vertex clustering.

    Parses the file directly with numpy (float32) and snaps vertices to a grid,
    keeping peak memory a few hundred MB — so a 512 MB host can handle
    multi-million-triangle meshes that trimesh's normal loader would OOM on.
    The grid math is chunked because a whole-array expression would allocate
    large float64 temporaries. Each surviving vertex is the *average* of the
    vertices that fell in its cell, which tracks the original surface closely
    enough to preserve the axis verdict. Cell size is per-axis so shallow reliefs
    keep their depth detail.
    """
    with open(path, "rb") as fh:
        n = int(np.frombuffer(fh.read(84)[80:84], dtype="<u4")[0])

        def _chunks():
            """Yield (c*3, 3) float32 vertex blocks, re-reading from the start."""
            fh.seek(84)
            left = n
            while left:
                c = min(chunk, left)
                raw = np.frombuffer(fh.read(c * 50), dtype=np.uint8).reshape(c, 50)
                yield np.frombuffer(raw[:, 12:48].tobytes(), dtype="<f4").reshape(c * 3, 3)
                left -= c

        # Pass 1: bounding box, so we never hold every vertex at once.
        mn = np.full(3, np.inf, dtype=np.float32)
        mx = np.full(3, -np.inf, dtype=np.float32)
        for v in _chunks():
            mn = np.minimum(mn, v.min(axis=0))
            mx = np.maximum(mx, v.max(axis=0))
        # Per-axis cell size: give every axis the SAME number of cells rather than
        # one cell size everywhere. A relief is wide but shallow (e.g. 0.10 x 0.07
        # x 0.02), so a single cell size leaves the depth with almost no
        # resolution — the surface gets quantised into stair-steps that read as
        # overhangs and turn a 3-axis carving into a false 4/5-axis verdict.
        ext = (mx - mn).astype(np.float64)
        diag = float(np.linalg.norm(ext)) or 1.0
        cells = max(4.0, (target_faces ** 0.5) * 0.55)
        res = np.maximum(ext / cells, diag * 1e-6).astype(np.float32)   # (3,)
        span = (np.floor(ext / res).astype(np.int64) + 2)

        # Pass 2: grid-cell key per vertex. int32 when the grid fits, halving the
        # key array and np.unique's working set.
        key_dtype = np.int32 if int(span.prod()) < 2 ** 31 else np.int64
        keys = np.empty(n * 3, dtype=key_dtype)
        off = 0
        for v in _chunks():
            g = np.floor((v - mn) / res).astype(np.int64)
            k = (g[:, 0] * span[1] + g[:, 1]) * span[2] + g[:, 2]
            keys[off:off + len(v)] = k
            off += len(v)

        # unique() without return_inverse (that array alone would be n*3 int64);
        # map back with a chunked searchsorted instead.
        uniq = np.unique(keys)
        nv = len(uniq)

        # Pass 3: assign vertices to cells and accumulate per-cell sums. Using the
        # *average* of the vertices in a cell (not the cell centre) matters a lot:
        # snapping to centres adds stair-step noise that reads as fake undercuts
        # and can flip a 4-axis part to 5-axis. The accumulators are tiny (one row
        # per surviving vertex), so this costs almost nothing.
        sums = np.zeros((nv, 3), dtype=np.float64)
        counts = np.zeros(nv, dtype=np.float64)
        faces = np.empty((n, 3), dtype=np.int32)
        flat = faces.reshape(-1)
        pos = 0
        for v in _chunks():
            idx = np.searchsorted(uniq, keys[pos:pos + len(v)])
            for i in range(3):
                sums[:, i] += np.bincount(idx, weights=v[:, i], minlength=nv)
            counts += np.bincount(idx, minlength=nv)
            flat[pos:pos + len(v)] = idx
            pos += len(v)
        del keys

    rep = sums / np.maximum(counts, 1.0)[:, None]

    keep = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    mesh = trimesh.Trimesh(vertices=rep, faces=faces[keep], process=False)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    mesh.fix_normals()
    mesh.metadata["approximate"] = True    # clustered: verdict uses a looser tolerance
    return mesh


def load_mesh(path: str, max_faces: int = 30_000) -> trimesh.Trimesh:
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
    area_tol: float = 0.02,           # 2% of area may be unreachable and still "pass"
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

    # --- faces that are never machined: clamped mounting face, hollow interiors
    fixture, voids = excluded_faces(mesh, vertical_deg)
    face_class[fixture] = CLASS_FIXTURE

    considered = np.nonzero(~fixture & ~voids)[0]
    considered_area = float(areas[considered].sum()) or 1.0
    tol_area = area_tol * considered_area

    def area_of(mask_idx) -> float:
        return float(areas[mask_idx].sum())

    # --- 3-axis: cut from +Z, and only flip the stock if that leaves work -----
    # Testing +Z on its own first is what tells us whether a second setup is
    # genuinely needed: a part like a gear, clamped on its base, is finished in
    # one setup even though a flip is available.
    # A 2.5D relief is a plate carved from above: by definition nothing overhangs,
    # so the whole top surface is reachable in one pass. Decided by ray-column
    # parity rather than per-face rays, because decimation stair-steps and bad
    # winding make the per-face test fail on exactly these parts.
    relief = relief_probe(mesh, intersector=intersector)
    if relief["is_relief"]:
        ok3 = np.ones(len(considered), dtype=bool)
    else:
        ok3 = _reachable_from_any(intersector, centroids, normals, considered,
                                  np.array([[0, 0, 1.0]]), diagonal, eps_face,
                                  offset, tool_radius)
    sides = 1
    if allow_flip and not relief["is_relief"] and (considered_area - area_of(considered[ok3])) > tol_area:
        left = np.nonzero(~ok3)[0]
        okb = _reachable_from_any(intersector, centroids, normals, considered[left],
                                  np.array([[0, 0, -1.0]]), diagonal, eps_face,
                                  offset, tool_radius)
        if okb.any():
            ok3[left[okb]] = True
            sides = 2

    three_idx = considered[ok3]
    face_class[three_idx] = CLASS_3AXIS

    remaining = considered[~ok3]

    # --- 4-axis: radial directions about the rotary axis -----------------------
    # The 4th axis is horizontal, so the part spins about whichever of X/Y it is
    # longest along — that is how it would actually be held in the chuck.
    best_axis, rotary_length = rotary_axis_for(mesh)
    best_ok4 = None
    if len(remaining):
        ext = np.asarray(mesh.extents, dtype=np.float64)
        # Mount along the longest axis. When two spans are within a few percent
        # there is no meaningful "longest", so among those near-equal candidates
        # take whichever actually reaches more — e.g. a cross-drilled cylinder is
        # square in plan, and only one radial plane can see down the bore.
        longest = float(ext.max())
        candidates = [ax for ax, e in zip("xyz", ext)
                      if e >= longest - 0.05 * longest]

        best_gained = -1.0
        for axis in candidates:
            dirs = _radial_directions(axis, radial_step_deg)
            ok4 = _reachable_from_any(intersector, centroids, normals, remaining,
                                      dirs, diagonal, eps_face, offset, tool_radius)
            gained = area_of(remaining[ok4])
            if gained > best_gained:
                best_gained, best_axis, best_ok4 = gained, axis, ok4
        rotary_length = float(ext["xyz".index(best_axis)])

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

    # The denominator is the *outer* surface (fixture faces and hollow-model
    # interiors already removed). Anything unreachable that is still part of the
    # outer surface — a deep pocket, or a slot too narrow for the tool — stays in
    # and correctly counts against the verdict.
    void_area = area_of(np.nonzero(voids)[0])
    machinable_area = considered_area

    pct3 = 100.0 * a3 / machinable_area
    pct4 = 100.0 * (a3 + a4) / machinable_area     # cumulative: a 4-axis machine does 3-axis work
    pct5 = 100.0 * (a3 + a4 + a5) / machinable_area
    enclosed_pct = 100.0 * void_area / (machinable_area + void_area)

    if (machinable_area - a3) <= tol_area:
        verdict = VERDICT_3AXIS
    elif (machinable_area - a3 - a4) <= tol_area:
        verdict = VERDICT_4AXIS
        # Held between chuck and tailstock, the part spins to present every side,
        # so it is a SINGLE setup — only the tabs at each end are left to trim.
        sides = 1
    else:
        verdict = VERDICT_5AXIS

    # vertical-wall flag (long-tool hint) — near-vertical, non-fixture faces
    vertical = np.abs(normals[:, 2]) < math.sin(math.radians(vertical_deg))
    vertical_area = area_of(np.nonzero(vertical & (~fixture))[0])
    vertical_pct = 100.0 * vertical_area / machinable_area

    label = {
        VERDICT_3AXIS: ("3-axis relief (single setup)" if relief["is_relief"]
                        else "3-axis" + (" (2-sided)" if sides == 2 else " (single setup)")),
        # The part gets mounted along its longest dimension, so name it that way
        # rather than by a model-space axis letter, which flips as soon as the
        # part is laid into the chuck.
        VERDICT_4AXIS: "4-axis (single rotary setup)",
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
    if relief["is_relief"]:
        caveats.append("Detected as a 2.5D relief: the surface is a height map with "
                       "nothing overhanging, so it is carved from the top in one "
                       "setup. The blank's underside is clamped and never cut.")
    elif verdict == VERDICT_4AXIS:
        caveats.append("One rotary setup: held between chuck and tailstock, the part "
                       "turns to present every side. Only the tabs at each end are "
                       "left, to be trimmed off afterwards.")
    elif sides == 2:
        caveats.append("Needs the stock flipped once: some faces are only reachable "
                       "from below. The mounting face itself is clamped and excluded.")
    else:
        caveats.append("Everything reachable is cut in a single setup — no flip needed. "
                       "The mounting face is clamped and excluded.")
    if enclosed_pct > 0.5:
        caveats.append(f"This model is hollow — ~{enclosed_pct:.0f}% of its surface is "
                       "an internal void. Cut from solid stock that interior does not "
                       "exist, so it is excluded from the verdict rather than counted "
                       "as unreachable.")
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
        face_area=machinable_area,
        n_faces=n,
        ray_backend=backend,
        rotary_length=rotary_length,
        is_relief=bool(relief["is_relief"]),
        sides=sides,
        caveats=caveats,
    )
    return report, face_class


# ---------------------------------------------------------------------------
# Colorized export
# ---------------------------------------------------------------------------
def colorize(mesh: trimesh.Trimesh, face_class: np.ndarray,
             chuck_axis: Optional[str] = None) -> trimesh.Trimesh:
    """Return a copy of ``mesh`` with per-face colors set from CLASS_COLORS.

    When ``chuck_axis`` is given (4-axis work), the end of the part the rotary
    chuck clamps is recoloured so it is visible in the viewer. This is display
    only — it happens after classification, so the reported percentages are
    unaffected.
    """
    shown = np.asarray(face_class).copy()
    if chuck_axis:
        shown[chuck_grip_mask(mesh, chuck_axis)] = CLASS_CHUCK
    colored = mesh.copy()
    # Give every face its own vertices. glTF stores colour per *vertex*, so a
    # vertex shared by two classes would blend them and smear the boundaries
    # into a gradient instead of showing crisp reachable/undercut regions.
    colored.unmerge_vertices()
    colored.visual.face_colors = np.array(
        [CLASS_COLORS[int(c)] for c in shown], dtype=np.uint8)
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

    # Only advise a re-orientation that is actually worth doing:
    #  - an oblique mounting is impractical to clamp, whatever it scores;
    #  - dropping 4-axis (one rotary setup) to 3-axis but needing a flip is not a
    #    win — it trades one setup for two.
    if _name_dir(best_up) == "oblique":
        improved = False
    if (improved and current.verdict == VERDICT_4AXIS
            and best_rep.verdict == VERDICT_3AXIS and best_rep.sides == 2):
        improved = False

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

    # A relief is carved from the top in one pass — say so, rather than letting the
    # greedy search invent flips out of decimation noise.
    if relief_probe(mesh, intersector=intersector)["is_relief"]:
        return {"n_setups": 1,
                "setups": [{"direction": "+Z", "vector": [0.0, 0.0, 1.0],
                            "cumulative_coverage_pct": 100.0}],
                "uncoverable_pct": 0.0, "fully_covered": True}

    # Plan over the same surface the verdict judges: the clamped mounting face and
    # hollow interiors are never cut, so counting them would invent extra setups.
    fixture, voids = excluded_faces(mesh)
    all_idx = np.nonzero(~fixture & ~voids)[0]
    total = float(areas[all_idx].sum()) or 1.0

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
            gain = float(areas[all_idx[m & ~covered]].sum())
            if gain > best_gain:
                best_gain, best_i = gain, i
        if best_i < 0 or best_gain <= total * 1e-4:
            break
        covered |= masks[best_i]
        chosen.append((best_i, deduped[best_i]))
        cov = float(areas[all_idx[covered]].sum()) / total
        steps.append({"direction": _name_dir(deduped[best_i]),
                      "vector": [round(float(x), 3) for x in deduped[best_i]],
                      "cumulative_coverage_pct": round(100.0 * cov, 1)})
        if cov >= coverage_target:
            break

    uncovered_pct = round(100.0 * float(areas[all_idx[~covered]].sum()) / total, 1)
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
