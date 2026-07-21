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
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import get_store
from . import engine as eng
import base64
from .schemas import (Health, Student, EnrollResult, RecognizeResult,
                      MarkRequest, MarkResult, AttendanceRecord, AttendanceSummary,
                      Institute, LoginRequest, AuthUser)

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


@app.get("/api/students", response_model=list[Student])
def list_students():
    return get_store().list_students()


@app.get("/api/students/{sid}/profile")
def get_student_profile(sid: str):
    s = get_store().get_student_full(sid)
    if not s:
        raise HTTPException(404, f"Unknown student {sid}")
    return s


@app.get("/api/students/{sid}/stats")
def get_student_stats(sid: str):
    if not get_store().get(sid):
        raise HTTPException(404, f"Unknown student {sid}")
    return get_store().student_stats(sid)


@app.post("/api/students/seed")
def seed(force: bool = False):
    get_store().seed(force=force)
    return {"ok": True, "students": get_store().count()}


@app.post("/api/students/reset")
def reset():
    get_store().clear_all_embeddings()
    return {"ok": True, "enrolled": get_store().enrolled_count()}


@app.post("/api/students/{sid}/enroll", response_model=EnrollResult)
async def enroll(sid: str, file: UploadFile = File(...)):
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
    emb = engine.embedding(face)
    thumb = engine.thumbnail(img, face)
    quality = engine.quality(face)
    store.set_embedding(sid, emb, quality, thumb, settings.model_pack)
    return EnrollResult(ok=True, sid=sid, name=student["name"], quality=quality,
                        faces_found=len(faces), embVer=settings.model_pack, thumb=thumb,
                        message=f"Enrolled from largest of {len(faces)} face(s).")


@app.delete("/api/students/{sid}/enroll")
def unenroll(sid: str):
    if not get_store().clear_embedding(sid):
        raise HTTPException(404, f"Unknown student {sid}")
    return {"ok": True, "sid": sid}


@app.post("/api/recognize", response_model=RecognizeResult)
async def recognize(file: UploadFile = File(...), threshold: Optional[float] = Form(None)):
    store = get_store()
    engine = _engine_or_503()
    img = engine.decode(await file.read())
    h, w = img.shape[:2]
    thr = settings.match_threshold if threshold is None else float(threshold)

    gallery = store.gallery()
    faces_out = []
    for face in engine.detect(img):
        emb = engine.embedding(face)
        m = eng.best_match(emb, gallery, thr)
        faces_out.append({
            "bbox": engine.bbox(face),
            "quality": engine.quality(face),
            "recognized": m.get("recognized", False),
            "sid": m.get("sid"),
            "name": m.get("name"),
            "cls": m.get("cls"),
            "similarity": m.get("similarity", 0.0),
            "accuracy": m.get("accuracy", 0.0),
            "reason": m.get("reason"),
        })
    return RecognizeResult(image_w=w, image_h=h, threshold=thr, faces=faces_out)


# ---- attendance ------------------------------------------------------------
@app.post("/api/attendance", response_model=MarkResult)
def mark_attendance(req: MarkRequest):
    record, created = get_store().mark_attendance(
        req.sid, session=req.session, source=req.source or "kiosk",
        similarity=req.similarity, date=req.date)
    if record is None:
        raise HTTPException(404, f"Unknown student {req.sid}")
    msg = "Marked present." if created else "Already marked for this session."
    return MarkResult(ok=True, created=created, record=record, message=msg)


@app.get("/api/attendance", response_model=list[AttendanceRecord])
def list_attendance(date: Optional[str] = None, cls: Optional[str] = None,
                    session: Optional[str] = None, sid: Optional[str] = None):
    return get_store().list_attendance(date=date, cls=cls, session=session, sid=sid)


@app.delete("/api/attendance/{record_id}")
def delete_attendance(record_id: str):
    if not get_store().delete_attendance(record_id):
        raise HTTPException(404, "Record not found")
    return {"ok": True}


@app.get("/api/attendance/summary", response_model=AttendanceSummary)
def attendance_summary(date: Optional[str] = None):
    return get_store().attendance_summary(date=date)


def _engine_or_503():
    try:
        return eng.get_engine()
    except Exception as e:
        raise HTTPException(503, f"Face model unavailable: {e}")


# ---- static frontend (mounted last so /api/* wins) -------------------------
if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
