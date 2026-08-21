"""Ambiguity guard in best_match_vec (no model, no Mongo).

The guard exists so a near-tie between two DIFFERENT students is rejected rather
than risking a wrong attendance mark, while extra enrolled angles of the SAME
student never trip it.

    cd face-service
    python -m pytest tests/test_matching.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                              # noqa: E402
from app.engine import best_match_vec           # noqa: E402


def _u(*xy):
    v = np.array(xy, dtype=np.float32)
    return v / np.linalg.norm(v)


def _gallery(rows):
    """rows: list of (sid, unit_vec). Returns (mat, meta)."""
    mat = np.stack([v for _, v in rows]).astype(np.float32)
    meta = [{"sid": sid, "name": sid, "cls": "X"} for sid, _ in rows]
    return mat, meta


THR, MARGIN = 0.35, 0.05


def test_clear_match_is_recognized():
    mat, meta = _gallery([("A", _u(1, 0)), ("B", _u(1, 1))])
    m = best_match_vec(_u(1, 0), mat, meta, THR, margin=MARGIN)
    assert m["sid"] == "A" and m["recognized"] is True
    assert m["gap"] > MARGIN


def test_near_tie_between_two_students_is_ambiguous():
    # Probe sits exactly between A and B -> equal similarity, gap ~0.
    mat, meta = _gallery([("A", _u(1, 0)), ("B", _u(0, 1))])
    m = best_match_vec(_u(1, 1), mat, meta, THR, margin=MARGIN)
    assert m["recognized"] is False
    assert m.get("reason") == "ambiguous"
    assert m["gap"] < MARGIN


def test_extra_angles_of_same_student_do_not_trip_the_guard():
    # Two rows are the SAME student (A); the only other identity is far away.
    mat, meta = _gallery([("A", _u(1, 0)), ("A", _u(1, 1)), ("B", _u(0, 1))])
    m = best_match_vec(_u(1, 0), mat, meta, THR, margin=0.5)  # even a big margin
    assert m["sid"] == "A" and m["recognized"] is True
    assert m["gap"] == 1.0 or m["gap"] > 0.5


def test_margin_zero_disables_the_guard():
    mat, meta = _gallery([("A", _u(1, 0)), ("B", _u(0, 1))])
    m = best_match_vec(_u(1, 1), mat, meta, THR, margin=0.0)
    assert m["recognized"] is True and "reason" not in m


def test_below_threshold_is_not_recognized():
    mat, meta = _gallery([("A", _u(1, 3)), ("B", _u(0, 1))])
    m = best_match_vec(_u(1, 0), mat, meta, THR, margin=MARGIN)  # cos ~0.316 < 0.35
    assert m["recognized"] is False


def test_single_identity_has_full_gap():
    mat, meta = _gallery([("A", _u(1, 0)), ("A", _u(1, 1))])
    m = best_match_vec(_u(1, 0), mat, meta, THR, margin=0.3)
    assert m["recognized"] is True and m["gap"] == 1.0


def test_empty_gallery_reports_no_enrolled():
    m = best_match_vec(_u(1, 0), np.zeros((0, 2), dtype=np.float32), [], THR, margin=MARGIN)
    assert m["recognized"] is False and m["reason"] == "no_enrolled_students"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
