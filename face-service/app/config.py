"""Runtime configuration, all overridable via environment variables (.env)."""
import os
import secrets
from datetime import datetime, timedelta, timezone


def _get(name: str, default: str) -> str:
    return os.getenv(name, default)


# Fallback signing key, generated once per process when AUTH_SECRET is unset.
# Secure (unguessable) but not shared: sessions die on restart and do not verify
# across uvicorn workers, which is why startup warns when this is in use.
_EPHEMERAL_AUTH_SECRET = secrets.token_urlsafe(32)


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
    # Student-raised "I was present" challenges against attendance rows. Its own
    # collection so a dispute never mutates the attendance log until staff resolve it.
    disputes_coll: str = _get("FACE_DISPUTES_COLL", "attendance_disputes")

    # --- Session tokens -----------------------------------------------------
    # HMAC key used to sign session tokens (see app/auth.py). SET THIS IN
    # PRODUCTION: without it a random per-process key is used, so every restart
    # logs everyone out and tokens don't verify across uvicorn workers.
    auth_secret: str = _get("AUTH_SECRET", "")
    auth_token_ttl_hours: float = float(_get("AUTH_TOKEN_TTL_HOURS", "12"))

    @property
    def auth_key(self) -> bytes:
        return (self.auth_secret or _EPHEMERAL_AUTH_SECRET).encode()

    @property
    def auth_secret_is_ephemeral(self) -> bool:
        """True when no AUTH_SECRET was configured (dev-only fallback in use)."""
        return not self.auth_secret

    # Student-success signal thresholds (all overridable via env)
    attn_low_rate: float = float(_get("SIGNAL_ATTN_LOW", "75"))          # % present floor
    attn_decline_pts: float = float(_get("SIGNAL_ATTN_DECLINE", "15"))   # pt drop recent vs prior
    missing_assign_min: int = int(_get("SIGNAL_MISSING_MIN", "2"))
    quiz_low_avg: float = float(_get("SIGNAL_QUIZ_LOW", "60"))

    # Staff chat (OpenAI-compatible LLM). Default = local Ollama so no student
    # data leaves the machine. Point CHAT_BASE_URL at OpenRouter/Groq/etc. to swap.
    chat_enabled: bool = _get("CHAT_ENABLED", "true").lower() == "true"
    chat_base_url: str = _get("CHAT_BASE_URL", "http://localhost:11434/v1")
    
    # Gemma 4 model
    # Sizes: gemma4:e4b (~9.6GB) · gemma4:12b (~7.6GB) · gemma4:26b · gemma4:31b.
    chat_model: str = _get("CHAT_MODEL", "gemma4:31b-cloud")
    chat_api_key: str = _get("CHAT_API_KEY", "ollama")   # Ollama ignores the value
    chat_temperature: float = float(_get("CHAT_TEMPERATURE", "0.3"))
    chat_timeout: int = int(_get("CHAT_TIMEOUT", "60"))  # seconds per LLM call
    chat_row_cap: int = int(_get("CHAT_ROW_CAP", "200")) # max rows a query may read
    chat_history_coll: str = _get("FACE_CHAT_HISTORY_COLL", "chat_history")
    chat_history_limit: int = int(_get("CHAT_HISTORY_LIMIT", "100"))  # msgs returned
    # Log each chat's NLP→query trace (the LLM's tool choice + the actual DB query
    # it ran) to the server console. Set "false" to silence in production.
    chat_log_queries: bool = _get("CHAT_LOG_QUERIES", "true").lower() == "true"

    # Conversation memory replayed to the model each turn. The history token budget
    # is derived as (model context window − reserved headroom), so it self-adjusts
    # to whatever model CHAT_BASE_URL/CHAT_MODEL points at and can never silently
    # over-run it. Size CHAT_MODEL_CONTEXT to your model's REAL context window —
    # self-hosted runtimes (esp. Ollama) often default to a small window and
    # truncate the oldest tokens silently, so also raise num_ctx /
    # OLLAMA_CONTEXT_LENGTH server-side to match. See docs/chat-model.md.
    chat_model_context: int = int(_get("CHAT_MODEL_CONTEXT", "8192"))    # total tokens
    chat_reserve_tokens: int = int(_get("CHAT_RESERVE_TOKENS", "4000"))  # system+tools+data+reply
    chat_context_msgs: int = int(_get("CHAT_CONTEXT_MSGS", "20"))        # hard turn cap

    @property
    def chat_history_token_budget(self) -> int:
        """Tokens of prior conversation to replay to the model each turn."""
        return max(0, self.chat_model_context - self.chat_reserve_tokens)

    # InsightFace model
    # buffalo_l = accurate (ArcFace r100, 512-d) · buffalo_s = light/fast
    model_pack: str = _get("MODEL_PACK", "buffalo_l")
    det_size: int = int(_get("DET_SIZE", "640"))
    # "cpu" · "gpu"/"cuda" (NVIDIA, Linux) · "coreml"/"mps" (Apple Silicon).
    # On a Mac, "gpu" (CUDA) does nothing — use "coreml" to offload onto the Apple
    # Neural Engine / GPU via onnxruntime's CoreML EP (falls back to CPU if that EP
    # isn't in your onnxruntime build). See docs/gpu-acceleration.md.
    device: str = _get("DEVICE", "cpu").lower()

    # Matching (cosine similarity on L2-normalized embeddings, range 0..1)
    match_threshold: float = float(_get("MATCH_THRESHOLD", "0.35"))

    # How long the in-process face gallery may be trusted before re-checking
    # Mongo for enrollment changes made by ANOTHER process (uvicorn --workers>1).
    # Our own writes invalidate the cache immediately; this bounds how long a
    # sibling worker can serve a stale gallery. 0 = check on every recognition.
    gallery_stamp_ttl_seconds: float = float(_get("GALLERY_STAMP_TTL", "3"))

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

    # --- Guided enrollment (motion-based liveness + multi-angle embeddings) --
    # Enrollment no longer accepts a single uploaded photo. The student is walked
    # through a short sequence of head poses in front of the live camera. Requiring
    # genuine left/right head rotation (with geometrically-correct facial parallax)
    # PLUS the passive anti-spoof score on every frame is our motion/pseudo-3D
    # liveness gate — a flat photo or screen replay cannot present distinct,
    # correctly-shaped left and right profiles. This is not depth-sensor 3D.
    enroll_multi_angle: bool = _get("ENROLL_MULTI_ANGLE", "true").lower() == "true"
    # Ordered pose steps the client guides the user through (first should be frontal
    # so the stored thumbnail is a clean front-facing crop).
    enroll_poses: str = _get("ENROLL_POSES", "center,left,right")
    # Yaw tolerances (degrees). |yaw| <= center_max counts as frontal; a turn must
    # reach turn_min in the requested direction to be accepted for that step.
    enroll_yaw_center_max: float = float(_get("ENROLL_YAW_CENTER_MAX", "12"))
    enroll_yaw_turn_min: float = float(_get("ENROLL_YAW_TURN_MIN", "18"))
    # The accepted captures must span at least this much yaw (max - min) to prove
    # real rotation happened — the cross-frame liveness signal.
    enroll_yaw_span_min: float = float(_get("ENROLL_YAW_SPAN_MIN", "30"))
    # Minimum detector quality (det_score) for a capture to count.
    enroll_min_quality: float = float(_get("ENROLL_MIN_QUALITY", "0.5"))
    # Sign escape hatch: if "turn left"/"turn right" come out reversed on your
    # camera/model, set true to flip the yaw sign convention (no code change).
    enroll_yaw_invert: bool = _get("ENROLL_YAW_INVERT", "false").lower() == "true"

    def enroll_pose_list(self) -> list[str]:
        return [p.strip().lower() for p in self.enroll_poses.split(",") if p.strip()]

    def pose_bucket(self, yaw: float) -> str:
        """Classify a measured yaw (degrees) into 'center' | 'left' | 'right' | 'none'."""
        y = -yaw if self.enroll_yaw_invert else yaw
        if abs(y) <= self.enroll_yaw_center_max:
            return "center"
        if y >= self.enroll_yaw_turn_min:
            return "left"
        if y <= -self.enroll_yaw_turn_min:
            return "right"
        return "none"   # between center and a full turn — "turn a bit more"

    def liveness_threshold_for(self, source: str | None) -> float:
        """Resolve the liveness cutoff for a capture source ('kiosk' | 'phone')."""
        if source == "kiosk" and self.liveness_threshold_kiosk:
            return float(self.liveness_threshold_kiosk)
        if source == "phone" and self.liveness_threshold_phone:
            return float(self.liveness_threshold_phone)
        return self.liveness_threshold

    # Local timezone used for attendance day-bucketing and display. Cambodia is
    # UTC+7 with no daylight saving, so a fixed offset is exact and dependency-free.
    app_tz_offset_hours: float = float(_get("APP_TZ_OFFSET_HOURS", "7"))

    @property
    def tzinfo(self) -> timezone:
        """Fixed-offset tzinfo for the configured local timezone (default +07:00)."""
        return timezone(timedelta(hours=self.app_tz_offset_hours))

    def now_local(self) -> datetime:
        """Timezone-aware 'now' in local (Cambodia) time."""
        return datetime.now(self.tzinfo)

    def today_str(self) -> str:
        """Local calendar day as YYYY-MM-DD — the correct attendance date bucket."""
        return self.now_local().strftime("%Y-%m-%d")

    # API
    cors_origins: str = _get("CORS_ORIGINS", "*")
    host: str = _get("HOST", "0.0.0.0")
    port: int = int(_get("PORT", "8000"))

    @property
    def providers(self):
        # CPU is always kept last as a safe fallback: if the accelerated EP isn't
        # available in the installed onnxruntime, ORT silently uses CPU.
        if self.device in ("gpu", "cuda"):
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if self.device in ("coreml", "mps", "ane"):
            return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    @property
    def ctx_id(self) -> int:
        # InsightFace forces CPU-only when ctx_id < 0, so any accelerated device
        # needs ctx_id >= 0 for the providers above to be honored.
        return -1 if self.device in ("cpu", "") else 0


settings = Settings()
