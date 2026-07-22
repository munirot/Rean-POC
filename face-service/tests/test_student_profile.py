"""Unit tests for the student-success aggregation (attendance + assignments).

Runs without Mongo/insightface — exercises the pure reducers on fixture docs:

    cd face-service
    python -m pytest tests/test_student_profile.py -s
    # or: python tests/test_student_profile.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Store            # noqa: E402


# --------------------------------------------------------------------------- #
# _reduce_attendance
# --------------------------------------------------------------------------- #
def test_attendance_rate_counts_present_only():
    recs = [{"status": "P", "date": "2026-07-01"},
            {"status": "P", "date": "2026-07-02"},
            {"status": "L", "date": "2026-07-03"},
            {"status": "A", "date": "2026-07-04"}]
    a = Store._reduce_attendance(recs)
    assert a["records"] == 4
    assert a["present"] == 2 and a["late"] == 1 and a["absent"] == 1
    assert a["rate"] == 50.0


def test_attendance_ignores_unknown_status():
    recs = [{"status": "P", "date": "d1"}, {"status": "X", "date": "d2"}]
    a = Store._reduce_attendance(recs)
    assert a["records"] == 1 and a["present"] == 1


def test_attendance_trend_split_detects_decline():
    # prior half all present, recent half all absent -> big drop
    recs = ([{"status": "P", "date": f"2026-06-0{i}"} for i in range(1, 5)] +
            [{"status": "A", "date": f"2026-07-0{i}"} for i in range(1, 5)])
    a = Store._reduce_attendance(recs)
    assert a["priorRate"] == 100.0
    assert a["recentRate"] == 0.0


def test_attendance_no_trend_when_too_few_records():
    a = Store._reduce_attendance([{"status": "P", "date": "d1"}])
    assert a["priorRate"] is None and a["recentRate"] is None


# --------------------------------------------------------------------------- #
# _reduce_academics
# --------------------------------------------------------------------------- #
def _assign(catry, due, entries):
    return {"Catry": catry, "assgnDueDt": due, "Students": entries}


def test_academics_averages_only_graded_marks():
    docs = [
        _assign("Quiz", "2026-06-01", [{"StuID": "S1", "status": "graded", "marks": 80}]),
        _assign("Quiz", "2026-06-10", [{"StuID": "S1", "status": "graded", "marks": 60}]),
        # submitted-but-not-graded must not affect the average
        _assign("Quiz", "2026-06-20", [{"StuID": "S1", "status": "submitted", "marks": None}]),
    ]
    acad = Store._reduce_academics(docs, "S1", today="2026-07-21")
    assert acad["quizAvg"] == 70.0
    assert acad["byCategory"]["Quiz"]["graded"] == 2
    assert acad["submitted"] == 3   # 2 graded + 1 submitted all count as submitted


def test_academics_missing_is_assigned_and_past_due():
    docs = [
        _assign("Homework", "2026-06-01", [{"StuID": "S1", "status": "assigned", "marks": None}]),  # past due -> missing
        _assign("Homework", "2026-12-01", [{"StuID": "S1", "status": "assigned", "marks": None}]),  # future -> not missing
        _assign("Homework", "2026-06-01", [{"StuID": "S1", "status": "graded", "marks": 90}]),      # done -> not missing
    ]
    acad = Store._reduce_academics(docs, "S1", today="2026-07-21")
    assert acad["missing"] == 1
    assert acad["byCategory"]["Homework"]["missing"] == 1


def test_academics_ignores_other_students():
    docs = [_assign("Quiz", "2026-06-01", [{"StuID": "S2", "status": "graded", "marks": 10}])]
    acad = Store._reduce_academics(docs, "S1", today="2026-07-21")
    assert acad["quizAvg"] is None and acad["graded"] == 0


# --------------------------------------------------------------------------- #
# _signals
# --------------------------------------------------------------------------- #
def test_signal_attendance_low():
    att = {"records": 10, "rate": 60.0, "priorRate": None, "recentRate": None}
    acad = {"missing": 0, "quizAvg": None}
    assert "attendance_low" in Store._signals(att, acad)


def test_signal_at_risk_requires_decline_plus_academic():
    att = {"records": 8, "rate": 70.0, "priorRate": 90.0, "recentRate": 60.0}  # declines 30pt
    acad = {"missing": 3, "quizAvg": 55.0}
    sig = Store._signals(att, acad)
    assert "attendance_declining" in sig
    assert "missing_assignments" in sig
    assert "quiz_avg_below_60" in sig
    assert "at_risk" in sig


def test_signal_no_at_risk_when_academics_healthy():
    att = {"records": 8, "rate": 70.0, "priorRate": 90.0, "recentRate": 60.0}
    acad = {"missing": 0, "quizAvg": 88.0}
    sig = Store._signals(att, acad)
    assert "attendance_declining" in sig
    assert "at_risk" not in sig


def test_signal_clean_student_has_no_flags():
    att = {"records": 10, "rate": 95.0, "priorRate": 95.0, "recentRate": 96.0}
    acad = {"missing": 0, "quizAvg": 90.0}
    assert Store._signals(att, acad) == []


# --------------------------------------------------------------------------- #
# can_view_student (authorization scope)
# --------------------------------------------------------------------------- #
def _stu(inid="IN001", sid="S1", crid="CR001"):
    return {"InId": inid, "StuID": sid, "CurCrID": crid}


def test_admin_sees_any_student_in_own_institute():
    scope = {"InId": "IN001", "type": "admin", "sid": None, "courses": None}
    assert Store.can_view_student(scope, _stu(crid="CR999")) is True


def test_nobody_sees_across_institutes():
    scope = {"InId": "IN001", "type": "admin", "sid": None, "courses": None}
    assert Store.can_view_student(scope, _stu(inid="IN002")) is False


def test_staff_limited_to_their_courses():
    scope = {"InId": "IN001", "type": "staff", "sid": None, "courses": {"CR001"}}
    assert Store.can_view_student(scope, _stu(crid="CR001")) is True
    assert Store.can_view_student(scope, _stu(crid="CR002")) is False


def test_student_sees_only_self():
    scope = {"InId": "IN001", "type": "student", "sid": "S1", "courses": None}
    assert Store.can_view_student(scope, _stu(sid="S1")) is True
    assert Store.can_view_student(scope, _stu(sid="S2")) is False


# --------------------------------------------------------------------------- #
# scope query builders (Mongo filter fragments)
# --------------------------------------------------------------------------- #
def test_scope_student_query_admin_is_institute_only():
    scope = {"InId": "IN001", "type": "admin", "sid": None, "courses": None}
    assert Store._scope_student_query(scope) == {"InId": "IN001"}


def test_scope_student_query_staff_limits_courses():
    scope = {"InId": "IN001", "type": "staff", "sid": None, "courses": {"CR001", "CR002"}}
    q = Store._scope_student_query(scope)
    assert q["InId"] == "IN001"
    assert set(q["CurCrID"]["$in"]) == {"CR001", "CR002"}


def test_scope_student_query_student_limits_to_self():
    scope = {"InId": "IN001", "type": "student", "sid": "S1", "courses": None}
    q = Store._scope_student_query(scope)
    assert {"StuID": "S1"} in q["$or"]


def test_scope_attn_query_student_forces_sid():
    scope = {"InId": "IN001", "type": "student", "sid": "S1", "courses": None}
    assert Store._scope_attn_query(scope) == {"InId": "IN001", "StuID": "S1"}


def test_scope_attn_query_none_is_empty():
    assert Store._scope_attn_query(None) == {}
    assert Store._scope_student_query(None) == {}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
