"""Attendance-dispute store logic (no Mongo, no LLM).

A dispute is a review item, never a silent edit — these tests pin the security
boundary (a student can only dispute their OWN record) and the one path that is
allowed to mutate the attendance log (staff APPROVE).

    cd face-service
    python -m pytest tests/test_disputes.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId               # noqa: E402
from app.db import Store                # noqa: E402


# --- tiny in-memory stand-ins for the Mongo collections we touch ----------- #
class _Res:
    def __init__(self, inserted_id=None, modified=0):
        self.inserted_id = inserted_id
        self.modified_count = modified


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return list(self._docs)

    def __iter__(self):
        return iter(self._docs)


class _Col:
    def __init__(self, docs=None):
        self.docs = docs or []

    @staticmethod
    def _match(d, q):
        for k, v in q.items():
            dv = d.get(k)
            if isinstance(v, dict) and "$in" in v:
                if dv not in v["$in"]:
                    return False
            elif dv != v:
                return False
        return True

    def find_one(self, q, projection=None):
        return next((d for d in self.docs if self._match(d, q)), None)

    def find(self, q=None):
        return _Cursor([d for d in self.docs if self._match(d, q or {})])

    def insert_one(self, doc):
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return _Res(inserted_id=doc["_id"])

    def update_one(self, q, upd):
        d = self.find_one(q)
        if not d:
            return _Res(modified=0)
        d.update(upd.get("$set", {}))
        return _Res(modified=1)


def _store(attn_docs):
    s = object.__new__(Store)          # skip __init__ (no Mongo connection)
    s._indexed = True                  # so ensure_index() is a no-op
    s.attn = _Col(attn_docs)
    s.disputes = _Col()
    s.students = _Col([{"StuID": "S9", "CurCrNm": "Law"}])
    return s


STU = {"InId": "IN1", "type": "student", "sid": "S9", "loginId": "L9", "courses": None}
STAFF_IN = {"InId": "IN1", "type": "staff", "loginId": "T1", "courses": {"C1"}}
STAFF_OUT = {"InId": "IN1", "type": "staff", "loginId": "T2", "courses": {"C2"}}


def _absent_row():
    rid = ObjectId()
    return rid, {"_id": rid, "InId": "IN1", "CrID": "C1", "StuID": "S9",
                 "StuNa": "Dara", "SubNa": "Torts", "date": "2026-08-20",
                 "session": "Morning", "status": "A"}


def test_student_can_dispute_own_absent_row():
    rid, row = _absent_row()
    s = _store([row])
    d, err = s.raise_dispute(STU, str(rid), reason="I was there")
    assert err is None
    assert d["state"] == "open" and d["recordedStatus"] == "A"
    assert d["reason"] == "I was there" and d["cls"] == "Law"


def test_cannot_dispute_someone_elses_record():
    rid, row = _absent_row()
    row["StuID"] = "S_OTHER"
    s = _store([row])
    d, err = s.raise_dispute(STU, str(rid))
    assert d is None and err == "forbidden"


def test_cannot_dispute_a_present_row():
    rid, row = _absent_row()
    row["status"] = "P"
    s = _store([row])
    d, err = s.raise_dispute(STU, str(rid))
    assert d is None and err == "already_present"


def test_no_duplicate_open_dispute():
    rid, row = _absent_row()
    s = _store([row])
    _, err1 = s.raise_dispute(STU, str(rid))
    _, err2 = s.raise_dispute(STU, str(rid))
    assert err1 is None and err2 == "duplicate"


def test_approve_corrects_the_row_to_present():
    rid, row = _absent_row()
    s = _store([row])
    d, _ = s.raise_dispute(STU, str(rid))
    out, err = s.resolve_dispute(STAFF_IN, d["id"], "approve")
    assert err is None and out["state"] == "approved" and out["corrected"] is True
    assert row["status"] == "P"                     # the log was corrected
    assert row["correctedFromDispute"] == d["id"]


def test_reject_leaves_the_row_unchanged():
    rid, row = _absent_row()
    s = _store([row])
    d, _ = s.raise_dispute(STU, str(rid))
    out, err = s.resolve_dispute(STAFF_IN, d["id"], "reject", note="marked correctly")
    assert err is None and out["state"] == "rejected" and out["corrected"] is False
    assert row["status"] == "A"                     # untouched


def test_staff_cannot_resolve_outside_their_courses():
    rid, row = _absent_row()
    s = _store([row])
    d, _ = s.raise_dispute(STU, str(rid))
    out, err = s.resolve_dispute(STAFF_OUT, d["id"], "approve")
    assert out is None and err == "forbidden"
    assert row["status"] == "A"                     # not corrected


def test_resolved_dispute_cannot_be_resolved_again():
    rid, row = _absent_row()
    s = _store([row])
    d, _ = s.raise_dispute(STU, str(rid))
    s.resolve_dispute(STAFF_IN, d["id"], "reject")
    out, err = s.resolve_dispute(STAFF_IN, d["id"], "approve")
    assert out is None and err == "closed"


def test_list_disputes_scopes_to_the_student():
    rid, row = _absent_row()
    s = _store([row])
    s.raise_dispute(STU, str(rid))
    mine = s.list_disputes(STU)
    assert len(mine) == 1 and mine[0]["sid"] == "S9"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
