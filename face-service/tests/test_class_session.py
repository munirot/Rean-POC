"""Whole-class camera: accumulation, bucketing, close, and the device boundary.

Phase 1 of docs/class-camera-attendance-plan.md. No Mongo, no model — the fakes
cover the update operators the ingest path actually uses.

    cd face-service
    python -m pytest tests/test_class_session.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId                    # noqa: E402
from app.db import Store                     # noqa: E402
from app import auth as authmod              # noqa: E402
from app.config import settings              # noqa: E402


class _Res:
    def __init__(self, inserted_id=None, modified=0):
        self.inserted_id = inserted_id
        self.modified_count = modified


class _Col:
    """Supports the operators record_frame uses: $inc, $max, $set, $setOnInsert."""
    def __init__(self, docs=None):
        self.docs = docs or []

    @staticmethod
    def _match(d, q):
        return all(d.get(k) == v for k, v in q.items())

    def find_one(self, q, projection=None):
        return next((d for d in self.docs if self._match(d, q)), None)

    def find(self, q=None, projection=None):
        return [d for d in self.docs if self._match(d, q or {})]

    def insert_one(self, doc):
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return _Res(inserted_id=doc["_id"])

    def update_one(self, q, upd, upsert=False):
        d = self.find_one(q)
        if d is None:
            if not upsert:
                return _Res(modified=0)
            d = dict(q)
            d.setdefault("_id", ObjectId())
            d.update(upd.get("$setOnInsert", {}))
            self.docs.append(d)
        for k, v in upd.get("$inc", {}).items():
            d[k] = (d.get(k) or 0) + v
        for k, v in upd.get("$max", {}).items():
            if d.get(k) is None or v > d[k]:
                d[k] = v
        d.update(upd.get("$set", {}))
        return _Res(modified=1)

    def create_index(self, *a, **k):
        pass


STAFF = {"InId": "IN1", "type": "staff", "loginId": "T1", "courses": {"C1"}}
STAFF_OUT = {"InId": "IN1", "type": "staff", "loginId": "T2", "courses": {"C2"}}
ROSTER = [{"sid": "S1", "name": "Ann", "cls": "Law"},
          {"sid": "S2", "name": "Bo", "cls": "Law"},
          {"sid": "S3", "name": "Chan", "cls": "Law"}]


def _store(open_session=True):
    s = object.__new__(Store)
    s._indexed = True
    s.class_sessions = _Col()
    s.class_obs = _Col()
    s.attn = _Col()
    s.students = _Col([{"StuID": r["sid"], "CurCrID": "C1"} for r in ROSTER])
    s.list_students = lambda scope=None: list(ROSTER)
    s._student_course = lambda sid: "C1"
    marked = []
    def _mark(sid, session, source="face", similarity=None, date=None, status="P"):
        marked.append({"sid": sid, "session": session, "source": source,
                       "status": status, "date": date})
        return {"sid": sid, "status": status}, True
    s.mark_attendance = _mark
    s.marked = marked
    settings.class_cam_enabled = True
    return s


def _open(s):
    sess, err = s.open_class_session(STAFF, "C1", "SC1", date="2026-08-22",
                                     session="Morning", camera="ROOM-A")
    assert err is None
    return sess


# --------------------------------------------------------------------------- #
# opening a sitting
# --------------------------------------------------------------------------- #
def test_open_is_idempotent():
    s = _store()
    a = _open(s)
    b = _open(s)
    assert a["id"] == b["id"]              # re-tapping must not split the evidence
    assert len(s.class_sessions.docs) == 1


def test_open_refused_when_disabled():
    s = _store()
    settings.class_cam_enabled = False
    try:
        sess, err = s.open_class_session(STAFF, "C1", "SC1")
        assert sess is None and err == "disabled"
    finally:
        settings.class_cam_enabled = True


def test_staff_cannot_open_a_course_they_do_not_teach():
    s = _store()
    sess, err = s.open_class_session(STAFF_OUT, "C1", "SC1")
    assert sess is None and err == "forbidden"


def test_open_requires_a_course():
    s = _store()
    sess, err = s.open_class_session(STAFF, "", None)
    assert sess is None and err == "bad_class"


# --------------------------------------------------------------------------- #
# accumulation across frames
# --------------------------------------------------------------------------- #
def test_hits_accumulate_over_frames():
    s = _store(); sess = _open(s)
    for _ in range(3):
        s.record_frame(sess["id"], [{"sid": "S1", "similarity": 0.6, "gap": 0.2}])
    o = s.class_obs.find_one({"sessionId": sess["id"], "StuID": "S1"})
    assert o["hits"] == 3
    assert s.class_sessions.docs[0]["frames"] == 3


def test_duplicate_boxes_in_one_frame_count_once():
    """Two detections of the same face must not confirm a student on their own."""
    s = _store(); sess = _open(s)
    n = s.record_frame(sess["id"], [
        {"sid": "S1", "similarity": 0.55, "gap": 0.2},
        {"sid": "S1", "similarity": 0.71, "gap": 0.3},
    ])
    o = s.class_obs.find_one({"sessionId": sess["id"], "StuID": "S1"})
    assert n == 1 and o["hits"] == 1
    assert o["bestSim"] == 0.71            # keeps the strongest evidence


def test_best_similarity_is_a_running_max():
    s = _store(); sess = _open(s)
    for sim in (0.5, 0.9, 0.6):
        s.record_frame(sess["id"], [{"sid": "S1", "similarity": sim, "gap": 0.1}])
    assert s.class_obs.find_one({"StuID": "S1"})["bestSim"] == 0.9


def test_frames_with_no_matches_still_count():
    s = _store(); sess = _open(s)
    s.record_frame(sess["id"], [])
    assert s.class_sessions.docs[0]["frames"] == 1
    assert s.class_obs.docs == []


# --------------------------------------------------------------------------- #
# the three buckets (pure)
# --------------------------------------------------------------------------- #
def test_buckets_split_by_confirm_hits():
    obs = [{"StuID": "S1", "hits": 5, "bestSim": 0.8},
           {"StuID": "S2", "hits": 1, "bestSim": 0.4}]
    b = Store.bucket_observations(ROSTER, obs, confirm_hits=3)
    assert [r["sid"] for r in b["confirmed"]] == ["S1"]
    assert [r["sid"] for r in b["ambiguous"]] == ["S2"]
    assert [r["sid"] for r in b["notDetected"]] == ["S3"]


def test_every_student_lands_in_exactly_one_bucket():
    obs = [{"StuID": "S1", "hits": 9}, {"StuID": "S2", "hits": 2}]
    b = Store.bucket_observations(ROSTER, obs, confirm_hits=3)
    seen = [r["sid"] for k in ("confirmed", "ambiguous", "notDetected") for r in b[k]]
    assert sorted(seen) == ["S1", "S2", "S3"] and len(seen) == 3


def test_confirm_hits_boundary_is_inclusive():
    b = Store.bucket_observations(ROSTER, [{"StuID": "S1", "hits": 3}], confirm_hits=3)
    assert [r["sid"] for r in b["confirmed"]] == ["S1"]


# --------------------------------------------------------------------------- #
# closing — the safety guarantee
# --------------------------------------------------------------------------- #
def test_close_marks_only_the_confirmed():
    s = _store(); sess = _open(s)
    for _ in range(3):
        s.record_frame(sess["id"], [{"sid": "S1", "similarity": 0.7, "gap": 0.2}])
    s.record_frame(sess["id"], [{"sid": "S2", "similarity": 0.5, "gap": 0.1}])
    view, err = s.close_class_session(STAFF, sess["id"], confirm_hits=3)
    assert err is None
    assert [m["sid"] for m in s.marked] == ["S1"]
    assert s.marked[0]["source"] == "class_camera" and s.marked[0]["status"] == "P"


def test_close_never_marks_anyone_absent():
    """The locked guarantee (plan §2.1): a student the camera never saw is left to
    the teacher. Nothing may be written as 'A' by this path."""
    s = _store(); sess = _open(s)
    for _ in range(3):
        s.record_frame(sess["id"], [{"sid": "S1", "similarity": 0.7, "gap": 0.2}])
    view, _ = s.close_class_session(STAFF, sess["id"], confirm_hits=3)
    assert all(m["status"] == "P" for m in s.marked)
    assert {r["sid"] for r in view["notDetected"]} == {"S2", "S3"}
    assert "S3" not in [m["sid"] for m in s.marked]


def test_close_reports_stats_and_flips_state():
    s = _store(); sess = _open(s)
    for _ in range(3):
        s.record_frame(sess["id"], [{"sid": "S1", "similarity": 0.7, "gap": 0.2}])
    view, _ = s.close_class_session(STAFF, sess["id"], confirm_hits=3)
    assert view["stats"] == {"confirmed": 1, "ambiguous": 0, "notDetected": 2, "marked": 1}
    assert s.class_sessions.docs[0]["state"] == "closed"


def test_closing_twice_is_refused():
    s = _store(); sess = _open(s)
    s.close_class_session(STAFF, sess["id"])
    view, err = s.close_class_session(STAFF, sess["id"])
    assert view is None and err == "closed"


def test_staff_outside_the_course_cannot_view_or_close():
    s = _store(); sess = _open(s)
    assert s.class_session_view(STAFF_OUT, sess["id"])[1] == "forbidden"
    assert s.close_class_session(STAFF_OUT, sess["id"])[1] == "forbidden"


# --------------------------------------------------------------------------- #
# device token boundary
# --------------------------------------------------------------------------- #
def test_device_token_round_trips():
    t = authmod.issue_device("ROOM-A", "IN1", ttl_days=1)
    d = authmod.verify_device(t)
    assert d["room"] == "ROOM-A" and d["InId"] == "IN1"


def test_device_token_is_not_a_user_session():
    """A physically reachable camera must not be able to act as a person."""
    t = authmod.issue_device("ROOM-A", "IN1")
    claims = authmod.verify(t)
    # verify() may parse it, but it carries typ 'device' and a ROOM as the subject,
    # so current_user's `logins` lookup can never resolve it to a person.
    assert claims is None or claims.get("type") == "device"


def test_user_session_is_not_a_device_token():
    assert authmod.verify_device(authmod.issue("LG-ADMIN", "admin")) is None


def test_expired_and_tampered_device_tokens_are_rejected():
    assert authmod.verify_device(authmod.issue_device("R", "IN1", ttl_days=-1)) is None
    t = authmod.issue_device("ROOM-A", "IN1")
    body, sig = t.rsplit(".", 1)
    assert authmod.verify_device(body + "." + ("A" * len(sig))) is None


def test_zz_restore_class_cam_flag():
    settings.class_cam_enabled = False
    assert settings.class_cam_enabled is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
