"""Recognition gallery must be scoped to the caller (no Mongo).

A kiosk in one institute must never match — and therefore never auto-mark — a
student who belongs to another institute or (for a course-limited staff scope)
another class. These tests pin the masking in Store._scoped_gallery directly,
using a hand-built cached gallery so no database is needed.

    cd face-service
    python -m pytest tests/test_gallery_scope.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                      # noqa: E402
from app.db import Store                # noqa: E402


def _store():
    s = object.__new__(Store)           # skip __init__ (no Mongo)
    # Four enrolled rows across two institutes / three courses.
    s._gallery_meta = [
        {"sid": "A1", "name": "Ann",  "cls": "Law",   "InId": "IN1", "crid": "C1"},
        {"sid": "A2", "name": "Bo",   "cls": "Econ",  "InId": "IN1", "crid": "C2"},
        {"sid": "B1", "name": "Chan", "cls": "Law",   "InId": "IN2", "crid": "C9"},
        {"sid": "A3", "name": "Dara", "cls": "Law",   "InId": "IN1", "crid": "C1"},
    ]
    # Row i is a distinct unit vector so we can tell which rows survived masking.
    s._gallery_mat = np.eye(4, dtype=np.float32)
    return s


def _sids(mat, meta):
    return [m["sid"] for m in meta]


def test_admin_sees_only_its_institute():
    s = _store()
    scope = {"InId": "IN1", "type": "admin", "courses": None}
    mat, meta = s._scoped_gallery(scope)
    assert _sids(mat, meta) == ["A1", "A2", "A3"]     # IN2 excluded
    assert mat.shape[0] == 3


def test_staff_limited_to_their_courses():
    s = _store()
    scope = {"InId": "IN1", "type": "staff", "courses": {"C1"}}
    mat, meta = s._scoped_gallery(scope)
    assert _sids(mat, meta) == ["A1", "A3"]            # only course C1, same institute
    assert mat.shape[0] == 2


def test_other_institute_matches_nobody():
    s = _store()
    scope = {"InId": "IN_X", "type": "admin", "courses": None}
    mat, meta = s._scoped_gallery(scope)
    assert meta == [] and mat.shape[0] == 0


def test_kiosk_cannot_match_across_institutes():
    # The concrete risk: a student enrolled in IN2 must not surface to an IN1 kiosk.
    s = _store()
    mat, meta = s._scoped_gallery({"InId": "IN1", "type": "admin", "courses": None})
    assert "B1" not in _sids(mat, meta)


def test_embedding_rows_stay_aligned_to_meta():
    # Masking must keep each surviving embedding paired with its own metadata.
    s = _store()
    mat, meta = s._scoped_gallery({"InId": "IN1", "type": "staff", "courses": {"C2"}})
    assert _sids(mat, meta) == ["A2"]
    assert np.array_equal(mat[0], np.eye(4, dtype=np.float32)[1])   # A2 was row index 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
