"""MongoDB store wired to the REAL migrated collections.

- `students`         : real student docs (mstudent shape: CmStudID/StuID, FNa, LNa,
                       CurCrID/CurCrNm, CurDeptID/CurDeptNm, CurSemNm, CurSecNm ...)
- `face_embeddings`  : one doc per enrolled student — { StuID, emb[512], embVer,
                       quality, thumb, enrolledAt } (kept separate so we never
                       mutate real student documents)
- `attendance`       : real attendance log (InId/PrID/CrID/DeptID/SemID/SecID/AcYr,
                       StuID, StuNa, SubID, SubNa, date, session, status, source ...)
- `subjects`         : used to resolve a SubID/SubNa for a student's course when
                       face-marking attendance.
"""
import hashlib
import re
from datetime import datetime, timezone
import numpy as np
from pymongo import MongoClient, ASCENDING, DESCENDING
from bson import ObjectId

from .config import settings


def _full_name(s):
    return " ".join(x for x in [s.get("FNa"), s.get("LNa")] if x) or s.get("Name") or s.get("StuID")


def _student_sid(s):
    return s.get("StuID") or s.get("CmStudID")


def _student_class(s):
    # what the UI shows in the "Class" column — course name is the most useful
    return s.get("CurCrNm") or s.get("CurCrCd") or s.get("CurSemNm") or "—"


class Store:
    def __init__(self):
        self.client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=4000)
        db = self.client[settings.db_name]
        self.students = db[settings.students_coll]
        self.emb = db[settings.embeddings_coll]
        self.attn = db[settings.attendance_coll]
        self.subjects = db[settings.subjects_coll]
        self.logins = db[settings.logins_coll]
        self.institutes = db[settings.institutes_coll]
        self._indexed = False

    def ping(self):
        self.client.admin.command("ping")

    # -- auth / institutes ---------------------------------------------------
    def list_institutes(self, q=None):
        query = {}
        if q:
            query["InNa"] = {"$regex": re.escape(q), "$options": "i"}
        return [{k: v for k, v in d.items() if k != "_id"}
                for d in self.institutes.find(query).limit(20)]

    def authenticate(self, username, password):
        uname = (username or "").strip()
        login = self.logins.find_one(
            {"$or": [{"Email": uname.lower()}, {"LoginID": uname}, {"Email": uname}]})
        if not login:
            return None
        expected = "sha256$" + hashlib.sha256((password or "").encode()).hexdigest()
        if login.get("pwd") != expected:
            return None
        user = {"name": login.get("Name"), "email": login.get("Email"),
                "type": login.get("Type"), "InId": login.get("InId"),
                "loginId": login.get("LoginID"), "sid": None, "staffId": login.get("StaffID")}
        if login.get("Student"):
            user["sid"] = login["Student"][0].get("ProfileId")
        return user

    def ensure_index(self):
        if not self._indexed:
            self.emb.create_index([("StuID", ASCENDING)], unique=True)
            self.attn.create_index([("StuID", ASCENDING), ("date", ASCENDING),
                                    ("session", ASCENDING)])
            self.attn.create_index([("date", DESCENDING)])
            self.attn.create_index([("dateAt", DESCENDING)])  # native Date range queries
            self._indexed = True

    # seed() kept as a no-op hook (real data comes from the migration, not from here)
    def seed(self, force: bool = False):
        self.ensure_index()

    # -- counts --------------------------------------------------------------
    def count(self) -> int:
        return self.students.count_documents({"StFl": {"$ne": "I"}})

    def enrolled_count(self) -> int:
        return self.emb.count_documents({})

    # -- students ------------------------------------------------------------
    def _emb_map(self):
        return {e["StuID"]: e for e in self.emb.find({}, {"emb": 0})}

    def list_students(self):
        embs = self._emb_map()
        out = []
        for s in self.students.find({"StFl": {"$ne": "I"}}):
            sid = _student_sid(s)
            e = embs.get(sid)
            out.append({
                "sid": sid, "name": _full_name(s), "cls": _student_class(s),
                "expected": False,
                "enrolled": e is not None,
                "quality": e.get("quality") if e else None,
                "embVer": e.get("embVer") if e else None,
                "thumb": e.get("thumb") if e else None,
            })
        out.sort(key=lambda x: str(x["sid"]))
        return out

    def get_student_full(self, sid):
        rec = self.get(sid)
        if not rec:
            return None
        prof = {k: v for k, v in rec["raw"].items() if k != "_id"}
        return {"sid": rec["sid"], "name": rec["name"], "cls": rec["cls"],
                "enrolled": rec["enrolled"], "quality": rec["quality"],
                "embVer": rec["embVer"], "thumb": rec["thumb"], "profile": prof}

    def student_stats(self, sid):
        recs = list(self.attn.find({"StuID": sid}, {"_id": 0, "status": 1}))
        total = len(recs)
        present = sum(1 for r in recs if r.get("status") == "P")
        late = sum(1 for r in recs if r.get("status") == "L")
        absent = sum(1 for r in recs if r.get("status") == "A")
        return {"records": total, "present": present, "late": late, "absent": absent,
                "rate": round(present / total * 100, 1) if total else 0.0}

    def get(self, sid: str):
        s = self.students.find_one({"$or": [{"StuID": sid}, {"CmStudID": sid}]}, {"_id": 0})
        if not s:
            return None
        e = self.emb.find_one({"StuID": sid}, {"emb": 0}) or {}
        return {"sid": sid, "name": _full_name(s), "cls": _student_class(s),
                "raw": s, "enrolled": bool(e),
                "quality": e.get("quality"), "embVer": e.get("embVer"), "thumb": e.get("thumb")}

    def gallery(self):
        """Enrolled students with embeddings as numpy arrays, joined to names."""
        name_by_sid = {}
        for s in self.students.find({}, {"_id": 0}):
            name_by_sid[_student_sid(s)] = (_full_name(s), _student_class(s))
        g = []
        for e in self.emb.find({}):
            nm, cls = name_by_sid.get(e["StuID"], (e["StuID"], None))
            g.append({"sid": e["StuID"], "name": nm, "cls": cls,
                      "emb": np.asarray(e["emb"], dtype=np.float32)})
        return g

    # -- enrollment (face_embeddings) ---------------------------------------
    def set_embedding(self, sid, emb, quality, thumb, emb_ver):
        self.ensure_index()
        now = datetime.now(timezone.utc)
        self.emb.update_one(
            {"StuID": sid},
            {"$set": {"emb": [float(x) for x in emb], "quality": quality,
                      "thumb": thumb, "embVer": emb_ver, "updatedAt": now},
             "$setOnInsert": {"enrolledAt": now}},
            upsert=True,
        )
        return True

    def clear_embedding(self, sid):
        return self.emb.delete_one({"StuID": sid}).deleted_count > 0

    def clear_all_embeddings(self):
        self.emb.delete_many({})

    # -- attendance (real schema) -------------------------------------------
    @staticmethod
    def _today():
        return datetime.now().strftime("%Y-%m-%d")

    def _subject_for_course(self, cr_id):
        s = self.subjects.find_one({"CrID": cr_id}, {"_id": 0, "SubID": 1, "SubNa": 1})
        return (s or {}).get("SubID"), (s or {}).get("SubNa")

    def mark_attendance(self, sid, session, source="face", similarity=None, date=None):
        """Insert a real attendance row (status 'P'). Idempotent per
        (StuID, date, session). Returns (public_record, created)."""
        self.ensure_index()
        rec = self.get(sid)
        if not rec:
            return None, False
        s = rec["raw"]
        date = date or self._today()
        session = session or "Morning"
        existing = self.attn.find_one({"StuID": sid, "date": date, "session": session})
        if existing:
            return self._attn_public(existing), False
        sub_id, sub_na = self._subject_for_course(s.get("CurCrID"))
        now = datetime.now(timezone.utc)
        try:
            date_at = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            date_at = now
        doc = {
            "InId": s.get("InId"), "PrID": s.get("CurPrID"), "CrID": s.get("CurCrID"),
            "DeptID": s.get("CurDeptID"), "SemID": s.get("CurSemID"),
            "SecID": s.get("CurSecID"), "AcYr": s.get("CurAcYr"),
            "StuID": sid, "StuNa": rec["name"], "SubID": sub_id, "SubNa": sub_na,
            "date": date, "dateAt": date_at, "session": session, "status": "P",
            "source": source, "markedBy": None,
            "similarity": round(float(similarity), 4) if similarity is not None else None,
            "CrAt": now,
        }
        res = self.attn.insert_one(doc)
        doc["_id"] = res.inserted_id
        return self._attn_public(doc), True

    def _attn_public(self, d):
        # join a class label for the record if not present
        cls = d.get("CrNm")
        if not cls and d.get("StuID"):
            s = self.students.find_one({"StuID": d["StuID"]}, {"_id": 0, "CurCrNm": 1, "CurCrCd": 1})
            if s:
                cls = s.get("CurCrNm") or s.get("CurCrCd")
        ts = d.get("CrAt")
        return {"id": str(d.get("_id", "")), "sid": d.get("StuID"),
                "name": d.get("StuNa"), "cls": cls, "subNa": d.get("SubNa"),
                "date": d.get("date"), "session": d.get("session"), "source": d.get("source"),
                "status": d.get("status"), "similarity": d.get("similarity"),
                "ts": ts.isoformat() if isinstance(ts, datetime) else ts}

    def list_attendance(self, date=None, cls=None, session=None, sid=None, limit=1000):
        q = {}
        if date:
            q["date"] = date
        if session:
            q["session"] = session
        if sid:
            q["StuID"] = sid
        # resolve class name -> CrID(s) for filtering
        if cls:
            course_crids = self._crids_for_class(cls)
            if course_crids:
                q["CrID"] = {"$in": course_crids}
        cur = self.attn.find(q).sort("CrAt", DESCENDING).limit(limit)
        return [self._attn_public(d) for d in cur]

    def _crids_for_class(self, cls):
        # match the UI's class label (course name/code) back to CrID(s)
        sids = self.students.find({"$or": [{"CurCrNm": cls}, {"CurCrCd": cls}]},
                                  {"_id": 0, "CurCrID": 1})
        return sorted({s.get("CurCrID") for s in sids if s.get("CurCrID")})

    def delete_attendance(self, record_id):
        try:
            oid = ObjectId(record_id)
        except Exception:
            return False
        return self.attn.delete_one({"_id": oid}).deleted_count > 0

    def attendance_summary(self, date=None):
        date = date or self._today()
        present_sids = set(self.attn.distinct("StuID", {"date": date, "status": "P"}))
        total = self.count()
        by_class = {}
        for s in self.students.find({"StFl": {"$ne": "I"}},
                                    {"_id": 0, "StuID": 1, "CmStudID": 1, "CurCrNm": 1, "CurCrCd": 1}):
            c = s.get("CurCrNm") or s.get("CurCrCd") or "—"
            by_class.setdefault(c, {"cls": c, "total": 0, "present": 0})
            by_class[c]["total"] += 1
            if _student_sid(s) in present_sids:
                by_class[c]["present"] += 1
        return {
            "date": date,
            "total_students": total,
            "enrolled": self.enrolled_count(),
            "present": len(present_sids),
            "absent": max(0, total - len(present_sids)),
            "by_class": sorted(by_class.values(), key=lambda x: x["cls"]),
            "sessions": sorted(x for x in self.attn.distinct("session", {"date": date}) if x),
        }


_store = None


def get_store() -> "Store":
    global _store
    if _store is None:
        _store = Store()
    return _store
