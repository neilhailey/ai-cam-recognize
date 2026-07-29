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


def _measured_region(hit: np.ndarray, margin_cells: int = 3) -> np.ndarray:
    """Cells whose loss figure is meaningful.

    Only the part's own surface counts (rays that missed are background), and a
    small fixed margin is trimmed from its outline, where the drop to the
    background is a cliff no ball can reach into and would otherwise dominate.

    The margin is fixed rather than tool-sized on purpose: every tool has to be
    scored over the same cells, or the percentages cannot be compared.
    """
    if not hit.any():
        return np.ones_like(hit, dtype=bool)
    disc = np.ones((2 * margin_cells + 1,) * 2, dtype=bool)
    inner = ndi.binary_erosion(hit, structure=disc, border_value=0)
    return inner if inner.any() else hit


def simulate_cut(mesh: trimesh.Trimesh, tool_diameter: float,
                 grid: int = 300, stepover_frac: float = 0.35) -> dict:
    """Compare the target relief with what a given ball nose can actually cut."""
    target, cell, hit = sample_height_map(mesh, grid)
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
    region = _measured_region(hit)
    loss = np.where(region, loss, 0.0)
    depth = float(target.max() - target.min()) or 1.0
    tol = 0.02 * depth                       # 2% of relief depth counts as "lost"
    stepover = stepover_frac * tool_diameter
    scallop = radius - math.sqrt(max(radius ** 2 - (stepover / 2.0) ** 2, 0.0))

    return {
        "ok": True,
        "tool_diameter": tool_diameter,
        "lost_pct": round(100.0 * float((loss[region] > tol).mean()), 1) if region.any() else 0.0,
        # 99th percentile, not the max: one stray cell should not define the number
        "max_loss": round(float(np.percentile(loss[region], 99)), 4) if region.any() else 0.0,
        "mean_loss": round(float(loss[region].mean()), 4) if region.any() else 0.0,
        "relief_depth": round(depth, 4),
        "scallop": round(scallop, 4),
        "stepover": round(stepover, 4),
        "grid": [int(target.shape[1]), int(target.shape[0])],
    }


def cut_preview_curve(mesh: trimesh.Trimesh, grid: int = 240) -> dict:
    """Detail loss across a range of ball-nose sizes.

    Computed once, server-side, for a spread of tool sizes expressed as
    fractions of the part's smaller footprint dimension — the file has no real
    units, so the caller scales these to millimetres once the user says how big
    the part is. Returning a curve means the UI can move a slider with no
    further round-trips.
    """
    ext = np.asarray(mesh.extents, dtype=np.float64)
    base = float(min(ext[0], ext[1])) or 1.0
    fractions = [0.01, 0.02, 0.04, 0.07, 0.11, 0.16, 0.22]

    target, cell, hit = sample_height_map(mesh, grid)
    region = _measured_region(hit)
    depth = float(target.max() - target.min())
    if depth <= 0 or cell <= 0:
        return {"ok": False}

    tol = 0.02 * depth
    points = []
    for f in fractions:
        d = f * base
        radius = d / 2.0
        if radius / cell < 0.75:
            points.append({"tool": round(d, 6), "lost_pct": 0.0, "max_loss": 0.0})
            continue
        foot, prof = ball_structure(radius, cell)
        cut = ndi.grey_erosion(
            ndi.grey_dilation(target, footprint=foot, structure=prof),
            footprint=foot, structure=prof)
        loss = cut - target
        if not region.any():
            points.append({"tool": round(d, 6), "lost_pct": 0.0, "max_loss": 0.0})
            continue
        vals = loss[region]
        points.append({
            "tool": round(d, 6),
            "lost_pct": round(100.0 * float((vals > tol).mean()), 1),
            "max_loss": round(float(np.percentile(vals, 99)), 6),
        })
    return {"ok": True, "relief_depth": round(depth, 6), "points": points,
            "grid": [int(target.shape[1]), int(target.shape[0])]}
