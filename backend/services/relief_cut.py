"""
Relief height sampling for the machining simulation.

Samples the relief to a height grid and ships it to the browser, which runs the
cutting simulation itself — a ball at (x, y) can descend only to the highest
point of the surface under its footprint, which is cheap to evaluate per tool
position, so material removal animates without a round trip.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh


def sample_height_map(mesh: trimesh.Trimesh, grid: int = 300
                      ) -> tuple[np.ndarray, float, np.ndarray]:
    """Height of the top surface on a regular grid, plus the cell size.

    One downward ray per cell — the same probe the relief detector uses. Cells
    that miss the part get the floor height.
    """
    from .machinability import _make_intersector

    intersector, _ = _make_intersector(mesh)
    lo, hi = mesh.bounds
    span = max(float(hi[0] - lo[0]), float(hi[1] - lo[1]))
    nx = max(int(grid * (hi[0] - lo[0]) / span), 8)
    ny = max(int(grid * (hi[1] - lo[1]) / span), 8)
    xs = np.linspace(lo[0], hi[0], nx)
    ys = np.linspace(lo[1], hi[1], ny)
    X, Y = np.meshgrid(xs, ys)
    n = X.size
    origins = np.column_stack([X.ravel(), Y.ravel(), np.full(n, hi[2] + span)])
    dirs = np.tile([0.0, 0.0, -1.0], (n, 1))

    locs, ray_idx, _tri = intersector.intersects_location(
        origins, dirs, multiple_hits=False)
    z = np.full(n, float(lo[2]))
    if len(ray_idx):
        z[np.asarray(ray_idx, dtype=np.int64)] = locs[:, 2]
    hit = np.zeros(n, dtype=bool)
    if len(ray_idx):
        hit[np.asarray(ray_idx, dtype=np.int64)] = True
    cell = float(xs[1] - xs[0]) if nx > 1 else span / grid
    return z.reshape(ny, nx), cell, hit.reshape(ny, nx)


def heightmap_payload(mesh: trimesh.Trimesh, grid: int = 190) -> dict:
    """The relief as a height grid, for the browser to machine live.

    Everything the simulator needs travels in this one payload: the surface to
    cut, and the physical size of a cell so the tool can be scaled correctly.
    The browser does the cutting itself — a ball at (x, y) can descend only to
    the highest point of the surface under its footprint, which is cheap to
    evaluate per position, so material removal animates without a round trip.
    """
    z, cell, hit = sample_height_map(mesh, grid)
    lo, hi = mesh.bounds
    zmin, zmax = float(z.min()), float(z.max())
    # Ship as integers: 16-bit over the actual z range is well under the
    # resolution of the mesh itself, and keeps the payload a few hundred KB.
    span = max(zmax - zmin, 1e-9)
    q = np.clip(((z - zmin) / span * 65535.0).round(), 0, 65535).astype(np.uint16)
    return {
        "w": int(z.shape[1]),
        "h": int(z.shape[0]),
        "cell": round(float(cell), 8),
        "x0": round(float(lo[0]), 6),
        "y0": round(float(lo[1]), 6),
        "zmin": round(zmin, 6),
        "zmax": round(zmax, 6),
        "inside": [int(v) for v in hit.ravel().astype(np.uint8)],
        "z": [int(v) for v in q.ravel()],
    }
