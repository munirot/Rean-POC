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
        # tz_aware=True so datetimes read back from Mongo carry UTC tzinfo. Without
        # it PyMongo returns naive datetimes and isoformat() drops the offset, so
        # the browser reads a UTC time as if it were local.
        self.client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=4000,
                                  tz_aware=True, tzinfo=timezone.utc)
        db = self.client[settings.db_name]
        self.students = db[settings.students_coll]
        self.emb = db[settings.embeddings_coll]
        self.attn = db[settings.attendance_coll]
        self.subjects = db[settings.subjects_coll]
        self.logins = db[settings.logins_coll]
        self.institutes = db[settings.institutes_coll]
        self.assignments = db[settings.assignments_coll]
        self.staffs = db[settings.staffs_coll]
        self.chat_hist = db[settings.chat_history_coll]
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

    # -- authorization scope -------------------------------------------------
    def get_login(self, login_id):
        return self.logins.find_one({"LoginID": login_id})

    def _staff_courses(self, staff_id):
        """Set of CrIDs a staff member teaches (via their subjects)."""
        st = self.staffs.find_one({"StaffID": staff_id}) or {}
        sub_ids = [e.get("SubID") for e in st.get("SubE", []) if e.get("SubID")]
        if not sub_ids:
            return set()
        crs = self.subjects.find({"SubID": {"$in": sub_ids}}, {"_id": 0, "CrID": 1})
        return {c.get("CrID") for c in crs if c.get("CrID")}

    def user_scope(self, login):
        """Resolve a login doc into the data it is allowed to see.
        courses=None means 'no course-level restriction' (admin); a set means
        the staff member is limited to those CrIDs; students are limited to sid."""
        if not login:
            return None
        t = login.get("Type")
        scope = {"InId": login.get("InId"), "type": t,
                 "loginId": login.get("LoginID"), "staffId": login.get("StaffID"),
                 "sid": None, "courses": None}
        if t == "student" and login.get("Student"):
            scope["sid"] = login["Student"][0].get("ProfileId")
        if t == "staff" and login.get("StaffID"):
            scope["courses"] = self._staff_courses(login["StaffID"])
        return scope

    @staticmethod
    def can_view_student(scope, raw):
        """Whether a scope may read a given student document."""
        if not scope or raw.get("InId") != scope.get("InId"):
            return False
        if scope["type"] == "student":
            return _student_sid(raw) == scope.get("sid")
        if scope["type"] == "staff" and scope.get("courses") is not None:
            return raw.get("CurCrID") in scope["courses"]
        return True  # admin

    @staticmethod
    def _scope_student_query(scope):
        """Mongo filter fragment limiting the students collection to a scope."""
        if not scope:
            return {}
        f = {"InId": scope["InId"]}
        if scope["type"] == "student":
            f["$or"] = [{"StuID": scope["sid"]}, {"CmStudID": scope["sid"]}]
        elif scope["type"] == "staff" and scope.get("courses") is not None:
            f["CurCrID"] = {"$in": sorted(scope["courses"])}
        return f

    @staticmethod
    def _scope_attn_query(scope):
        """Mongo filter fragment limiting the attendance collection to a scope."""
        if not scope:
            return {}
        f = {"InId": scope["InId"]}
        if scope["type"] == "student":
            f["StuID"] = scope["sid"]
        elif scope["type"] == "staff" and scope.get("courses") is not None:
            f["CrID"] = {"$in": sorted(scope["courses"])}
        return f

    def ensure_index(self):
        if not self._indexed:
            self.emb.create_index([("StuID", ASCENDING)], unique=True)
            self.attn.create_index([("StuID", ASCENDING), ("date", ASCENDING),
                                    ("session", ASCENDING)])
            self.attn.create_index([("date", DESCENDING)])
            self.chat_hist.create_index([("loginId", ASCENDING),
                                         ("conversationId", ASCENDING),
                                         ("ts", ASCENDING)])
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

    def list_students(self, scope=None):
        embs = self._emb_map()
        out = []
        query = {"StFl": {"$ne": "I"}, **self._scope_student_query(scope)}
        for s in self.students.find(query):
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

    # ======================================================================= #
    # Student-success aggregation (attendance + assignments)
    # Pure reducers are staticmethods so they're unit-testable without Mongo.
    # ======================================================================= #
    @staticmethod
    def _reduce_attendance(records):
        """records: list of {status: P/L/A, date: 'YYYY-MM-DD'}.
        Present-rate counts 'P' (Late is tracked separately, not as present).
        Splits chronologically into prior/recent halves to detect a decline."""
        recs = [r for r in records if r.get("status") in ("P", "L", "A")]
        total = len(recs)
        present = sum(1 for r in recs if r.get("status") == "P")
        late = sum(1 for r in recs if r.get("status") == "L")
        absent = sum(1 for r in recs if r.get("status") == "A")
        rate = round(present / total * 100, 1) if total else 0.0

        def _rate(rows):
            n = len(rows)
            return round(sum(1 for r in rows if r.get("status") == "P") / n * 100, 1) if n else None

        recent_rate = prior_rate = None
        if total >= 4:
            ordered = sorted(recs, key=lambda r: r.get("date") or "")
            mid = total // 2
            prior_rate = _rate(ordered[:mid])
            recent_rate = _rate(ordered[mid:])
        return {"records": total, "present": present, "late": late, "absent": absent,
                "rate": rate, "priorRate": prior_rate, "recentRate": recent_rate}

    @staticmethod
    def _reduce_academics(assignment_docs, sid, today=None):
        """assignment_docs: list of assignment docs (with Catry, assgnDueDt and a
        Students[] array of {StuID, status, marks}). Reduces to per-category and
        overall figures for one student. 'missing' = assigned, past due, not
        submitted/graded."""
        today = today or settings.today_str()
        by_cat = {}
        submitted = graded = missing = 0
        for a in assignment_docs:
            entry = next((e for e in a.get("Students", []) if e.get("StuID") == sid), None)
            if entry is None:
                continue
            cat = a.get("Catry") or "Other"
            c = by_cat.setdefault(cat, {"count": 0, "graded": 0, "missing": 0, "_sum": 0.0})
            c["count"] += 1
            status = entry.get("status")
            marks = entry.get("marks")
            if status in ("submitted", "graded"):
                submitted += 1
            if status == "graded" and marks is not None:
                graded += 1
                c["graded"] += 1
                c["_sum"] += float(marks)
            is_missing = status == "assigned" and (a.get("assgnDueDt") or "") < today
            if is_missing:
                missing += 1
                c["missing"] += 1

        by_category = {}
        for cat, c in by_cat.items():
            by_category[cat] = {
                "count": c["count"], "graded": c["graded"], "missing": c["missing"],
                "avg": round(c["_sum"] / c["graded"], 1) if c["graded"] else None,
            }

        def _avg(cat):
            return by_category.get(cat, {}).get("avg")

        return {"quizAvg": _avg("Quiz"), "homeworkAvg": _avg("Homework"),
                "projectAvg": _avg("Project"), "submitted": submitted,
                "graded": graded, "missing": missing, "byCategory": by_category}

    @staticmethod
    def _signals(att, acad):
        """Threshold rules over the two blocks. Auditable, no ML."""
        s = settings
        sig = []
        if att.get("records") and att.get("rate", 100) < s.attn_low_rate:
            sig.append("attendance_low")
        pr, rr = att.get("priorRate"), att.get("recentRate")
        declining = pr is not None and rr is not None and (pr - rr) >= s.attn_decline_pts
        if declining:
            sig.append("attendance_declining")
        if acad.get("missing", 0) >= s.missing_assign_min:
            sig.append("missing_assignments")
        quiz = acad.get("quizAvg")
        quiz_low = quiz is not None and quiz < s.quiz_low_avg
        if quiz_low:
            sig.append("quiz_avg_below_60")
        if declining and (quiz_low or acad.get("missing", 0) >= s.missing_assign_min):
            sig.append("at_risk")
        return sig

    def _academics(self, sid):
        docs = list(self.assignments.find({"Students.StuID": sid}))
        return self._reduce_academics(docs, sid)

    def student_profile(self, sid):
        rec = self.get(sid)
        if not rec:
            return None
        raw = rec["raw"]
        att_recs = list(self.attn.find({"StuID": sid}, {"_id": 0, "status": 1, "date": 1}))
        att = self._reduce_attendance(att_recs)
        acad = self._academics(sid)
        return {"sid": sid, "name": rec["name"], "cls": rec["cls"],
                "InId": raw.get("InId"), "CrID": raw.get("CurCrID"),
                "attendance": att, "academics": acad,
                "signals": self._signals(att, acad)}

    def cohort_signals(self, cls, scope=None):
        """Every student in a class label + their signals, flagged first.
        When scope is given, students outside it are excluded."""
        out = []
        for s in self.students.find({"StFl": {"$ne": "I"}}):
            if _student_class(s) != cls:
                continue
            if scope and not self.can_view_student(scope, s):
                continue
            sid = _student_sid(s)
            prof = self.student_profile(sid)
            if not prof:
                continue
            out.append({"sid": sid, "name": prof["name"], "cls": prof["cls"],
                        "rate": prof["attendance"]["rate"],
                        "quizAvg": prof["academics"]["quizAvg"],
                        "missing": prof["academics"]["missing"],
                        "signals": prof["signals"]})
        out.sort(key=lambda x: (len(x["signals"]) == 0, "at_risk" not in x["signals"],
                                x["rate"]))
        return {"cls": cls, "total": len(out),
                "flagged": sum(1 for x in out if x["signals"]), "students": out}

    # -- chat history (per staff member, grouped into conversations) ---------
    def save_chat(self, scope, role, content, conversation_id, meta=None):
        """Persist one chat turn keyed to the login + conversation."""
        self.chat_hist.insert_one({
            "loginId": scope.get("loginId"), "staffId": scope.get("staffId"),
            "InId": scope.get("InId"), "conversationId": conversation_id,
            "role": role, "content": content,
            "meta": meta, "ts": datetime.now(timezone.utc)})

    def list_conversations(self, scope, limit=50):
        """One entry per conversation, most-recently-active first. Title = the
        first user message in that conversation."""
        pipeline = [
            {"$match": {"loginId": scope.get("loginId")}},
            {"$sort": {"ts": ASCENDING}},
            {"$group": {"_id": "$conversationId",
                        "title": {"$first": "$content"},
                        "updatedAt": {"$last": "$ts"},
                        "count": {"$sum": 1}}},
            {"$sort": {"updatedAt": DESCENDING}},
            {"$limit": limit},
        ]
        out = []
        for d in self.chat_hist.aggregate(pipeline):
            if not d.get("_id"):
                continue
            ts = d.get("updatedAt")
            out.append({"conversationId": d["_id"],
                        "title": (d.get("title") or "New chat")[:60],
                        "updatedAt": ts.isoformat() if isinstance(ts, datetime) else ts,
                        "count": d.get("count", 0)})
        return out

    def list_chat(self, scope, conversation_id=None, limit=None):
        """Messages for one conversation (oldest-first). Without an id, returns
        the caller's most recent turns across all conversations."""
        limit = limit or settings.chat_history_limit
        q = {"loginId": scope.get("loginId")}
        if conversation_id:
            q["conversationId"] = conversation_id
        docs = list(self.chat_hist.find(q, {"_id": 0}).sort("ts", DESCENDING).limit(limit))
        docs.reverse()
        return [{"role": d.get("role"), "content": d.get("content"),
                 "data": d.get("meta"), "conversationId": d.get("conversationId"),
                 "ts": d["ts"].isoformat() if isinstance(d.get("ts"), datetime) else d.get("ts")}
                for d in docs]

    def clear_chat(self, scope, conversation_id=None):
        """Delete one conversation, or all of the caller's history if no id."""
        q = {"loginId": scope.get("loginId")}
        if conversation_id:
            q["conversationId"] = conversation_id
        return self.chat_hist.delete_many(q).deleted_count

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
        # Local (Cambodia, UTC+7) calendar day, so late-evening marks don't roll
        # into the next UTC day when the server runs in UTC.
        return settings.today_str()

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
            # Anchor the day marker to local (Cambodia) midnight, not UTC midnight.
            date_at = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=settings.tzinfo)
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

    def list_attendance(self, date=None, cls=None, session=None, sid=None, limit=1000,
                         scope=None):
        q = dict(self._scope_attn_query(scope))   # InId + (StuID|CrID) scope
        if date:
            q["date"] = date
        if session:
            q["session"] = session
        if sid:
            q["StuID"] = sid
        # CrID may come from both the class filter and the staff scope — intersect.
        crid_sets = []
        if cls:
            course_crids = self._crids_for_class(cls)
            if course_crids:
                crid_sets.append(set(course_crids))
        if "CrID" in q and isinstance(q["CrID"], dict) and "$in" in q["CrID"]:
            crid_sets.append(set(q["CrID"]["$in"]))
        if crid_sets:
            inter = set.intersection(*crid_sets) if len(crid_sets) > 1 else crid_sets[0]
            q["CrID"] = {"$in": sorted(inter)}
        cur = self.attn.find(q).sort("CrAt", DESCENDING).limit(limit)
        return [self._attn_public(d) for d in cur]

    def _crids_for_class(self, cls):
        # match the UI's class label (course name/code) back to CrID(s)
        sids = self.students.find({"$or": [{"CurCrNm": cls}, {"CurCrCd": cls}]},
                                  {"_id": 0, "CurCrID": 1})
        return sorted({s.get("CurCrID") for s in sids if s.get("CurCrID")})

    def attendance_roster(self, date=None, cls=None, session=None, scope=None):
        """For a date, every student + whether they've registered attendance yet.
        checkedIn = has a Present/Late record; else 'not yet'."""
        date = date or self._today()
        q = {"date": date, **self._scope_attn_query(scope)}
        if session:
            q["session"] = session
        recs = {}
        for r in self.attn.find(q):
            # prefer a present/late record if a student has multiple sessions
            prev = recs.get(r["StuID"])
            if prev is None or (r.get("status") in ("P", "L") and prev.get("status") == "A"):
                recs[r["StuID"]] = r
        out = []
        for s in self.students.find({"StFl": {"$ne": "I"}, **self._scope_student_query(scope)}):
            sid = _student_sid(s)
            cls_name = _student_class(s)
            if cls and cls_name != cls:
                continue
            r = recs.get(sid)
            status = r.get("status") if r else None
            ts = r.get("CrAt") if r else None
            out.append({
                "sid": sid, "name": _full_name(s), "cls": cls_name,
                "checkedIn": bool(r) and status in ("P", "L"),
                "status": {"P": "present", "L": "late", "A": "absent"}.get(status, "none"),
                "session": (r.get("session") if r else session),
                "source": r.get("source") if r else None,
                "time": ts.isoformat() if isinstance(ts, datetime) else ts,
            })
        out.sort(key=lambda x: str(x["sid"]))
        checked = sum(1 for x in out if x["checkedIn"])
        return {"date": date, "students": out, "checked_in": checked,
                "not_yet": len(out) - checked, "total": len(out),
                "sessions": sorted(x for x in self.attn.distinct("session", {"date": date}) if x)}

    def attendance_range(self, start, end, scope=None):
        """Attendance aggregates over a date range [start, end] (YYYY-MM-DD),
        scoped to the caller. Used for 'last week / this month' questions."""
        attn_scope = self._scope_attn_query(scope)
        base = {"date": {"$gte": start, "$lte": end}, **attn_scope}
        records = self.attn.count_documents(base)
        present_records = self.attn.count_documents({**base, "status": "P"})
        distinct_present = len(self.attn.distinct("StuID", {**base, "status": "P"}))
        days = len(self.attn.distinct("date", base))
        total = self.students.count_documents(
            {"StFl": {"$ne": "I"}, **self._scope_student_query(scope)})
        return {"start": start, "end": end, "records": records,
                "present_records": present_records, "distinct_present": distinct_present,
                "days": days, "total_students": total}

    def delete_attendance(self, record_id):
        try:
            oid = ObjectId(record_id)
        except Exception:
            return False
        return self.attn.delete_one({"_id": oid}).deleted_count > 0

    def attendance_summary(self, date=None, scope=None):
        date = date or self._today()
        attn_scope = self._scope_attn_query(scope)
        stu_scope = self._scope_student_query(scope)
        present_sids = set(self.attn.distinct(
            "StuID", {"date": date, "status": "P", **attn_scope}))
        stu_query = {"StFl": {"$ne": "I"}, **stu_scope}
        total = self.students.count_documents(stu_query)
        by_class = {}
        for s in self.students.find(stu_query,
                                    {"_id": 0, "StuID": 1, "CmStudID": 1, "CurCrNm": 1, "CurCrCd": 1}):
            c = s.get("CurCrNm") or s.get("CurCrCd") or "—"
            by_class.setdefault(c, {"cls": c, "total": 0, "present": 0})
            by_class[c]["total"] += 1
            if _student_sid(s) in present_sids:
                by_class[c]["present"] += 1
        marked = self.attn.count_documents({"date": date, **attn_scope})
        return {
            "date": date,
            "total_students": total,
            "enrolled": self.enrolled_count(),
            "present": len(present_sids),
            "absent": max(0, total - len(present_sids)),
            "marked": marked,   # attendance rows recorded for the day (0 = none taken)
            "by_class": sorted(by_class.values(), key=lambda x: x["cls"]),
            "sessions": sorted(x for x in self.attn.distinct("session", {"date": date, **attn_scope}) if x),
        }


_store = None


def get_store() -> "Store":
    global _store
    if _store is None:
        _store = Store()
    return _store
