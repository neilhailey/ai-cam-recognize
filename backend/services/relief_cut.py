"""
Relief cut preview.

What a ball-nose cutter can physically leave behind on a 2.5D relief, without
generating a toolpath.

The key fact: a ball of radius r rolling over a surface cannot enter anything
narrower or tighter than itself, and the surface it leaves is exactly the
*grayscale morphological closing* of the target height map by a spherical
structuring element (dilate, then erode). So the "simulation" is a pair of
image operations rather than a solid-modelling problem — cheap, exact for the
geometry it models, and it directly answers the question customers actually ask:
"will a 6 mm ball nose still show the eyelashes?"

Scallops between passes are separate and analytic: with stepover s and radius r
the leftover cusp is r - sqrt(r^2 - (s/2)^2).
"""
from __future__ import annotations

import math

import numpy as np
import scipy.ndimage as ndi
import trimesh


def sample_height_map(mesh: trimesh.Trimesh, grid: int = 300) -> tuple[np.ndarray, float]:
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
    cell = float(xs[1] - xs[0]) if nx > 1 else span / grid
    return z.reshape(ny, nx), cell


def ball_structure(radius: float, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """Footprint (in cells) and height profile (in MODEL units) of a ball nose.

    The profile has to be in the same units as the height map: grey_dilation adds
    these values to it. Expressing the profile in cells instead makes dilation and
    erosion cancel exactly, and the simulation silently does nothing.
    """
    rc = max(int(math.ceil(radius / cell)), 1)
    y, x = np.mgrid[-rc:rc + 1, -rc:rc + 1]
    dist2 = ((x * cell) ** 2 + (y * cell) ** 2)          # model units, squared
    foot = dist2 <= radius ** 2
    prof = np.where(foot, np.sqrt(np.maximum(radius ** 2 - dist2, 0.0)), 0.0)
    return foot, prof


def simulate_cut(mesh: trimesh.Trimesh, tool_diameter: float,
                 grid: int = 300, stepover_frac: float = 0.35) -> dict:
    """Compare the target relief with what a given ball nose can actually cut."""
    target, cell = sample_height_map(mesh, grid)
    if cell <= 0 or tool_diameter <= 0:
        return {"ok": False}

    radius = tool_diameter / 2.0
    if radius / cell < 0.75:      # tool finer than the sampling: nothing is lost
        return {"ok": True, "tool_diameter": tool_diameter, "lost_pct": 0.0,
                "max_loss": 0.0, "mean_loss": 0.0, "scallop": 0.0,
                "grid": [int(target.shape[1]), int(target.shape[0])]}

    foot, prof = ball_structure(radius, cell)
    # closing = the lower envelope of every legal tool position
    cut = ndi.grey_erosion(
        ndi.grey_dilation(target, footprint=foot, structure=prof),
        footprint=foot, structure=prof)

    loss = cut - target                      # >= 0: material the tool leaves behind
    depth = float(target.max() - target.min()) or 1.0
    tol = 0.02 * depth                       # 2% of relief depth counts as "lost"
    stepover = stepover_frac * tool_diameter
    scallop = radius - math.sqrt(max(radius ** 2 - (stepover / 2.0) ** 2, 0.0))

    return {
        "ok": True,
        "tool_diameter": tool_diameter,
        "lost_pct": round(100.0 * float((loss > tol).mean()), 1),
        "max_loss": round(float(loss.max()), 4),
        "mean_loss": round(float(loss.mean()), 4),
        "relief_depth": round(depth, 4),
        "scallop": round(scallop, 4),
        "stepover": round(stepover, 4),
        "grid": [int(target.shape[1]), int(target.shape[0])],
    }
