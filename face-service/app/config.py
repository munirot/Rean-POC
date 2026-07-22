"""Runtime configuration, all overridable via environment variables (.env)."""
import os


def _get(name: str, default: str) -> str:
    return os.getenv(name, default)


class Settings:
    # MongoDB
    mongo_uri: str = _get("MONGO_URI", "mongodb://admin:mysecurepassword@localhost:27017/")
    db_name: str = _get("FACE_DB_NAME", "rean_face_poc")
    # Real migrated collections (from sample-data). Face embeddings live in their
    # own collection so we never mutate the real student documents.
    students_coll: str = _get("FACE_STUDENTS_COLL", "students")
    embeddings_coll: str = _get("FACE_EMBEDDINGS_COLL", "face_embeddings")
    attendance_coll: str = _get("FACE_ATTENDANCE_COLL", "attendance")
    subjects_coll: str = _get("FACE_SUBJECTS_COLL", "subjects")
    logins_coll: str = _get("FACE_LOGINS_COLL", "logins")
    institutes_coll: str = _get("FACE_INSTITUTES_COLL", "institutes")
    assignments_coll: str = _get("FACE_ASSIGNMENTS_COLL", "assignments")
    staffs_coll: str = _get("FACE_STAFFS_COLL", "staffs")

    # Student-success signal thresholds (all overridable via env)
    attn_low_rate: float = float(_get("SIGNAL_ATTN_LOW", "75"))          # % present floor
    attn_decline_pts: float = float(_get("SIGNAL_ATTN_DECLINE", "15"))   # pt drop recent vs prior
    missing_assign_min: int = int(_get("SIGNAL_MISSING_MIN", "2"))
    quiz_low_avg: float = float(_get("SIGNAL_QUIZ_LOW", "60"))

    # Staff chat (OpenAI-compatible LLM). Default = local Ollama so no student
    # data leaves the machine. Point CHAT_BASE_URL at OpenRouter/Groq/etc. to swap.
    chat_enabled: bool = _get("CHAT_ENABLED", "true").lower() == "true"
    chat_base_url: str = _get("CHAT_BASE_URL", "http://localhost:11434/v1")
    chat_model: str = _get("CHAT_MODEL", "qwen2.5:7b-instruct")
    chat_api_key: str = _get("CHAT_API_KEY", "ollama")   # Ollama ignores the value
    chat_timeout: int = int(_get("CHAT_TIMEOUT", "60"))  # seconds per LLM call
    chat_row_cap: int = int(_get("CHAT_ROW_CAP", "200")) # max rows a query may read

    # InsightFace model
    # buffalo_l = accurate (ArcFace r100, 512-d) · buffalo_s = light/fast
    model_pack: str = _get("MODEL_PACK", "buffalo_l")
    det_size: int = int(_get("DET_SIZE", "640"))
    device: str = _get("DEVICE", "cpu").lower()          # "cpu" | "gpu"

    # Matching (cosine similarity on L2-normalized embeddings, range 0..1)
    match_threshold: float = float(_get("MATCH_THRESHOLD", "0.35"))

    # Anti-spoofing / liveness (presentation-attack detection)
    # Master switch. Set to "false" for an instant rollback to pure recognition.
    antispoof_enabled: bool = _get("ANTISPOOF_ENABLED", "true").lower() == "true"
    # Optional ONNX model (e.g. MiniFASNet / Silent-Face). If the file exists it is
    # used; otherwise the service falls back to the built-in classical-CV detector.
    antispoof_model_path: str = _get("ANTISPOOF_MODEL_PATH", "models/antispoof.onnx")
    # Liveness probability cutoff (0..1, higher = stricter). Recognition path.
    liveness_threshold: float = float(_get("LIVENESS_THRESHOLD", "0.55"))
    # Enrollment runs stricter — a template is stored once and must be a live face.
    enroll_liveness_threshold: float = float(_get("ENROLL_LIVENESS_THRESHOLD", "0.65"))
    # Per-capture-source overrides. Supervised kiosk can run looser (fewer false
    # rejects); unsupervised phone should run tighter. Blank = use liveness_threshold.
    liveness_threshold_kiosk: str = _get("LIVENESS_THRESHOLD_KIOSK", "")
    liveness_threshold_phone: str = _get("LIVENESS_THRESHOLD_PHONE", "")
    # Fail-closed: if the anti-spoof model errors at runtime, treat as NOT live
    # (refuse, fall back to manual). Set "false" to fail-open (availability first).
    antispoof_fail_closed: bool = _get("ANTISPOOF_FAIL_CLOSED", "true").lower() == "true"

    def liveness_threshold_for(self, source: str | None) -> float:
        """Resolve the liveness cutoff for a capture source ('kiosk' | 'phone')."""
        if source == "kiosk" and self.liveness_threshold_kiosk:
            return float(self.liveness_threshold_kiosk)
        if source == "phone" and self.liveness_threshold_phone:
            return float(self.liveness_threshold_phone)
        return self.liveness_threshold

    # API
    cors_origins: str = _get("CORS_ORIGINS", "*")
    host: str = _get("HOST", "0.0.0.0")
    port: int = int(_get("PORT", "8000"))

    @property
    def providers(self):
        if self.device == "gpu":
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    @property
    def ctx_id(self) -> int:
        return 0 if self.device == "gpu" else -1


settings = Settings()
