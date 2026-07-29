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
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import get_store
from . import engine as eng
from . import antispoof as anti
from . import chat as chatmod
from . import plan as planmod
import base64
from .schemas import (Health, Student, EnrollResult, RecognizeResult,
                      MarkRequest, MarkResult, AttendanceRecord, AttendanceSummary,
                      Institute, LoginRequest, AuthUser, ChatRequest, AttendanceSet)

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
    token = base64.urlsafe_b64encode(f"{user['loginId']}:{user['type']}".encode()).decode()
    return AuthUser(ok=True, token=token, **user)


def current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Decode the session token (base64 'loginId:type'), load the login, and
    return the authorization scope used to filter data. Raises 401 if invalid."""
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    token = authorization.split(" ", 1)[1] if " " in authorization else authorization
    try:
        login_id = base64.urlsafe_b64decode(token.encode()).decode().split(":", 1)[0]
    except Exception:
        raise HTTPException(401, "Invalid session token")
    scope = get_store().user_scope(get_store().get_login(login_id))
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
    """Teacher-facing improvement suggestions grounded in the student's signals.
    Deterministic — never auto-applied to the student."""
    rec = get_store().get(sid)
    if not rec:
        raise HTTPException(404, f"Unknown student {sid}")
    if not get_store().can_view_student(user, rec["raw"]):
        raise HTTPException(403, "Not permitted to view this student")
    return planmod.build_plan(get_store().student_profile(sid))


@app.get("/api/analytics/cohort")
def get_cohort_signals(cls: str, user: dict = Depends(current_user)):
    """Every student in a class label + their at-risk signals (flagged first).
    Scoped to what the caller is allowed to see."""
    return get_store().cohort_signals(cls, scope=user)


@app.post("/api/chat")
def chat(req: ChatRequest, user: dict = Depends(current_user)):
    """Grounded staff chat over attendance + assignments, scoped to the caller.
    Staff/admin only — students use their own dashboard. Persists history."""
    require_staff(user)
    store = get_store()
    conv = req.conversationId or uuid.uuid4().hex
    # Prior turns for this conversation give the model context for follow-ups.
    history = store.list_chat(user, conversation_id=conv)
    result = chatmod.answer(store, req.message, scope=user, context_sid=req.sid,
                            history=history)
    # Persist both turns so the conversation survives page reloads.
    store.save_chat(user, "user", req.message, conv)
    store.save_chat(user, "assistant", result.get("answer", ""), conv)
    result["conversationId"] = conv
    return result


@app.get("/api/chat/conversations")
def chat_conversations(user: dict = Depends(current_user)):
    require_staff(user)
    return get_store().list_conversations(user)


@app.get("/api/chat/history")
def chat_history(conversationId: Optional[str] = None, user: dict = Depends(current_user)):
    require_staff(user)
    return get_store().list_chat(user, conversation_id=conversationId)


@app.delete("/api/chat/history")
def clear_chat_history(conversationId: Optional[str] = None, user: dict = Depends(current_user)):
    """Delete one conversation (conversationId given) or all history (omitted)."""
    require_staff(user)
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


@app.post("/api/students/{sid}/enroll", response_model=EnrollResult)
async def enroll(sid: str, file: UploadFile = File(...), user: dict = Depends(current_user)):
    require_staff(user)
    store = get_store()
    student = store.get(sid)
    if not student:
        raise HTTPException(404, f"Unknown student {sid}")

    engine = _engine_or_503()
    img = engine.decode(await file.read())
    faces = engine.detect(img)
    if not faces:
        return EnrollResult(ok=False, sid=sid, name=student["name"], quality=0.0,
                            faces_found=0, embVer=settings.model_pack,
                            message="No face detected — use a clear, front-facing photo.")
    face = faces[0]  # largest
    # Reject spoofed templates: enrollment uses a stricter liveness cutoff since a
    # template is stored once and quality matters most.
    live = _liveness(img, face, settings.enroll_liveness_threshold)
    if live is not None and not live["live"]:
        return EnrollResult(ok=False, sid=sid, name=student["name"], quality=0.0,
                            faces_found=len(faces), embVer=settings.model_pack,
                            live=False, liveness_score=live["score"],
                            message="Liveness check failed — enroll from a live face, "
                                    "not a photo or screen.")
    emb = engine.embedding(face)
    thumb = engine.thumbnail(img, face)
    quality = engine.quality(face)
    store.set_embedding(sid, emb, quality, thumb, settings.model_pack)
    return EnrollResult(ok=True, sid=sid, name=student["name"], quality=quality,
                        faces_found=len(faces), embVer=settings.model_pack, thumb=thumb,
                        live=(live["live"] if live is not None else None),
                        liveness_score=(live["score"] if live is not None else None),
                        message=f"Enrolled from largest of {len(faces)} face(s).")


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
    store = get_store()
    engine = _engine_or_503()
    img = engine.decode(await file.read())
    h, w = img.shape[:2]
    thr = settings.match_threshold if threshold is None else float(threshold)
    live_thr = settings.liveness_threshold_for(source)

    gallery = store.gallery()
    faces_out = []
    for face in engine.detect(img):
        # Liveness first — a matched identity is only trusted if the face is live.
        live = _liveness(img, face, live_thr)
        emb = engine.embedding(face)
        m = eng.best_match(emb, gallery, thr)
        id_ok = m.get("recognized", False)
        # Combined gate: accept only when identity matches AND liveness passes.
        recognized = id_ok and (live["live"] if live is not None else True)
        reason = m.get("reason")
        if live is not None and id_ok and not live["live"]:
            reason = "spoof_suspected"   # matched a real student, but presented a photo
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
    # Any authenticated user may mark (kiosk or self-service); anonymous is blocked.
    record, created = get_store().mark_attendance(
        req.sid, session=req.session, source=req.source or "kiosk",
        similarity=req.similarity, date=req.date)
    if record is None:
        raise HTTPException(404, f"Unknown student {req.sid}")
    msg = "Marked present." if created else "Already marked for this session."
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


def _engine_or_503():
    try:
        return eng.get_engine()
    except Exception as e:
        raise HTTPException(503, f"Face model unavailable: {e}")


def _liveness(img, face, threshold: float):
    """Score one face for liveness, honoring the enabled flag + fail-open/closed
    policy. Returns None when anti-spoofing is disabled (so callers skip the gate),
    else {'live': bool, 'score': float}."""
    if not settings.antispoof_enabled:
        return None
    try:
        res = anti.get_antispoof().score(img, face)
        return {"live": res["score"] >= threshold, "score": res["score"]}
    except Exception as e:  # model missing/broken at runtime → apply policy
        print(f"[antispoof] runtime error: {e}")
        return {"live": not settings.antispoof_fail_closed, "score": 0.0}


# ---- static frontend (mounted last so /api/* wins) -------------------------
if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
