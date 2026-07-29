"""Tests for the relief cut preview (grayscale-morphology model)."""
import os
import sys

import numpy as np
import trimesh

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services import relief_cut as rc  # noqa: E402


def _plate_with_groove(groove_width=2.0):
    """A flat plate with a narrow slot cut into its top face."""
    body = trimesh.creation.box(extents=(100, 100, 20))
    slot = trimesh.creation.box(extents=(groove_width, 80, 10))
    slot.apply_translation([0, 0, 12])
    m = body.difference(slot)
    m.merge_vertices()
    m.fix_normals()
    m.apply_translation([0, 0, -float(m.bounds[0][2])])
    return m


def test_ball_profile_is_in_model_units():
    """The profile must match the height map's units or morphology cancels out."""
    foot, prof = rc.ball_structure(radius=5.0, cell=1.0)
    assert foot.any()
    # centre of a 5 mm ball stands 5 mm above the contact point
    assert abs(prof.max() - 5.0) < 1e-6, prof.max()


def test_bigger_tool_loses_more_detail():
    m = _plate_with_groove(groove_width=3.0)
    small = rc.simulate_cut(m, tool_diameter=1.0, grid=140)
    big = rc.simulate_cut(m, tool_diameter=12.0, grid=140)
    assert small["ok"] and big["ok"]
    assert big["lost_pct"] > small["lost_pct"], (small, big)


def test_tool_wider_than_the_groove_cannot_enter_it():
    """A 12 mm ball simply cannot reach the bottom of a 3 mm slot."""
    m = _plate_with_groove(groove_width=3.0)
    res = rc.simulate_cut(m, tool_diameter=12.0, grid=140)
    assert res["max_loss"] > 1.0, res     # leaves >1 mm of material behind


def test_scallop_grows_with_stepover():
    m = _plate_with_groove()
    fine = rc.simulate_cut(m, 6.0, grid=100, stepover_frac=0.1)
    coarse = rc.simulate_cut(m, 6.0, grid=100, stepover_frac=0.6)
    assert coarse["scallop"] > fine["scallop"], (fine, coarse)


def test_height_map_matches_the_part():
    m = _plate_with_groove()
    z, cell, hit = rc.sample_height_map(m, grid=120)
    assert cell > 0
    assert np.isfinite(z).all()
    # plate top sits at 20; the slot cuts a groove into it
    assert abs(float(z.max()) - 20.0) < 1.0, z.max()
    assert float(z.max()) - float(z.min()) > 2.0, (z.min(), z.max())
    assert hit.any(), 'no rays hit the part'
