"""
Known-answer tests for the machinability engine, on procedurally-built meshes.
No external files needed.

Run: cd AI_CAM_Recognize/backend && .venv/bin/python -m pytest tests/ -q
"""
import os
import sys

import numpy as np
import trimesh

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services import machinability as mac  # noqa: E402


def _clean(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.fix_normals()
    return mesh


# --- fixtures / geometry ----------------------------------------------------
def box():
    return _clean(trimesh.creation.box(extents=(40, 40, 20)))


def sphere():
    return _clean(trimesh.creation.icosphere(subdivisions=3, radius=20))


def cross_drilled_cylinder():
    """Upright cylinder (axis Z) with a horizontal through-hole along X."""
    body = trimesh.creation.cylinder(radius=15, height=50, sections=64)
    hole = trimesh.creation.cylinder(radius=5, height=60, sections=48)
    # rotate hole so its axis lies along X
    hole.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    return _clean(body.difference(hole))


def tilted_blind_pocket():
    """Box with a blind hole tilted about a DIAGONAL axis -> true 5-axis undercut.

    The pocket axis has both X and Y components, so its far wall is reachable
    only along an oblique direction (d_x != 0 AND d_y != 0) that lies in neither
    rotary plane -> a genuine 5-axis feature (not 3- or 4-axis reachable).
    """
    body = trimesh.creation.box(extents=(50, 50, 50))
    pocket = trimesh.creation.cylinder(radius=7, height=55, sections=48)
    # tilt ~45 deg about the diagonal [1, 1, 0] axis, then enter through the top
    pocket.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 4, [1, 1, 0]))
    pocket.apply_translation([7, -7, 20])
    return _clean(body.difference(pocket))


def hollow_sphere():
    """Spherical shell with a fully enclosed internal cavity."""
    outer = trimesh.creation.icosphere(subdivisions=3, radius=20)
    inner = trimesh.creation.icosphere(subdivisions=3, radius=14)
    return _clean(outer.difference(inner))


# --- tests ------------------------------------------------------------------
def test_box_is_3axis():
    report, _ = mac.analyze(box())
    assert report.verdict == mac.VERDICT_3AXIS, report.to_dict()


def test_sphere_is_3axis():
    report, _ = mac.analyze(sphere())
    assert report.verdict == mac.VERDICT_3AXIS, report.to_dict()


def test_cross_drilled_cylinder_needs_4axis():
    report, _ = mac.analyze(cross_drilled_cylinder())
    assert report.verdict == mac.VERDICT_4AXIS, report.to_dict()
    # the top / outer wall is 3-axis; only the through-hole pushes it to 4-axis
    assert report.machinable_pct["3axis"] < 99.5
    assert report.machinable_pct["4axis"] >= 99.5


def test_tilted_pocket_needs_5axis():
    report, _ = mac.analyze(tilted_blind_pocket())
    assert report.verdict == mac.VERDICT_5AXIS, report.to_dict()
    # a genuine undercut: not fully reachable even with a rotary axis
    assert report.machinable_pct["4axis"] < 99.5


def test_hollow_sphere_flags_enclosed_cavity():
    report, _ = mac.analyze(hollow_sphere())
    assert report.enclosed_pct > 5.0, report.to_dict()


def test_machinable_pct_is_monotonic():
    for geom in (box(), sphere(), cross_drilled_cylinder(), tilted_blind_pocket()):
        report, _ = mac.analyze(geom)
        p = report.machinable_pct
        assert p["3axis"] <= p["4axis"] + 1e-6 <= p["5axis"] + 1e-6, report.to_dict()


# --- new features -----------------------------------------------------------
def test_tool_diameter_reduces_reachability():
    """A fat tool cannot enter a narrow slot that a point tool can."""
    body = trimesh.creation.box(extents=(60, 60, 40))
    # narrow deep slot (6 mm wide) cut down into the top
    slot = trimesh.creation.box(extents=(6, 40, 30))
    slot.apply_translation([0, 0, 12])
    part = _clean(body.difference(slot))

    thin, _ = mac.analyze(part, tool_diameter=0.0)     # ideal point tool
    fat, _ = mac.analyze(part, tool_diameter=12.0)     # tool wider than the 6mm slot
    assert fat.machinable_pct["3axis"] < thin.machinable_pct["3axis"], (
        thin.to_dict(), fat.to_dict())


def test_orientation_search_flips_a_top_only_undercut():
    """A pocket only reachable from below is 5-axis as-oriented but 3-axis flipped."""
    body = trimesh.creation.box(extents=(50, 50, 50))
    # blind pocket opening downward (−Z): 3-axis reachable only after a flip
    pocket = trimesh.creation.box(extents=(20, 20, 25))
    pocket.apply_translation([0, 0, -20])
    part = _clean(body.difference(pocket))
    res = mac.find_best_orientation(part)
    # best orientation should be no worse than as-oriented
    assert mac._TIER[res["best_verdict"]] <= mac._TIER[res["current_verdict"]]
    assert "description" in res


def test_plan_setups_box_two_sided():
    """A box is fully covered by a small number of single-direction setups."""
    plan = mac.plan_setups(box())
    assert plan["fully_covered"] is True
    assert 1 <= plan["n_setups"] <= 3, plan


def test_plan_setups_flags_uncoverable_undercut():
    """The diagonally-tilted pocket leaves area no single setup can reach."""
    plan = mac.plan_setups(tilted_blind_pocket())
    assert plan["uncoverable_pct"] > 0.0, plan


def _slot_box(width):
    body = trimesh.creation.box(extents=(60, 60, 40))
    slot = trimesh.creation.box(extents=(width, 40, 30))
    slot.apply_translation([0, 0, 12])
    return _clean(body.difference(slot))


def test_max_tool_diameter_box_has_no_limit():
    """A box has no fine internal detail -> no tooling limit."""
    res = mac.max_tool_diameter(box())
    assert res["limited"] is False, res


def test_hollow_model_voids_do_not_drive_the_verdict():
    """A hollow shell is machinable from solid stock — the void must not count."""
    report, _ = mac.analyze(hollow_sphere())
    # the interior is still detected and reported...
    assert report.enclosed_pct > 5.0, report.to_dict()
    # ...but the exterior is fully reachable, so it must not read as 5-axis.
    assert report.machinable_pct["5axis"] > 99.0, report.to_dict()
    assert report.verdict != mac.VERDICT_5AXIS, report.to_dict()


def test_detect_mounting_face_finds_the_flat_base():
    face = mac.detect_mounting_face(box())          # 40 x 40 x 20
    assert face["source"] == "flat-face", face
    # each 40x40 end is 1600 of 6400 total area = 25%
    assert 20.0 < face["area_pct"] < 30.0, face


def test_detect_mounting_face_falls_back_for_organic_shapes():
    face = mac.detect_mounting_face(sphere())
    assert face["source"] in ("flat-face", "hull-fallback"), face
    assert np.isfinite(face["normal"]).all()


def test_orient_for_machining_puts_mounting_face_down():
    m = trimesh.creation.box(extents=(30, 30, 10))
    m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 3, [1, 0, 0]))
    out, info = mac.orient_for_machining(_clean(m))
    assert abs(out.bounds[0][2]) < 1e-6, info          # sits on z = 0
    assert info["normal"][2] < -0.9, info              # mounting face points down


def test_rotary_axis_is_the_longest_horizontal_span():
    long_x = _clean(trimesh.creation.box(extents=(100, 20, 20)))
    assert mac.rotary_axis_for(long_x)[0] == "x"
    long_y = _clean(trimesh.creation.box(extents=(20, 100, 20)))
    assert mac.rotary_axis_for(long_y)[0] == "y"


def test_chuck_grip_mask_marks_one_end():
    m = _clean(trimesh.creation.box(extents=(100, 20, 20)))
    mask = mac.chuck_grip_mask(m, "x", grip_frac=0.2)
    assert mask.any() and not mask.all()
    # gripped faces must sit at the low-X end
    cx = m.triangles_center[:, 0]
    assert cx[mask].max() < cx[~mask].max()


def test_max_tool_diameter_tracks_feature_width():
    """A narrower slot must be cut with a smaller tool than a wider slot."""
    narrow = mac.max_tool_diameter(_slot_box(4))
    wide = mac.max_tool_diameter(_slot_box(12))
    assert narrow["limited"] and wide["limited"], (narrow, wide)
    assert narrow["max_tool_diameter"] < wide["max_tool_diameter"], (narrow, wide)
