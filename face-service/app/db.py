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
import hmac
import re
import secrets
import time
from datetime import datetime, timezone
import numpy as np
from pymongo import MongoClient, ASCENDING, DESCENDING
from bson import ObjectId

from .config import settings

_PBKDF2_ROUNDS = 240_000


def hash_password(password: str, rounds: int = _PBKDF2_ROUNDS) -> str:
    """Salted PBKDF2-SHA256 hash, for newly set passwords."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def verify_password(stored: str, password: str) -> bool:
    """Check a password against a stored hash, in constant time.

    Accepts two formats: the salted PBKDF2 hash written by hash_password(), and
    the legacy unsalted single-round `sha256$<hex>` the demo data was seeded with
    (sample-data/seed_sample_data.py), so existing logins keep working. Legacy
    hashes are weak — identical passwords collide and they're cheap to brute
    force — so re-hash with hash_password() whenever a password is next set.
    """
    stored = stored or ""
    pw = (password or "").encode()
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt, digest = stored.split("$", 3)
            calc = hashlib.pbkdf2_hmac("sha256", pw, bytes.fromhex(salt), int(rounds))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(calc.hex(), digest)
    if stored.startswith("sha256$"):
        return hmac.compare_digest(stored, "sha256$" + hashlib.sha256(pw).hexdigest())
    return False


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
        self.disputes = db[settings.disputes_coll]
        self.periods = db[settings.periods_coll]
        self.policies = db[settings.policies_coll]
        self._indexed = False
        # Cached gallery (embeddings joined to names). Rebuilt lazily and only when
        # enrollment changes, so live recognition doesn't re-scan Mongo every frame.
        self._gallery_cache = None
        # Vectorized form of the same gallery (stacked matrix + aligned metadata).
        self._gallery_mat = None
        self._gallery_meta = None
        # Fingerprint of face_embeddings when the cache was built, + when we last
        # re-checked it. Used to notice enrollments made by OTHER processes.
        self._gallery_stamp = None
        self._stamp_checked_at = 0.0

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
        if not verify_password(login.get("pwd"), password):
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
            # Disputes: staff queue reads by scope + state, newest first; the
            # per-record unique-ish lookup guards against duplicate open challenges.
            self.disputes.create_index([("InId", ASCENDING), ("state", ASCENDING),
                                        ("createdAt", DESCENDING)])
            self.disputes.create_index([("StuID", ASCENDING), ("createdAt", DESCENDING)])
            # One period-set document per institute.
            self.periods.create_index([("InId", ASCENDING)], unique=True)
            # One policy per scope level; the unique key is what makes an upsert
            # per (institute | course | section) safe.
            self.policies.create_index([("InId", ASCENDING), ("scope", ASCENDING),
                                        ("CrID", ASCENDING), ("SecID", ASCENDING)],
                                       unique=True)
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
        # Exclude the heavy vectors — the roster only needs the metadata/thumb.
        return {e["StuID"]: e for e in self.emb.find({}, {"emb": 0, "embs": 0})}

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
                "angles": (e.get("nAngles") if e else None),
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
        e = self.emb.find_one({"StuID": sid}, {"emb": 0, "embs": 0}) or {}
        return {"sid": sid, "name": _full_name(s), "cls": _student_class(s),
                "raw": s, "enrolled": bool(e),
                "quality": e.get("quality"), "embVer": e.get("embVer"),
                "thumb": e.get("thumb"), "angles": e.get("nAngles")}

    def _invalidate_gallery(self):
        self._gallery_cache = None
        self._gallery_mat = None
        self._gallery_meta = None
        self._gallery_stamp = None
        self._stamp_checked_at = 0.0

    def _emb_stamp(self):
        """(count, latest updatedAt) fingerprint of face_embeddings — one
        round-trip. Changes on any enroll, re-enroll, or clear, whichever
        process performed it."""
        cur = self.emb.aggregate([{"$group": {"_id": None, "n": {"$sum": 1},
                                             "latest": {"$max": "$updatedAt"}}}])
        d = next(iter(cur), None) or {}
        return d.get("n", 0), str(d.get("latest"))

    def _ensure_fresh_gallery(self):
        """Drop the cached gallery if another process changed enrollments.

        Our own writes call _invalidate_gallery() directly, so this exists purely
        to catch writes by SIBLING processes (uvicorn --workers > 1), which would
        otherwise keep serving a stale gallery for the life of the worker — a
        newly-enrolled student would simply never be recognized there. Throttled
        to one cheap aggregate every GALLERY_STAMP_TTL seconds so the live
        recognition loop isn't issuing an extra query on every frame."""
        if self._gallery_cache is None and self._gallery_mat is None:
            return                      # nothing cached; the next build reads fresh
        now = time.monotonic()
        if now - self._stamp_checked_at < settings.gallery_stamp_ttl_seconds:
            return
        self._stamp_checked_at = now
        try:
            stamp = self._emb_stamp()
        except Exception:
            return                      # Mongo hiccup — keep serving what we have
        if stamp != self._gallery_stamp:
            self._invalidate_gallery()

    def gallery_matrix(self, scope=None):
        """(matrix, meta) form of the gallery for vectorized matching.
        matrix: float32 (N, D) of L2-normalized embeddings (one row per angle);
        meta:   list of {sid, name, cls, InId, crid} aligned to matrix rows. Cached
        with the gallery and invalidated together on any enrollment change.

        When `scope` is given, only rows the caller may recognize are returned:
        same institute, and — for a course-limited staff scope — only their
        courses. This keeps a kiosk from ever matching a student in another
        institute/class; recognition must not see wider than the caller's data."""
        self._ensure_fresh_gallery()
        if self._gallery_mat is None:
            g = self.gallery()
            if g:
                self._gallery_mat = np.stack([e["emb"] for e in g]).astype(np.float32)
                self._gallery_meta = [{"sid": e["sid"], "name": e["name"],
                                       "cls": e.get("cls"), "InId": e.get("InId"),
                                       "crid": e.get("crid")} for e in g]
            else:
                self._gallery_mat = np.zeros((0, 512), dtype=np.float32)
                self._gallery_meta = []
        if scope is None:
            return self._gallery_mat, self._gallery_meta
        return self._scoped_gallery(scope)

    def _scoped_gallery(self, scope):
        """Mask the cached gallery to the rows a scope may recognize. O(N) over the
        gallery — negligible next to detection, and it avoids a per-scope cache."""
        inid = scope.get("InId")
        courses = scope.get("courses")
        idx = [i for i, m in enumerate(self._gallery_meta)
               if m.get("InId") == inid
               and (courses is None or m.get("crid") in courses)]
        if len(idx) == len(self._gallery_meta):
            return self._gallery_mat, self._gallery_meta          # nothing filtered
        if not idx:
            dim = self._gallery_mat.shape[1] if self._gallery_mat.ndim == 2 else 512
            return np.zeros((0, dim), dtype=np.float32), []
        return self._gallery_mat[idx], [self._gallery_meta[i] for i in idx]

    def gallery(self):
        """Enrolled students with embeddings as numpy arrays, joined to names.

        Emits one gallery row per stored angle so a turned face still matches the
        student. Reads both new multi-angle docs (embs[]) and legacy single-vector
        docs (emb) — best_match takes the max similarity across all rows, so extra
        angles only improve recall. Cached until enrollment changes so the live
        recognition loop doesn't re-scan Mongo on every frame."""
        self._ensure_fresh_gallery()
        if self._gallery_cache is not None:
            return self._gallery_cache
        # Fingerprint BEFORE reading the vectors: if a write lands mid-read we
        # record the older stamp and rebuild on the next check. Stamping after
        # the read could pin a stamp newer than the data we actually loaded and
        # leave the cache stale for good.
        try:
            self._gallery_stamp = self._emb_stamp()
        except Exception:
            self._gallery_stamp = None
        self._stamp_checked_at = time.monotonic()
        # Carry InId + course id so the gallery can be masked to a caller's scope
        # at recognition time (see gallery_matrix(scope)).
        meta_by_sid = {}
        for s in self.students.find({}, {"_id": 0}):
            meta_by_sid[_student_sid(s)] = (_full_name(s), _student_class(s),
                                            s.get("InId"), s.get("CurCrID"))
        g = []
        for e in self.emb.find({}):
            nm, cls, inid, crid = meta_by_sid.get(e["StuID"], (e["StuID"], None, None, None))
            vecs = e.get("embs")
            if vecs:
                for v in vecs:
                    g.append({"sid": e["StuID"], "name": nm, "cls": cls,
                              "InId": inid, "crid": crid, "pose": v.get("pose"),
                              "emb": np.asarray(v["emb"], dtype=np.float32)})
            elif e.get("emb") is not None:        # legacy single-vector enrollment
                g.append({"sid": e["StuID"], "name": nm, "cls": cls,
                          "InId": inid, "crid": crid, "pose": None,
                          "emb": np.asarray(e["emb"], dtype=np.float32)})
        self._gallery_cache = g
        return g

    # -- enrollment (face_embeddings) ---------------------------------------
    def set_embedding(self, sid, emb, quality, thumb, emb_ver):
        """Legacy single-vector enrollment (kept for back-compat / tests)."""
        self.ensure_index()
        now = datetime.now(timezone.utc)
        self.emb.update_one(
            {"StuID": sid},
            {"$set": {"emb": [float(x) for x in emb], "quality": quality,
                      "thumb": thumb, "embVer": emb_ver, "updatedAt": now,
                      "embs": [{"emb": [float(x) for x in emb], "pose": "center",
                                "quality": quality}], "nAngles": 1},
             "$setOnInsert": {"enrolledAt": now}},
            upsert=True,
        )
        self._invalidate_gallery()

    def set_embeddings(self, sid, captures, emb_ver):
        """Multi-angle enrollment. `captures` is an ordered list of dicts:
        {emb, pose, yaw, quality, liveness_score, thumb}. The frontal (or first)
        capture's thumb/quality are surfaced at the top level for the roster.
        Replaces any prior profile (single- or multi-angle)."""
        self.ensure_index()
        now = datetime.now(timezone.utc)
        # Prefer the 'center' capture for the roster thumbnail; else the first.
        face = next((c for c in captures if c.get("pose") == "center"), captures[0])
        embs = [{"emb": [float(x) for x in c["emb"]], "pose": c.get("pose"),
                 "yaw": round(float(c.get("yaw", 0.0)), 2),
                 "quality": c.get("quality"),
                 "liveness_score": c.get("liveness_score")} for c in captures]
        self.emb.update_one(
            {"StuID": sid},
            {"$set": {"embs": embs, "nAngles": len(embs),
                      "quality": face.get("quality"), "thumb": face.get("thumb"),
                      "embVer": emb_ver, "updatedAt": now},
             "$unset": {"emb": ""},          # drop any legacy single vector
             "$setOnInsert": {"enrolledAt": now}},
            upsert=True,
        )
        self._invalidate_gallery()
        return True

    def clear_embedding(self, sid):
        ok = self.emb.delete_one({"StuID": sid}).deleted_count > 0
        if ok:
            self._invalidate_gallery()
        return ok

    def clear_all_embeddings(self):
        self.emb.delete_many({})
        self._invalidate_gallery()

    # -- attendance (real schema) -------------------------------------------
    @staticmethod
    def _today():
        # Local (Cambodia, UTC+7) calendar day, so late-evening marks don't roll
        # into the next UTC day when the server runs in UTC.
        return settings.today_str()

    def _subject_for_course(self, cr_id):
        s = self.subjects.find_one({"CrID": cr_id}, {"_id": 0, "SubID": 1, "SubNa": 1})
        return (s or {}).get("SubID"), (s or {}).get("SubNa")

    def mark_attendance(self, sid, session, source="face", similarity=None, date=None,
                        status="P"):
        """Insert a real attendance row. Idempotent per (StuID, date, session).
        `status` is normally 'P', or 'L' when the capture window has moved into
        its grace period (see capture_status). Returns (public_record, created)."""
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
            "date": date, "dateAt": date_at, "session": session,
            "status": status if status in ("P", "L", "A") else "P",
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

    def set_attendance(self, sid, date, session, status, scope=None):
        """Create or update the status of an attendance record for
        (sid, date, session). Used for manual edits. Scope-checked.
        Returns (public_record, error) — error in {None, 'unknown', 'forbidden'}."""
        self.ensure_index()
        rec = self.get(sid)
        if not rec:
            return None, "unknown"
        if scope and not self.can_view_student(scope, rec["raw"]):
            return None, "forbidden"
        status = status if status in ("P", "L", "A") else "P"
        date = date or self._today()
        session = session or "Morning"
        now = datetime.now(timezone.utc)
        existing = self.attn.find_one({"StuID": sid, "date": date, "session": session})
        if existing:
            self.attn.update_one(
                {"_id": existing["_id"]},
                {"$set": {"status": status, "updatedAt": now,
                          "editedBy": scope.get("loginId") if scope else None}})
            existing["status"] = status
            return self._attn_public(existing), None
        # no record yet for this slot -> create one with the chosen status
        s = rec["raw"]
        sub_id, sub_na = self._subject_for_course(s.get("CurCrID"))
        try:
            date_at = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            date_at = now
        doc = {
            "InId": s.get("InId"), "PrID": s.get("CurPrID"), "CrID": s.get("CurCrID"),
            "DeptID": s.get("CurDeptID"), "SemID": s.get("CurSemID"),
            "SecID": s.get("CurSecID"), "AcYr": s.get("CurAcYr"),
            "StuID": sid, "StuNa": rec["name"], "SubID": sub_id, "SubNa": sub_na,
            "date": date, "dateAt": date_at, "session": session, "status": status,
            "source": "manual", "markedBy": scope.get("loginId") if scope else None,
            "editedBy": scope.get("loginId") if scope else None,
            "similarity": None, "CrAt": now,
        }
        res = self.attn.insert_one(doc)
        doc["_id"] = res.inserted_id
        return self._attn_public(doc), None

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
        def _ts(r):
            t = r.get("updatedAt") or r.get("CrAt")
            try:
                return t.timestamp()
            except Exception:
                return 0.0

        recs = {}
        for r in self.attn.find(q):
            # When a student has multiple records for the day, show the most
            # recently updated one so a manual edit is authoritative (rather than
            # blindly preferring present over absent).
            prev = recs.get(r["StuID"])
            if prev is None or _ts(r) >= _ts(prev):
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

    # -- attendance periods / capture windows --------------------------------
    # The clock arithmetic below is deliberately pure (minutes since local
    # midnight) so it is unit-testable without Mongo or a frozen clock. Callers
    # must derive "now" from settings.now_local() — the app's local timezone is
    # UTC+7, so comparing a "08:00" window against a UTC clock would be off by
    # seven hours and reject every genuine check-in.
    @staticmethod
    def _hhmm_to_minutes(text):
        """'08:05' -> 485. Returns None for anything malformed."""
        try:
            hh, mm = str(text).split(":")
            hh, mm = int(hh), int(mm)
        except (ValueError, TypeError, AttributeError):
            return None
        if not (0 <= hh < 24 and 0 <= mm < 60):
            return None
        return hh * 60 + mm

    @staticmethod
    def window_status(period, now_minutes):
        """Status a mark earns inside a capture window, or None when outside.

        [start, end]            -> 'P'  (present)
        (end, end + grace]      -> 'L'  (late)
        anything else / invalid -> None (caller rejects the mark)
        """
        start = Store._hhmm_to_minutes((period or {}).get("start"))
        end = Store._hhmm_to_minutes((period or {}).get("end"))
        if start is None or end is None or end < start:
            return None
        try:
            grace = max(0, int(period.get("graceMinutes") or 0))
        except (ValueError, TypeError):
            grace = 0
        if start <= now_minutes <= end:
            return "P"
        if end < now_minutes <= end + grace:
            return "L"
        return None

    @staticmethod
    def window_state(period, now_minutes):
        """Where the clock sits relative to a window — richer than window_status
        so the UI can say "opens at 08:00" instead of just refusing.
        'before' | 'open' | 'grace' | 'closed'."""
        start = Store._hhmm_to_minutes((period or {}).get("start"))
        end = Store._hhmm_to_minutes((period or {}).get("end"))
        if start is None or end is None or end < start:
            return "closed"
        try:
            grace = max(0, int(period.get("graceMinutes") or 0))
        except (ValueError, TypeError):
            grace = 0
        if now_minutes < start:
            return "before"
        if now_minutes <= end:
            return "open"
        if now_minutes <= end + grace:
            return "grace"
        return "closed"

    @staticmethod
    def validate_periods(periods):
        """Return (cleaned, error). Pure, so the admin UI gets a precise message
        instead of a generic 500 — and so bad windows can never reach the DB and
        silently lock a campus out of taking attendance."""
        if not isinstance(periods, list):
            return None, "periods must be a list"
        cleaned, seen = [], set()
        for i, p in enumerate(periods):
            if not isinstance(p, dict):
                return None, f"period {i + 1} must be an object"
            code = str(p.get("code") or "").strip()
            name = str(p.get("name") or "").strip()
            if not code:
                return None, f"period {i + 1} needs a code"
            if not name:
                return None, f"period '{code}' needs a name"
            if code.lower() in seen:
                return None, f"duplicate period code '{code}'"
            seen.add(code.lower())
            start = Store._hhmm_to_minutes(p.get("start"))
            end = Store._hhmm_to_minutes(p.get("end"))
            if start is None:
                return None, f"period '{code}' has an invalid start time (use HH:MM)"
            if end is None:
                return None, f"period '{code}' has an invalid end time (use HH:MM)"
            if end < start:
                return None, f"period '{code}' ends before it starts"
            try:
                grace = int(p.get("graceMinutes") or 0)
            except (ValueError, TypeError):
                return None, f"period '{code}' has an invalid grace value"
            if grace < 0:
                return None, f"period '{code}' cannot have negative grace"
            cleaned.append({"code": code, "name": name,
                            "start": str(p["start"]).strip(),
                            "end": str(p["end"]).strip(), "graceMinutes": grace})
        return cleaned, None

    def get_periods(self, in_id):
        """Configured capture windows for an institute ([] when none)."""
        doc = self.periods.find_one({"InId": in_id}, {"_id": 0}) or {}
        return doc.get("periods") or []

    def set_periods(self, in_id, periods, login_id=None):
        """Replace an institute's capture windows. Returns (cleaned, error)."""
        cleaned, err = self.validate_periods(periods)
        if err:
            return None, err
        self.ensure_index()
        self.periods.update_one(
            {"InId": in_id},
            {"$set": {"periods": cleaned, "updatedBy": login_id,
                      "updatedAt": datetime.now(timezone.utc)}},
            upsert=True)
        return cleaned, None

    # -- capture mode policy (institute / course / section) -------------------
    MODES = ("individual", "class_camera", "both")
    SCOPES = ("institute", "course", "section")

    @staticmethod
    def default_policy():
        """Policy used when no row matches — mirrors pre-policy behaviour."""
        return {"scope": "default", "CrID": None, "SecID": None,
                "mode": settings.attendance_default_mode,
                "allowIndividualFallback": True, "enforceWindow": None,
                "inherited": True}

    @staticmethod
    def _policy_public(d):
        return {"id": str(d.get("_id", "")), "scope": d.get("scope"),
                "CrID": d.get("CrID"), "SecID": d.get("SecID"),
                "mode": d.get("mode"),
                "allowIndividualFallback": bool(d.get("allowIndividualFallback", True)),
                "enforceWindow": d.get("enforceWindow"),
                "updatedBy": d.get("updatedBy")}

    @staticmethod
    def validate_policy(scope, cr_id, sec_id, mode):
        """Return (fields, error). Pure. Rejects class_camera while the capture
        pipeline is unbuilt (CLASS_CAM_ENABLED) — otherwise an admin could point a
        class at a source nothing feeds and silently stop its attendance."""
        if scope not in Store.SCOPES:
            return None, f"scope must be one of {', '.join(Store.SCOPES)}"
        if mode not in Store.MODES:
            return None, f"mode must be one of {', '.join(Store.MODES)}"
        if mode in ("class_camera", "both") and not settings.class_cam_enabled:
            return None, ("Whole-class camera capture is not enabled on this server "
                          "(CLASS_CAM_ENABLED=false).")
        cr_id = (cr_id or "").strip() or None
        sec_id = (sec_id or "").strip() or None
        if scope in ("course", "section") and not cr_id:
            return None, f"a {scope} policy needs a course (CrID)"
        if scope == "section" and not sec_id:
            return None, "a section policy needs a section (SecID)"
        if scope == "institute":
            cr_id = sec_id = None
        elif scope == "course":
            sec_id = None
        return {"scope": scope, "CrID": cr_id, "SecID": sec_id, "mode": mode}, None

    def get_policies(self, in_id):
        """Every policy row for an institute, broadest scope first."""
        rows = [self._policy_public(d) for d in self.policies.find({"InId": in_id})]
        order = {"institute": 0, "course": 1, "section": 2}
        rows.sort(key=lambda r: (order.get(r["scope"], 9), r["CrID"] or "", r["SecID"] or ""))
        return rows

    def resolve_policy(self, in_id, cr_id=None, sec_id=None):
        """Effective policy for a class: section > course > institute > default.
        Most specific wins — the same precedence user_scope uses for InId/CrID."""
        candidates = []
        if cr_id and sec_id:
            candidates.append({"InId": in_id, "scope": "section", "CrID": cr_id,
                               "SecID": sec_id})
        if cr_id:
            candidates.append({"InId": in_id, "scope": "course", "CrID": cr_id,
                               "SecID": None})
        candidates.append({"InId": in_id, "scope": "institute", "CrID": None,
                           "SecID": None})
        for q in candidates:
            d = self.policies.find_one(q)
            if d:
                p = self._policy_public(d)
                p["inherited"] = False
                return p
        return self.default_policy()

    def set_policy(self, in_id, scope, cr_id, sec_id, mode,
                   allow_fallback=True, enforce_window=None, login_id=None):
        """Upsert one scope's policy. Returns (policy, error)."""
        fields, err = self.validate_policy(scope, cr_id, sec_id, mode)
        if err:
            return None, err
        self.ensure_index()
        key = {"InId": in_id, "scope": fields["scope"],
               "CrID": fields["CrID"], "SecID": fields["SecID"]}
        self.policies.update_one(
            key,
            {"$set": {"mode": fields["mode"],
                      "allowIndividualFallback": bool(allow_fallback),
                      "enforceWindow": enforce_window,
                      "updatedBy": login_id,
                      "updatedAt": datetime.now(timezone.utc)}},
            upsert=True)
        d = self.policies.find_one(key) or {**key, **fields}
        return self._policy_public(d), None

    def delete_policy(self, in_id, policy_id):
        """Remove an override so the scope falls back to its parent."""
        try:
            oid = ObjectId(policy_id)
        except Exception:
            return False
        return self.policies.delete_one({"_id": oid, "InId": in_id}).deleted_count > 0

    @staticmethod
    def individual_capture_allowed(policy):
        """May a face scan / student self check-in mark under this policy?
        class_camera classes only allow it as an explicit fallback, so a student
        the camera misses can still resolve their own case."""
        mode = (policy or {}).get("mode") or "individual"
        if mode in ("individual", "both"):
            return True
        return bool((policy or {}).get("allowIndividualFallback", True))

    def policy_state(self, in_id, session=None, now=None, cr_id=None, sec_id=None):
        """Capture state for a class, for the UI: the effective mode plus which
        window (if any) is live right now."""
        periods = self.get_periods(in_id)
        now = now or settings.now_local()
        policy = self.resolve_policy(in_id, cr_id, sec_id)
        enforce = policy.get("enforceWindow")
        if enforce is None:
            enforce = settings.attendance_enforce_window
        out = {"enforceWindow": bool(enforce),
               "mode": policy.get("mode") or "individual",
               "allowIndividualFallback": bool(policy.get("allowIndividualFallback", True)),
               "individualAllowed": self.individual_capture_allowed(policy),
               "policyScope": policy.get("scope"),
               "classCameraEnabled": settings.class_cam_enabled,
               "periods": periods,
               "period": None, "state": None, "markStatus": None,
               "now": now.isoformat()}
        period = self.match_period(periods, session) if session else None
        # With no session asked about, show the window that is currently live (if
        # any) so the UI can surface "open until ..." without guessing a label.
        if period is None and not session:
            mins = now.hour * 60 + now.minute
            period = next((p for p in periods
                           if self.window_state(p, mins) in ("open", "grace")), None)
        if period is None:
            return out
        mins = now.hour * 60 + now.minute
        out["period"] = period
        out["state"] = self.window_state(period, mins)
        out["markStatus"] = self.window_status(period, mins)
        return out

    @staticmethod
    def match_period(periods, session):
        """Find the period a session label refers to, by code or name
        (case-insensitive). Names mirror the legacy free-text session strings so
        existing attendance history keeps resolving."""
        want = (session or "").strip().lower()
        if not want:
            return None
        for p in periods:
            if want in ((p.get("code") or "").strip().lower(),
                        (p.get("name") or "").strip().lower()):
                return p
        return None

    def capture_status(self, in_id, session, now=None, enforce=None):
        """Status an AUTOMATED mark (face scan / self check-in) should get right
        now, or an error when capture isn't allowed.

        Returns (status, error) — error in {None, 'closed', 'unknown_session'}.
        Returns ('P', None) when enforcement is off or the institute has no
        periods configured, so behaviour is unchanged until an admin opts in.
        `enforce` lets a resolved policy override the server-wide default.
        """
        if enforce is None:
            enforce = settings.attendance_enforce_window
        if not enforce:
            return "P", None
        periods = self.get_periods(in_id)
        if not periods:
            return "P", None            # nothing configured -> unchanged behaviour
        period = self.match_period(periods, session)
        if period is None:
            # Periods ARE configured, so an unrecognised session label must not
            # become a way to sidestep the window — students post their own marks.
            return None, "unknown_session"
        now = now or settings.now_local()
        status = self.window_status(period, now.hour * 60 + now.minute)
        if status is None:
            return None, "closed"
        return status, None

    # -- attendance disputes (student "I was present" challenges) ------------
    # A dispute is a review item, never a silent edit: raising one only logs the
    # challenge; the attendance row changes only if staff APPROVE it.
    @staticmethod
    def _scope_dispute_query(scope):
        """Mongo filter fragment limiting the disputes collection to a scope:
        a student sees their own, staff sees their courses, admin the institute."""
        if not scope:
            return {}
        f = {"InId": scope["InId"]}
        if scope["type"] == "student":
            f["StuID"] = scope["sid"]
        elif scope["type"] == "staff" and scope.get("courses") is not None:
            f["CrID"] = {"$in": sorted(scope["courses"])}
        return f

    def _dispute_public(self, d):
        ts, rt = d.get("createdAt"), d.get("resolvedAt")
        return {
            "id": str(d.get("_id", "")), "sid": d.get("StuID"), "name": d.get("StuNa"),
            "cls": d.get("cls"), "subNa": d.get("SubNa"),
            "date": d.get("date"), "session": d.get("session"),
            "recordId": d.get("recordId"), "recordedStatus": d.get("recordedStatus"),
            "reason": d.get("reason"), "state": d.get("state"),
            "resolution": d.get("resolution"), "resolvedBy": d.get("resolvedBy"),
            "corrected": d.get("corrected", False),
            "createdAt": ts.isoformat() if isinstance(ts, datetime) else ts,
            "resolvedAt": rt.isoformat() if isinstance(rt, datetime) else rt,
        }

    def raise_dispute(self, scope, record_id, reason=None):
        """A student challenges one of their own attendance rows ("I was present").
        Logs a review item for staff; never edits the attendance log itself.
        Returns (public_dispute, error) — error in
        {None,'unknown','forbidden','already_present','duplicate'}."""
        self.ensure_index()
        try:
            oid = ObjectId(record_id)
        except Exception:
            return None, "unknown"
        row = self.attn.find_one({"_id": oid})
        if not row:
            return None, "unknown"
        # A student may only dispute their OWN record — nobody else's.
        if (not scope or scope.get("type") != "student"
                or row.get("StuID") != scope.get("sid")):
            return None, "forbidden"
        # Nothing to challenge if it's already a present mark.
        if row.get("status") == "P":
            return None, "already_present"
        # At most one OPEN dispute per record, so the queue can't be spammed.
        if self.disputes.find_one({"recordId": record_id, "state": "open"}):
            return None, "duplicate"
        s = self.students.find_one({"StuID": row.get("StuID")},
                                   {"_id": 0, "CurCrNm": 1, "CurCrCd": 1}) or {}
        now = datetime.now(timezone.utc)
        doc = {
            "InId": row.get("InId"), "CrID": row.get("CrID"),
            "StuID": row.get("StuID"), "StuNa": row.get("StuNa"),
            "SubNa": row.get("SubNa"), "cls": s.get("CurCrNm") or s.get("CurCrCd"),
            "recordId": record_id, "recordedStatus": row.get("status"),
            "date": row.get("date"), "session": row.get("session"),
            "reason": ((reason or "").strip()[:500]) or None,
            "state": "open", "createdAt": now,
            "resolution": None, "resolvedBy": None, "resolvedAt": None,
            "corrected": False,
        }
        res = self.disputes.insert_one(doc)
        doc["_id"] = res.inserted_id
        return self._dispute_public(doc), None

    def list_disputes(self, scope, state=None):
        """Disputes visible to the caller. Open items first, then newest — so a
        staff member's queue surfaces the work to do at the top."""
        q = dict(self._scope_dispute_query(scope))
        if state:
            q["state"] = state
        cur = self.disputes.find(q).sort("createdAt", DESCENDING).limit(500)
        items = [self._dispute_public(d) for d in cur]
        items.sort(key=lambda x: x["state"] != "open")   # stable: keeps newest-first
        return items

    def resolve_dispute(self, scope, dispute_id, action, note=None):
        """Staff/admin resolve a dispute. 'approve' corrects the attendance row to
        Present (the only path that mutates the log); 'reject' leaves it unchanged.
        Scope-checked. Returns (public_dispute, error) — error in
        {None,'unknown','forbidden','closed','bad_action'}."""
        self.ensure_index()
        try:
            oid = ObjectId(dispute_id)
        except Exception:
            return None, "unknown"
        d = self.disputes.find_one({"_id": oid})
        if not d:
            return None, "unknown"
        scoped = self._scope_dispute_query(scope)
        if d.get("InId") != scoped.get("InId"):
            return None, "forbidden"
        if isinstance(scoped.get("CrID"), dict):        # staff: course-limited
            if d.get("CrID") not in set(scoped["CrID"].get("$in", [])):
                return None, "forbidden"
        if d.get("state") != "open":
            return None, "closed"
        if action not in ("approve", "reject"):
            return None, "bad_action"
        now = datetime.now(timezone.utc)
        corrected = False
        if action == "approve" and d.get("recordId"):
            try:
                upd = self.attn.update_one(
                    {"_id": ObjectId(d["recordId"])},
                    {"$set": {"status": "P", "updatedAt": now,
                              "editedBy": scope.get("loginId"),
                              "correctedFromDispute": str(oid)}})
                corrected = upd.modified_count > 0
            except Exception:
                corrected = False
        state = "approved" if action == "approve" else "rejected"
        note = ((note or "").strip()[:500]) or None
        self.disputes.update_one(
            {"_id": oid},
            {"$set": {"state": state, "resolvedBy": scope.get("loginId"),
                      "resolvedAt": now, "resolution": note, "corrected": corrected}})
        d.update({"state": state, "resolvedBy": scope.get("loginId"),
                  "resolvedAt": now, "resolution": note, "corrected": corrected})
        return self._dispute_public(d), None


_store = None


def get_store() -> "Store":
    global _store
    if _store is None:
        _store = Store()
    return _store
