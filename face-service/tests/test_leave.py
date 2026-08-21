"""Leave / excused-absence workflow (no Mongo).

The acceptance criterion for the story is that approving leave reclassifies the
affected attendance rows — and, just as importantly, that an excused absence stops
counting against the student's attendance rate.

    cd face-service
    python -m pytest tests/test_leave.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId                # noqa: E402
from app.db import Store                 # noqa: E402


class _Res:
    def __init__(self, inserted_id=None, modified=0):
        self.inserted_id = inserted_id
        self.modified_count = modified


class _Cursor(list):
    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return list(self)


class _Col:
    def __init__(self, docs=None):
        self.docs = docs or []

    @staticmethod
    def _match(d, q):
        for k, v in q.items():
            dv = d.get(k)
            if isinstance(v, dict):
                if "$in" in v and dv not in v["$in"]:
                    return False
                if "$lte" in v and not (dv is not None and dv <= v["$lte"]):
                    return False
                if "$gte" in v and not (dv is not None and dv >= v["$gte"]):
                    return False
            elif dv != v:
                return False
        return True

    def find_one(self, q, projection=None):
        return next((d for d in self.docs if self._match(d, q)), None)

    def find(self, q=None, projection=None):
        return _Cursor([d for d in self.docs if self._match(d, q or {})])

    def insert_one(self, doc):
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return _Res(inserted_id=doc["_id"])

    def update_one(self, q, upd, upsert=False):
        d = self.find_one(q)
        if not d:
            return _Res(modified=0)
        d.update(upd.get("$set", {}))
        return _Res(modified=1)

    def update_many(self, q, upd):
        n = 0
        for d in self.docs:
            if self._match(d, q):
                d.update(upd.get("$set", {}))
                n += 1
        return _Res(modified=n)

    def create_index(self, *a, **k):
        pass


STU = {"InId": "IN1", "type": "student", "sid": "S9", "loginId": "L9", "courses": None}
STAFF_IN = {"InId": "IN1", "type": "staff", "loginId": "T1", "courses": {"C1"}}
STAFF_OUT = {"InId": "IN1", "type": "staff", "loginId": "T2", "courses": {"C2"}}


def _row(date, status, sid="S9"):
    return {"_id": ObjectId(), "InId": "IN1", "CrID": "C1", "StuID": sid,
            "date": date, "session": "Morning", "status": status}


def _store(rows=()):
    s = object.__new__(Store)
    s._indexed = True
    s.attn = _Col(list(rows))
    s.leave = _Col()
    s.students = _Col([{"StuID": "S9", "CurCrNm": "Law"}])
    s.get = lambda sid: ({"sid": "S9", "name": "Dara", "cls": "Law",
                          "raw": {"InId": "IN1", "CurCrID": "C1", "CurSecID": "SC1"}}
                         if sid == "S9" else None)
    return s


# --------------------------------------------------------------------------- #
# requesting
# --------------------------------------------------------------------------- #
def test_student_can_request_leave():
    s = _store()
    lv, err = s.request_leave(STU, "2026-08-24", "2026-08-26", reason="Hospital")
    assert err is None and lv["state"] == "pending"
    assert lv["startDate"] == "2026-08-24" and lv["reason"] == "Hospital"


def test_only_a_student_may_request():
    s = _store()
    lv, err = s.request_leave(STAFF_IN, "2026-08-24", "2026-08-24")
    assert lv is None and err == "forbidden"


def test_dates_are_validated():
    s = _store()
    for a, b in (("nope", "2026-08-24"), ("2026-08-24", "24/08/2026"),
                 ("2026-08-26", "2026-08-24")):          # end before start
        lv, err = s.request_leave(STU, a, b)
        assert lv is None and err == "bad_dates", (a, b)


def test_overlapping_requests_are_refused():
    s = _store()
    s.request_leave(STU, "2026-08-24", "2026-08-26")
    lv, err = s.request_leave(STU, "2026-08-26", "2026-08-28")   # shares one day
    assert lv is None and err == "overlap"


def test_non_overlapping_requests_are_fine():
    s = _store()
    s.request_leave(STU, "2026-08-24", "2026-08-26")
    lv, err = s.request_leave(STU, "2026-08-27", "2026-08-28")
    assert err is None and lv["state"] == "pending"


# --------------------------------------------------------------------------- #
# approval reclassifies rows — the acceptance criterion
# --------------------------------------------------------------------------- #
def test_approval_excuses_absences_in_range_only():
    rows = [_row("2026-08-23", "A"),      # before the range — untouched
            _row("2026-08-24", "A"),      # in range -> excused
            _row("2026-08-25", "A"),      # in range -> excused
            _row("2026-08-27", "A")]      # after the range — untouched
    s = _store(rows)
    lv, _ = s.request_leave(STU, "2026-08-24", "2026-08-26")
    out, err = s.resolve_leave(STAFF_IN, lv["id"], "approve")
    assert err is None and out["state"] == "approved" and out["appliedRows"] == 2
    assert [r["status"] for r in rows] == ["A", "E", "E", "A"]


def test_approval_never_overwrites_present_or_late():
    # The student was demonstrably there; excusing it would erase a real record.
    rows = [_row("2026-08-24", "P"), _row("2026-08-25", "L"), _row("2026-08-26", "A")]
    s = _store(rows)
    lv, _ = s.request_leave(STU, "2026-08-24", "2026-08-26")
    out, _ = s.resolve_leave(STAFF_IN, lv["id"], "approve")
    assert out["appliedRows"] == 1
    assert [r["status"] for r in rows] == ["P", "L", "E"]


def test_approval_only_touches_the_requesting_student():
    rows = [_row("2026-08-24", "A", sid="S9"), _row("2026-08-24", "A", sid="OTHER")]
    s = _store(rows)
    lv, _ = s.request_leave(STU, "2026-08-24", "2026-08-24")
    s.resolve_leave(STAFF_IN, lv["id"], "approve")
    assert rows[0]["status"] == "E" and rows[1]["status"] == "A"


def test_rejection_changes_nothing():
    rows = [_row("2026-08-24", "A")]
    s = _store(rows)
    lv, _ = s.request_leave(STU, "2026-08-24", "2026-08-26")
    out, err = s.resolve_leave(STAFF_IN, lv["id"], "reject", note="No evidence")
    assert err is None and out["state"] == "rejected" and out["appliedRows"] == 0
    assert rows[0]["status"] == "A"


def test_staff_cannot_resolve_outside_their_courses():
    rows = [_row("2026-08-24", "A")]
    s = _store(rows)
    lv, _ = s.request_leave(STU, "2026-08-24", "2026-08-24")
    out, err = s.resolve_leave(STAFF_OUT, lv["id"], "approve")
    assert out is None and err == "forbidden" and rows[0]["status"] == "A"


def test_resolved_request_cannot_be_resolved_again():
    s = _store([_row("2026-08-24", "A")])
    lv, _ = s.request_leave(STU, "2026-08-24", "2026-08-24")
    s.resolve_leave(STAFF_IN, lv["id"], "approve")
    out, err = s.resolve_leave(STAFF_IN, lv["id"], "reject")
    assert out is None and err == "closed"


# --------------------------------------------------------------------------- #
# leave granted BEFORE the dates: excusing happens when the absence is entered
# --------------------------------------------------------------------------- #
def test_approved_leave_for_covers_the_range_inclusively():
    s = _store()
    lv, _ = s.request_leave(STU, "2026-08-24", "2026-08-26")
    s.resolve_leave(STAFF_IN, lv["id"], "approve")
    assert s.approved_leave_for("S9", "2026-08-24")      # first day
    assert s.approved_leave_for("S9", "2026-08-26")      # last day
    assert not s.approved_leave_for("S9", "2026-08-27")
    assert not s.approved_leave_for("OTHER", "2026-08-25")


def test_pending_leave_does_not_excuse_anything():
    s = _store()
    s.request_leave(STU, "2026-08-24", "2026-08-26")     # never approved
    assert not s.approved_leave_for("S9", "2026-08-25")


# --------------------------------------------------------------------------- #
# scoping of the queue
# --------------------------------------------------------------------------- #
def test_student_sees_only_their_own_requests():
    s = _store()
    s.request_leave(STU, "2026-08-24", "2026-08-24")
    s.leave.docs.append({"_id": ObjectId(), "InId": "IN1", "CrID": "C1",
                         "StuID": "OTHER", "state": "pending"})
    mine = s.list_leave(STU)
    assert len(mine) == 1 and mine[0]["sid"] == "S9"


def test_pending_requests_sort_first():
    s = _store()
    a, _ = s.request_leave(STU, "2026-08-01", "2026-08-01")
    s.resolve_leave(STAFF_IN, a["id"], "reject")
    s.request_leave(STU, "2026-08-24", "2026-08-24")
    assert s.list_leave(STAFF_IN)[0]["state"] == "pending"


# --------------------------------------------------------------------------- #
# excused must not count against the attendance rate
# --------------------------------------------------------------------------- #
def _stats_store(statuses):
    s = object.__new__(Store)
    s.attn = _Col([{"StuID": "S9", "status": st} for st in statuses])
    return s


def test_excused_leaves_the_rate_denominator():
    # 3 present, 1 absent -> 75%. Excusing that absence must RAISE it to 100%,
    # not lower it by sitting uncounted in the denominator.
    assert _stats_store(["P", "P", "P", "A"]).student_stats("S9")["rate"] == 75.0
    st = _stats_store(["P", "P", "P", "E"]).student_stats("S9")
    assert st["rate"] == 100.0 and st["excused"] == 1 and st["records"] == 4


def test_all_excused_does_not_divide_by_zero():
    st = _stats_store(["E", "E"]).student_stats("S9")
    assert st["rate"] == 0.0 and st["excused"] == 2


def test_profile_reducer_also_excludes_excused():
    r = Store._reduce_attendance([
        {"status": "P", "date": "2026-08-01"}, {"status": "P", "date": "2026-08-02"},
        {"status": "P", "date": "2026-08-03"}, {"status": "E", "date": "2026-08-04"}])
    assert r["rate"] == 100.0 and r["excused"] == 1 and r["records"] == 3


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
