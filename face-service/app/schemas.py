"""Pydantic response models for the API."""
from typing import Optional, List
from pydantic import BaseModel


class Health(BaseModel):
    status: str
    model_pack: str
    device: str
    det_size: int
    match_threshold: float
    students: int
    enrolled: int
    mongo: str


class Student(BaseModel):
    sid: str
    name: str
    cls: Optional[str] = None
    expected: bool = False
    enrolled: bool = False
    quality: Optional[float] = None
    embVer: Optional[str] = None
    thumb: Optional[str] = None


class BBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class PoseAnalysis(BaseModel):
    """Per-frame feedback for the guided enrollment loop (POST /api/face/pose)."""
    image_w: int
    image_h: int
    face_found: bool
    faces: int = 0
    bbox: Optional[BBox] = None
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    roll: Optional[float] = None
    quality: Optional[float] = None
    pose: Optional[str] = None          # 'center' | 'left' | 'right' | 'none'
    live: Optional[bool] = None
    liveness_score: Optional[float] = None
    quality_ok: bool = False
    message: Optional[str] = None


class EnrollAngle(BaseModel):
    pose: Optional[str] = None
    yaw: float = 0.0
    quality: Optional[float] = None
    liveness_score: Optional[float] = None


class EnrollMultiResult(BaseModel):
    ok: bool
    sid: str
    name: str
    embVer: str
    angles: List[EnrollAngle] = []
    yaw_span: float = 0.0
    quality: float = 0.0                # best/frontal quality (roster)
    thumb: Optional[str] = None
    message: Optional[str] = None


class FaceMatch(BaseModel):
    bbox: BBox
    quality: float
    recognized: bool
    sid: Optional[str] = None
    name: Optional[str] = None
    cls: Optional[str] = None
    similarity: float = 0.0
    accuracy: float = 0.0
    reason: Optional[str] = None
    # Liveness / anti-spoofing (null when anti-spoofing is disabled)
    live: Optional[bool] = None
    liveness_score: Optional[float] = None


class RecognizeResult(BaseModel):
    image_w: int
    image_h: int
    threshold: float
    faces: List[FaceMatch]


class Institute(BaseModel):
    InId: str
    InNa: str
    InCd: Optional[str] = None
    City: Optional[str] = None
    Country: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUser(BaseModel):
    ok: bool
    token: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    type: Optional[str] = None
    InId: Optional[str] = None
    loginId: Optional[str] = None
    sid: Optional[str] = None
    staffId: Optional[str] = None


class MarkRequest(BaseModel):
    sid: str
    session: Optional[str] = "Default"
    source: Optional[str] = "kiosk"
    similarity: Optional[float] = None
    date: Optional[str] = None


class AttendanceRecord(BaseModel):
    id: str
    sid: str
    name: Optional[str] = None
    cls: Optional[str] = None
    subNa: Optional[str] = None
    date: str
    session: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    similarity: Optional[float] = None
    ts: Optional[str] = None


class MarkResult(BaseModel):
    ok: bool
    created: bool
    record: Optional[AttendanceRecord] = None
    message: Optional[str] = None


class ClassSummary(BaseModel):
    cls: str
    total: int
    present: int


class AttendanceSummary(BaseModel):
    date: str
    total_students: int
    enrolled: int
    present: int
    absent: int
    marked: int = 0
    by_class: List[ClassSummary]
    sessions: List[str]


class AttendanceSet(BaseModel):
    sid: str
    status: str                       # "P" | "L" | "A"
    date: Optional[str] = None        # YYYY-MM-DD (defaults to today)
    session: Optional[str] = None     # defaults to "Morning"


class DisputeCreate(BaseModel):
    """A student challenging one of their own attendance rows ("I was present")."""
    recordId: str                     # the attendance row being disputed
    reason: Optional[str] = None      # optional free-text note (capped server-side)


class DisputeResolve(BaseModel):
    """Staff/admin decision on a dispute."""
    action: str                       # "approve" (correct to Present) | "reject"
    note: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    sid: Optional[str] = None             # student currently on screen, for context
    conversationId: Optional[str] = None  # thread to append to (new one if omitted)
