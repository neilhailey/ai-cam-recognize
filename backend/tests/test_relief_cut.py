"""Tests for the relief height sampling used by the machining simulation."""
import os
import sys

import numpy as np
import trimesh

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services import relief_cut as rc  # noqa: E402


def _plate_with_groove(groove_width=3.0):
    """A flat plate with a narrow slot cut into its top face."""
    body = trimesh.creation.box(extents=(100, 100, 20))
    slot = trimesh.creation.box(extents=(groove_width, 80, 10))
    slot.apply_translation([0, 0, 12])
    m = body.difference(slot)
    m.merge_vertices()
    m.fix_normals()
    m.apply_translation([0, 0, -float(m.bounds[0][2])])
    return m


def test_height_map_matches_the_part():
    m = _plate_with_groove()
    z, cell, hit = rc.sample_height_map(m, grid=120)
    assert cell > 0
    assert np.isfinite(z).all()
    assert hit.any(), "no rays hit the part"
    # plate top sits at 20; the slot cuts a groove into it
    assert abs(float(z.max()) - 20.0) < 1.0, z.max()
    assert float(z.max()) - float(z.min()) > 2.0, (z.min(), z.max())


def test_payload_round_trips_the_surface():
    """The browser rebuilds heights from uint16 — that must stay faithful."""
    m = _plate_with_groove()
    p = rc.heightmap_payload(m, grid=120)
    assert p["w"] > 0 and p["h"] > 0 and p["cell"] > 0
    assert len(p["z"]) == p["w"] * p["h"]
    assert len(p["inside"]) == p["w"] * p["h"]
    span = p["zmax"] - p["zmin"]
    rebuilt = np.array(p["z"], dtype=np.float64) / 65535.0 * span + p["zmin"]
    z, _cell, _hit = rc.sample_height_map(m, grid=120)
    # quantisation error must be far below the mesh's own resolution
    assert float(np.abs(rebuilt.reshape(z.shape) - z).max()) < span / 1000.0
