"""FastAPI entrypoint — face-attendance POC service.

Routes (all under /api):
  GET    /api/health                     model + db status
  GET    /api/students                   roster with enrollment state
  POST   /api/students/{sid}/enroll      multipart image -> store 512-d embedding
  DELETE /api/students/{sid}/enroll      remove a student's face profile
  POST   /api/students/seed              (re)seed the sample roster
  POST   /api/students/reset             clear all embeddings
  POST   /api/recognize                  multipart image -> matched students + accuracy
  POST   /api/attendance                  mark a student present (dedupe per date+session)
  GET    /api/attendance                  list records (filter: date, cls, session)
  DELETE /api/attendance/{record_id}      undo a record
  GET    /api/attendance/summary          dashboard counts for a date

The static frontend in ../web is served at / so the whole POC runs from one
origin (http://localhost:8000), which also satisfies the browser's secure-context
requirement for live camera access.
"""
import os
import uuid
import logging
from typing import Optional, List
from contextlib import asynccontextmanager

# Ensure our app loggers (e.g. the chat NLP→query trace) surface in the console.
# basicConfig only adds a root handler if none exists, so it won't fight uvicorn.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s")

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import get_store
from . import engine as eng
from . import antispoof as anti
from . import chat as chatmod
from . import plan as planmod
from . import auth as authmod
from .schemas import (Health, Student, RecognizeResult, PoseAnalysis,
                      EnrollMultiResult,
                      MarkRequest, MarkResult, AttendanceRecord, AttendanceSummary,
                      Institute, LoginRequest, AuthUser, ChatRequest, AttendanceSet,
                      DisputeCreate, DisputeResolve)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the DB + model at startup so the first request isn't slow / surprising.
    try:
        store = get_store()
        store.ping()
        store.seed(force=False)
        print(f"[startup] MongoDB connected · {store.count()} students "
              f"({store.enrolled_count()} enrolled)")
    except Exception as e:  # pragma: no cover
        print(f"[startup] WARNING: MongoDB unavailable at {settings.mongo_uri}: {e}")
        print("[startup] Fix: set MONGO_URI (with credentials if your Mongo has auth) in .env")
    try:
        eng.get_engine()
        print(f"[startup] InsightFace model '{settings.model_pack}' ready "
              f"(device={settings.device}, det_size={settings.det_size})")
    except Exception as e:  # pragma: no cover
        print(f"[startup] WARNING: face model failed to load: {e}")
    if settings.antispoof_enabled:
        try:
            a = anti.get_antispoof()
            print(f"[startup] Anti-spoofing ready (backend={a.backend}, "
                  f"threshold={settings.liveness_threshold})")
        except Exception as e:  # pragma: no cover
            print(f"[startup] WARNING: anti-spoofing failed to load: {e}")
    else:
        print("[startup] Anti-spoofing DISABLED (ANTISPOOF_ENABLED=false)")
    if settings.auth_secret_is_ephemeral:
        print("[startup] WARNING: AUTH_SECRET is not set — signing session tokens "
              "with a random per-process key.")
        print("[startup] Everyone is logged out on restart, and tokens will NOT "
              "verify across uvicorn --workers. Set AUTH_SECRET in .env.")
    yield


app = FastAPI(title="Rean Face Attendance POC", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"], allow_headers=["*"],
)


# ---- API -------------------------------------------------------------------
@app.get("/api/health", response_model=Health)
def health():
    store = get_store()
    mongo = "up"
    try:
        store.ping()
    except Exception:
        mongo = "down"
    return Health(
        status="ok", model_pack=settings.model_pack, device=settings.device,
        det_size=settings.det_size, match_threshold=settings.match_threshold,
        match_margin=settings.match_margin,
        self_checkin_challenge=settings.self_checkin_challenge,
        students=(store.count() if mongo == "up" else 0),
        enrolled=(store.enrolled_count() if mongo == "up" else 0),
        mongo=mongo,
    )


@app.get("/api/institutes", response_model=list[Institute])
def list_institutes(q: Optional[str] = None):
    return get_store().list_institutes(q)


@app.post("/api/auth/login", response_model=AuthUser)
def login(req: LoginRequest):
    user = get_store().authenticate(req.username, req.password)
    if not user:
        raise HTTPException(401, "Invalid username or password")
    return AuthUser(ok=True, token=authmod.issue(user["loginId"], user["type"]), **user)


def current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Verify the signed session token and resolve the caller's authorization
    scope. Raises 401 if the token is missing, forged, or expired.

    The scope is always recomputed from the live `logins` document rather than
    read out of the token, so a role or course change applies immediately and a
    token can never assert more than the login currently grants."""
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    token = authorization.split(" ", 1)[1] if " " in authorization else authorization
    claims = authmod.verify(token)
    if not claims:
        raise HTTPException(401, "Invalid or expired session token")
    store = get_store()
    scope = store.user_scope(store.get_login(claims["loginId"]))
    if not scope:
        raise HTTPException(401, "Unknown session")
    return scope


def require_staff(user: dict) -> dict:
    """Guard for admin/staff-only actions (enrollment, marking, deletes)."""
    if user.get("type") not in ("admin", "staff"):
        raise HTTPException(403, "Staff or admin role required")
    return user


@app.get("/api/students", response_model=list[Student])
def list_students(user: dict = Depends(current_user)):
    return get_store().list_students(scope=user)


@app.get("/api/students/{sid}/profile")
def get_student_profile(sid: str, user: dict = Depends(current_user)):
    rec = get_store().get(sid)
    if not rec:
        raise HTTPException(404, f"Unknown student {sid}")
    if not get_store().can_view_student(user, rec["raw"]):
        raise HTTPException(403, "Not permitted to view this student")
    return get_store().get_student_full(sid)


@app.get("/api/students/{sid}/stats")
def get_student_stats(sid: str, user: dict = Depends(current_user)):
    rec = get_store().get(sid)
    if not rec:
        raise HTTPException(404, f"Unknown student {sid}")
    if not get_store().can_view_student(user, rec["raw"]):
        raise HTTPException(403, "Not permitted to view this student")
    return get_store().student_stats(sid)


@app.get("/api/students/{sid}/profile/full")
def get_student_profile_full(sid: str, user: dict = Depends(current_user)):
    """Unified student-success profile: attendance + academics + signals."""
    rec = get_store().get(sid)
    if not rec:
        raise HTTPException(404, f"Unknown student {sid}")
    if not get_store().can_view_student(user, rec["raw"]):
        raise HTTPException(403, "Not permitted to view this student")
    return get_store().student_profile(sid)


@app.get("/api/students/{sid}/plan")
def get_student_plan(sid: str, user: dict = Depends(current_user)):
    """Improvement suggestions grounded in the student's signals. Deterministic —
    never auto-applied. A student viewing their own plan gets supportive,
    first-person wording; staff/admin get the teacher-facing version."""
    rec = get_store().get(sid)
    if not rec:
        raise HTTPException(404, f"Unknown student {sid}")
    if not get_store().can_view_student(user, rec["raw"]):
        raise HTTPException(403, "Not permitted to view this student")
    audience = "student" if user.get("type") == "student" else "teacher"
    return planmod.build_plan(get_store().student_profile(sid), audience=audience)


@app.get("/api/analytics/cohort")
def get_cohort_signals(cls: str, user: dict = Depends(current_user)):
    """Every student in a class label + their at-risk signals (flagged first).
    Scoped to what the caller is allowed to see."""
    return get_store().cohort_signals(cls, scope=user)


@app.post("/api/chat")
def chat(req: ChatRequest, user: dict = Depends(current_user)):
    """Grounded chat over attendance + assignments, scoped to the caller. Open to
    any authenticated role: staff/admin ask about their students, a student asks
    about their own record — scope injection limits every query to what the caller
    may see, and history is keyed per login. Persists history."""
    store = get_store()
    conv = req.conversationId or uuid.uuid4().hex
    # Per-login rate limit: protects the shared LLM from a runaway client (students
    # chat too now). Rejected turns aren't persisted and never reach the model.
    if not chatmod.rate_limit_ok(user.get("loginId")):
        return {"answer": "You're sending messages a bit fast — give it a few "
                          "seconds and try again.", "intent": None,
                "error": "rate_limited", "conversationId": conv}
    # Prior turns for this conversation give the model context for follow-ups.
    history = store.list_chat(user, conversation_id=conv)
    result = chatmod.answer(store, req.message, scope=user, context_sid=req.sid,
                            history=history)
    if settings.chat_log_queries:
        ans = (result.get("answer") or "").replace("\n", " ")[:160]
        logging.getLogger("rean.chat").info(
            "[chat] conv=%s intent=%s answer=%r", conv[:8], result.get("intent"), ans)
    # Persist both turns so the conversation survives page reloads. The assistant
    # turn also stores the NLP→query trace in meta (tool choice, resolved intent,
    # the actual DB query + counts) for later auditing — surfaced via list_chat.data.
    store.save_chat(user, "user", req.message, conv)
    store.save_chat(user, "assistant", result.get("answer", ""), conv,
                    meta=result.get("trace"))
    result["conversationId"] = conv
    return result


@app.get("/api/chat/conversations")
def chat_conversations(user: dict = Depends(current_user)):
    # History is keyed per login, so each caller only ever sees their own threads.
    return get_store().list_conversations(user)


@app.get("/api/chat/history")
def chat_history(conversationId: Optional[str] = None, user: dict = Depends(current_user)):
    return get_store().list_chat(user, conversation_id=conversationId)


@app.delete("/api/chat/history")
def clear_chat_history(conversationId: Optional[str] = None, user: dict = Depends(current_user)):
    """Delete one conversation (conversationId given) or all history (omitted)."""
    return {"ok": True, "deleted": get_store().clear_chat(user, conversation_id=conversationId)}


@app.post("/api/students/seed")
def seed(force: bool = False, user: dict = Depends(current_user)):
    if user.get("type") != "admin":
        raise HTTPException(403, "Admin role required")
    get_store().seed(force=force)
    return {"ok": True, "students": get_store().count()}


@app.post("/api/students/reset")
def reset(user: dict = Depends(current_user)):
    if user.get("type") != "admin":
        raise HTTPException(403, "Admin role required")
    get_store().clear_all_embeddings()
    return {"ok": True, "enrolled": get_store().enrolled_count()}


@app.post("/api/face/pose", response_model=PoseAnalysis)
async def analyze_pose(file: UploadFile = File(...), source: Optional[str] = Form(None),
                       user: dict = Depends(current_user)):
    """Analyze ONE frame for the guided-enrollment loop: the largest face's head
    pose, detector quality, and liveness, plus which pose bucket it currently
    satisfies ('center'|'left'|'right'|'none'). Stateless — the client polls this
    to prompt the user ("turn left") and to decide when to keep a frame. No data
    is stored here."""
    # Detection/liveness are CPU-bound and would block the event loop if run here
    # in the async handler, stalling every other request. Read the bytes on the
    # loop, then run the heavy work in a worker thread. Same for enroll/recognize.
    data = await file.read()
    return await run_in_threadpool(_pose_sync, data)


def _pose_sync(data: bytes) -> PoseAnalysis:
    engine = _engine_or_503()
    img = engine.decode(data)
    h, w = img.shape[:2]
    faces = engine.detect(img)
    if not faces:
        return PoseAnalysis(image_w=w, image_h=h, face_found=False, faces=0,
                            message="No face detected.")
    face = faces[0]  # largest
    pose = engine.head_pose(face)
    quality = engine.quality(face)
    bucket = settings.pose_bucket(pose["yaw"])
    live = _liveness(img, face, settings.enroll_liveness_threshold)
    return PoseAnalysis(
        image_w=w, image_h=h, face_found=True, faces=len(faces),
        bbox=engine.bbox(face), yaw=round(pose["yaw"], 1),
        pitch=round(pose["pitch"], 1), roll=round(pose["roll"], 1),
        quality=quality, pose=bucket,
        live=(live["live"] if live is not None else None),
        liveness_score=(live["score"] if live is not None else None),
        quality_ok=quality >= settings.enroll_min_quality,
    )


@app.post("/api/students/{sid}/enroll", response_model=EnrollMultiResult)
async def enroll(sid: str,
                 files: List[UploadFile] = File(...),
                 poses: List[str] = Form(...),
                 source: Optional[str] = Form(None),
                 user: dict = Depends(current_user)):
    """Guided multi-angle enrollment (replaces single-image upload).

    The client submits the frames it captured across the pose sequence
    (center / left / right) together with each frame's intended pose label. Every
    frame is RE-VALIDATED server-side — the client is never trusted: a face must be
    present, pass the strict enrollment liveness cutoff, meet a minimum quality,
    and its measured yaw must actually match the claimed pose. The accepted set
    must also span a real yaw range (genuine head rotation) — a flat photo or
    screen replay cannot produce correctly-shaped left AND right profiles. Each
    angle is stored as its own embedding for robust recognition."""
    require_staff(user)
    store = get_store()
    student = store.get(sid)
    if not student:
        raise HTTPException(404, f"Unknown student {sid}")
    if len(files) != len(poses):
        raise HTTPException(400, "Mismatched files and poses.")
    if not files:
        raise HTTPException(400, "No frames submitted.")
    blobs = [await up.read() for up in files]
    return await run_in_threadpool(_enroll_sync, sid, student, blobs, list(poses))


def _enroll_sync(sid, student, blobs, poses) -> EnrollMultiResult:
    store = get_store()
    engine = _engine_or_503()

    def fail(msg: str, yaw_span: float = 0.0) -> EnrollMultiResult:
        return EnrollMultiResult(ok=False, sid=sid, name=student["name"],
                                 embVer=settings.model_pack, yaw_span=round(yaw_span, 1),
                                 message=msg)

    captures, yaws = [], []
    for data, want in zip(blobs, poses):
        want = (want or "").strip().lower()
        img = engine.decode(data)
        faces = engine.detect(img)
        if not faces:
            return fail(f"No face detected in the '{want}' capture — retake it.")
        face = faces[0]  # largest
        quality = engine.quality(face)
        if quality < settings.enroll_min_quality:
            return fail(f"'{want}' capture too low quality ({quality}); move closer "
                        "and improve lighting.")
        live = _liveness(img, face, settings.enroll_liveness_threshold)
        if live is not None and not live["live"]:
            return fail("Liveness check failed — enroll from a live face, not a "
                        "photo or screen.")
        pose = engine.head_pose(face)
        bucket = settings.pose_bucket(pose["yaw"])
        if settings.enroll_multi_angle and want in ("center", "left", "right") \
                and bucket != want:
            return fail(f"'{want}' capture didn't match the pose (looked "
                        f"'{bucket}'). Retake it.")
        captures.append({
            "emb": engine.embedding(face), "pose": want or bucket,
            "yaw": pose["yaw"], "quality": quality,
            "thumb": engine.thumbnail(img, face),
            "liveness_score": (live["score"] if live is not None else None),
        })
        yaws.append(pose["yaw"])

    yaw_span = (max(yaws) - min(yaws)) if yaws else 0.0
    if settings.enroll_multi_angle and yaw_span < settings.enroll_yaw_span_min:
        return fail(f"Not enough head rotation (span {yaw_span:.0f}° < "
                    f"{settings.enroll_yaw_span_min:.0f}°). Turn your head further "
                    "left and right.", yaw_span)

    store.set_embeddings(sid, captures, settings.model_pack)
    face = next((c for c in captures if c.get("pose") == "center"), captures[0])
    return EnrollMultiResult(
        ok=True, sid=sid, name=student["name"], embVer=settings.model_pack,
        angles=[{"pose": c["pose"], "yaw": round(c["yaw"], 1),
                 "quality": c["quality"], "liveness_score": c["liveness_score"]}
                for c in captures],
        yaw_span=round(yaw_span, 1), quality=face["quality"], thumb=face["thumb"],
        message=f"Enrolled {len(captures)} angle(s) · yaw span {yaw_span:.0f}°.")


@app.delete("/api/students/{sid}/enroll")
def unenroll(sid: str, user: dict = Depends(current_user)):
    require_staff(user)
    if not get_store().clear_embedding(sid):
        raise HTTPException(404, f"Unknown student {sid}")
    return {"ok": True, "sid": sid}


@app.post("/api/recognize", response_model=RecognizeResult)
async def recognize(file: UploadFile = File(...), threshold: Optional[float] = Form(None),
                    source: Optional[str] = Form(None), user: dict = Depends(current_user)):
    # Any authenticated user (staff kiosk or student self-service) may recognize.
    data = await file.read()
    return await run_in_threadpool(_recognize_sync, data, threshold, source, user)


def _recognize_sync(data: bytes, threshold, source, user) -> RecognizeResult:
    store = get_store()
    engine = _engine_or_503()
    img = engine.decode(data)
    h, w = img.shape[:2]
    thr = settings.match_threshold if threshold is None else float(threshold)
    live_thr = settings.liveness_threshold_for(source)

    # Scope the gallery to the caller: a kiosk only matches its own institute
    # (and a course-limited staff scope, only their courses). Prevents matching —
    # and auto-marking — a student who belongs to another institute or class.
    mat, meta = store.gallery_matrix(scope=user)
    faces_out = []
    for face in engine.detect(img):
        # Liveness first — a matched identity is only trusted if the face is live.
        live = _liveness(img, face, live_thr)
        emb = engine.embedding(face)
        m = eng.best_match_vec(emb, mat, meta, thr, margin=settings.match_margin)
        id_ok = m.get("recognized", False)
        # Combined gate: accept only when identity matches AND liveness passes.
        recognized = id_ok and (live["live"] if live is not None else True)
        reason = m.get("reason")
        if live is not None and id_ok and not live["live"]:
            # Matched a real student but the liveness gate blocked it. Separate a
            # genuine spoof signal from "we simply couldn't verify this frame".
            reason = "spoof_suspected" if live.get("assessed", True) else "liveness_unknown"
        faces_out.append({
            "bbox": engine.bbox(face),
            "quality": engine.quality(face),
            "recognized": recognized,
            "sid": m.get("sid"),
            "name": m.get("name"),
            "cls": m.get("cls"),
            "similarity": m.get("similarity", 0.0),
            "accuracy": m.get("accuracy", 0.0),
            "reason": reason,
            "live": (live["live"] if live is not None else None),
            "liveness_score": (live["score"] if live is not None else None),
        })
    return RecognizeResult(image_w=w, image_h=h, threshold=thr, faces=faces_out)


# ---- attendance ------------------------------------------------------------
@app.post("/api/attendance", response_model=MarkResult)
def mark_attendance(req: MarkRequest, user: dict = Depends(current_user)):
    """Mark a student present (kiosk scan or student self-service).

    Scope-checked like every other student-facing route: staff/admin may mark
    anyone they can see, and a student login may only mark itself — otherwise any
    authenticated student could check in a friend by posting their sid.

    Confined to the configured capture window when one is set up. This is the
    path a student can call directly, so the window is enforced HERE rather than
    in the client. Staff corrections go through PUT /api/attendance, which is
    deliberately never window-gated (see docs/attendance-policy-plan.md)."""
    store = get_store()
    rec = store.get(req.sid)
    if not rec:
        raise HTTPException(404, f"Unknown student {req.sid}")
    if not store.can_view_student(user, rec["raw"]):
        raise HTTPException(403, "Not permitted to mark this student")
    status, err = store.capture_status(rec["raw"].get("InId"), req.session)
    if err == "unknown_session":
        raise HTTPException(400, f"'{req.session}' is not a configured attendance period.")
    if err == "closed":
        raise HTTPException(409, "Attendance is closed for this period.")
    record, created = store.mark_attendance(
        req.sid, session=req.session, source=req.source or "kiosk",
        similarity=req.similarity, date=req.date, status=status)
    if record is None:
        raise HTTPException(404, f"Unknown student {req.sid}")
    if not created:
        msg = "Already marked for this session."
    else:
        msg = "Marked late." if status == "L" else "Marked present."
    return MarkResult(ok=True, created=created, record=record, message=msg)


@app.get("/api/attendance", response_model=list[AttendanceRecord])
def list_attendance(date: Optional[str] = None, cls: Optional[str] = None,
                    session: Optional[str] = None, sid: Optional[str] = None,
                    user: dict = Depends(current_user)):
    return get_store().list_attendance(date=date, cls=cls, session=session, sid=sid, scope=user)


@app.put("/api/attendance")
def set_attendance(req: AttendanceSet, user: dict = Depends(current_user)):
    """Create or edit a student's attendance status for a date/session.
    Staff/admin only; limited to students the caller can see."""
    require_staff(user)
    record, err = get_store().set_attendance(
        req.sid, req.date, req.session, req.status, scope=user)
    if err == "unknown":
        raise HTTPException(404, f"Unknown student {req.sid}")
    if err == "forbidden":
        raise HTTPException(403, "Not permitted to edit this student")
    return {"ok": True, "record": record}


@app.delete("/api/attendance/{record_id}")
def delete_attendance(record_id: str, user: dict = Depends(current_user)):
    require_staff(user)
    if not get_store().delete_attendance(record_id):
        raise HTTPException(404, "Record not found")
    return {"ok": True}


@app.get("/api/attendance/summary", response_model=AttendanceSummary)
def attendance_summary(date: Optional[str] = None, user: dict = Depends(current_user)):
    return get_store().attendance_summary(date=date, scope=user)


@app.get("/api/attendance/roster")
def attendance_roster(date: Optional[str] = None, cls: Optional[str] = None,
                      session: Optional[str] = None, user: dict = Depends(current_user)):
    return get_store().attendance_roster(date=date, cls=cls, session=session, scope=user)


# ---- attendance disputes ("I was present") ---------------------------------
@app.post("/api/attendance/disputes")
def raise_dispute(req: DisputeCreate, user: dict = Depends(current_user)):
    """A student flags an attendance row as wrong ("recognition missed me").
    Logs a review item for staff — it never edits the attendance log itself."""
    if user.get("type") != "student":
        raise HTTPException(403, "Only a student can dispute their own attendance")
    dispute, err = get_store().raise_dispute(user, req.recordId, reason=req.reason)
    if err == "unknown":
        raise HTTPException(404, "Attendance record not found")
    if err == "forbidden":
        raise HTTPException(403, "You can only dispute your own attendance")
    if err == "already_present":
        raise HTTPException(409, "That record is already marked present — nothing to dispute")
    if err == "duplicate":
        raise HTTPException(409, "You already have an open dispute for this record")
    return {"ok": True, "dispute": dispute}


@app.get("/api/attendance/disputes")
def list_disputes(state: Optional[str] = None, user: dict = Depends(current_user)):
    """Disputes visible to the caller: a student sees their own, staff/admin the
    queue for their students. Open items first. Filter with ?state=open."""
    return get_store().list_disputes(user, state=state)


@app.post("/api/attendance/disputes/{dispute_id}/resolve")
def resolve_dispute(dispute_id: str, req: DisputeResolve, user: dict = Depends(current_user)):
    """Staff/admin resolve a dispute. 'approve' corrects the row to Present;
    'reject' leaves it unchanged. This is the only path that edits the log."""
    require_staff(user)
    dispute, err = get_store().resolve_dispute(user, dispute_id, req.action, note=req.note)
    if err == "unknown":
        raise HTTPException(404, "Dispute not found")
    if err == "forbidden":
        raise HTTPException(403, "Not permitted to resolve this dispute")
    if err == "closed":
        raise HTTPException(409, "This dispute is already resolved")
    if err == "bad_action":
        raise HTTPException(400, "action must be 'approve' or 'reject'")
    return {"ok": True, "dispute": dispute}


def _engine_or_503():
    try:
        return eng.get_engine()
    except Exception as e:
        raise HTTPException(503, f"Face model unavailable: {e}")


def _liveness(img, face, threshold: float):
    """Score one face for liveness, honoring the enabled flag + fail-open/closed
    policy. Returns None when anti-spoofing is disabled (so callers skip the gate),
    else {'live': bool, 'score': float, 'assessed': bool}.

    'assessed' is False when the scorer could not judge the face (empty crop at the
    frame edge, or a model error). In that case 'live' reflects the fail-open/closed
    policy, but callers should report it as "couldn't verify" — not as a spoof."""
    if not settings.antispoof_enabled:
        return None
    try:
        res = anti.get_antispoof().score(img, face)
    except Exception as e:  # model missing/broken at runtime → apply policy
        print(f"[antispoof] runtime error: {e}")
        return {"live": not settings.antispoof_fail_closed, "score": 0.0, "assessed": False}
    if res.get("score") is None:            # scorer couldn't assess → policy + 'unknown'
        return {"live": not settings.antispoof_fail_closed, "score": 0.0, "assessed": False}
    return {"live": res["score"] >= threshold, "score": res["score"], "assessed": True}


# ---- static frontend (mounted last so /api/* wins) -------------------------
if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
