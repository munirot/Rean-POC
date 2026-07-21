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


class EnrollResult(BaseModel):
    ok: bool
    sid: str
    name: str
    quality: float
    faces_found: int
    embVer: str
    thumb: Optional[str] = None
    message: Optional[str] = None


class BBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


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
    by_class: List[ClassSummary]
    sessions: List[str]
