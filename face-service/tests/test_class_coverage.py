"""Phase 0 coverage math and tiling geometry (no model, no images, no Mongo).

The class-camera gate decides whether a whole feature gets built, so the metric
behind the verdict has to be right before the first classroom frame exists.

    cd face-service
    python -m pytest tests/test_class_coverage.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.class_coverage import (           # noqa: E402
    tile_rects, iou, merge_detections, confirmed_by, coverage_report,
    frames_for_minutes, coverage_curve, coverage_by_group, face_size_summary,
    verdict, synth_observations)


def _o(frame, sid, height=80.0, sim=0.6):
    return {"frame": frame, "sid": sid, "similarity": sim, "height": height}


# --------------------------------------------------------------------------- #
# tiling geometry
# --------------------------------------------------------------------------- #
def test_tiles_cover_the_whole_frame():
    rects = tile_rects(1920, 1080, 3, 2, 0.15)
    assert len(rects) == 6
    assert min(r[0] for r in rects) == 0 and min(r[1] for r in rects) == 0
    assert max(r[2] for r in rects) == 1920 and max(r[3] for r in rects) == 1080


def test_tiles_overlap_so_seam_faces_are_not_clipped():
    plain = tile_rects(1000, 1000, 2, 1, 0.0)
    lapped = tile_rects(1000, 1000, 2, 1, 0.2)
    assert plain[0][2] == plain[1][0]          # no overlap: they merely touch
    assert lapped[0][2] > lapped[1][0]         # with overlap: they share a band


def test_single_tile_when_grid_is_one():
    assert tile_rects(640, 480, 1, 1, 0.15) == [(0, 0, 640, 480)]


def test_iou_basics():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert 0.1 < iou((0, 0, 10, 10), (5, 0, 15, 10)) < 0.5


def test_merge_drops_the_same_face_found_in_two_tiles():
    dup = [((0, 0, 10, 10), 0.9, "a"), ((1, 0, 11, 10), 0.8, "b")]
    assert len(merge_detections(dup, 0.35)) == 1
    # ...and keeps genuinely different faces
    two = [((0, 0, 10, 10), 0.9, "a"), ((50, 50, 60, 60), 0.8, "b")]
    assert len(merge_detections(two, 0.35)) == 2


def test_merge_keeps_the_higher_scoring_duplicate():
    dup = [((1, 0, 11, 10), 0.4, "low"), ((0, 0, 10, 10), 0.95, "high")]
    assert merge_detections(dup, 0.35)[0][2] == "high"


# --------------------------------------------------------------------------- #
# confirmation: N-of-M across frames
# --------------------------------------------------------------------------- #
def test_needs_enough_distinct_frames():
    obs = [_o(0, "A"), _o(1, "A"), _o(0, "B")]
    assert confirmed_by(obs, 2) == {"A"}
    assert confirmed_by(obs, 1) == {"A", "B"}
    assert confirmed_by(obs, 3) == set()


def test_repeat_hits_in_one_frame_count_once():
    # A duplicate detection of the same face must not confirm a student alone.
    obs = [_o(0, "A"), _o(0, "A"), _o(0, "A")]
    assert confirmed_by(obs, 2) == set()


def test_upto_frame_limits_the_window():
    obs = [_o(0, "A"), _o(9, "A")]
    assert confirmed_by(obs, 2, upto_frame=5) == set()
    assert confirmed_by(obs, 2, upto_frame=9) == {"A"}


# --------------------------------------------------------------------------- #
# coverage + the false marks that dominate the verdict
# --------------------------------------------------------------------------- #
def test_coverage_counts_only_students_really_present():
    obs = [_o(f, s) for f in range(3) for s in ("A", "B")]
    r = coverage_report(obs, present={"A", "B", "C"}, min_hits=2)
    assert r["covered"] == 2 and r["present"] == 3
    assert abs(r["coverage"] - 2 / 3) < 1e-9
    assert r["missed"] == ["C"] and r["false_marks"] == []


def test_confirming_an_absent_student_is_a_false_mark():
    obs = [_o(f, "GHOST") for f in range(3)]
    r = coverage_report(obs, present={"A"}, min_hits=2)
    assert r["false_marks"] == ["GHOST"] and r["coverage"] == 0.0


def test_empty_truth_does_not_divide_by_zero():
    assert coverage_report([], present=set(), min_hits=2)["coverage"] == 0.0


# --------------------------------------------------------------------------- #
# window arithmetic
# --------------------------------------------------------------------------- #
def test_frames_for_minutes():
    assert frames_for_minutes(1, 4) == 14        # 15 frames -> indices 0..14
    assert frames_for_minutes(10, 4) == 149
    assert frames_for_minutes(1, 0) == 0         # guard against a zero interval


def test_coverage_curve_is_monotonic_over_time():
    # Longer windows can only ever see more evidence, never less.
    obs, truth = synth_observations(24, frames=150, seed=1)
    curve = coverage_curve(obs, set(truth), min_hits=3, interval_s=4)
    covs = [c["coverage"] for c in curve]
    assert covs == sorted(covs)
    assert [c["minutes"] for c in curve] == [1, 3, 5, 10]


# --------------------------------------------------------------------------- #
# by-group split and face sizes
# --------------------------------------------------------------------------- #
def test_group_split_separates_placement_from_model():
    truth = {"A": "front", "B": "front", "C": "back"}
    obs = [_o(f, s) for f in range(3) for s in ("A", "B")]     # back row never seen
    by = {g["group"]: g for g in coverage_by_group(obs, truth, min_hits=2)}
    assert by["front"]["coverage"] == 1.0
    assert by["back"]["coverage"] == 0.0


def test_ungrouped_truth_still_reports():
    by = coverage_by_group([], {"A": None}, min_hits=1)
    assert by[0]["group"] == "ungrouped" and by[0]["present"] == 1


def test_face_size_summary():
    s = face_size_summary([_o(0, "A", height=h) for h in (20, 40, 60, 80, 100)])
    assert s["n"] == 5 and s["min"] == 20 and s["median"] == 60
    assert face_size_summary([]) is None


# --------------------------------------------------------------------------- #
# the verdict — the whole point of the gate
# --------------------------------------------------------------------------- #
def test_wrong_marks_block_regardless_of_coverage():
    assert verdict(0.99, ["GHOST"])[0] == "BLOCKED"
    assert verdict(0.10, ["GHOST"])[0] == "BLOCKED"


def test_verdict_thresholds():
    assert verdict(0.95, [])[0] == "BUILD"
    assert verdict(0.90, [])[0] == "BUILD"
    assert verdict(0.80, [])[0] == "ITERATE"
    assert verdict(0.70, [])[0] == "ITERATE"
    assert verdict(0.69, [])[0] == "STOP"


# --------------------------------------------------------------------------- #
# synthetic generator sanity (it backs the numbers above)
# --------------------------------------------------------------------------- #
def test_synthetic_absentees_are_excluded_from_truth():
    obs, truth = synth_observations(20, frames=50, seed=3, absent_frac=0.2)
    assert len(truth) == 16                       # 20 - 20% absent
    assert set(o["sid"] for o in obs) <= set(truth)


def test_synthetic_back_row_is_harder_than_front():
    obs, truth = synth_observations(30, frames=150, seed=5)
    by = {g["group"]: g["coverage"] for g in coverage_by_group(obs, truth, 3)}
    assert by["front"] >= by["back"]


def test_synthetic_wrong_rate_produces_false_marks():
    obs, truth = synth_observations(20, frames=150, seed=7, wrong_rate=0.9)
    assert coverage_report(obs, set(truth), min_hits=3)["false_marks"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
