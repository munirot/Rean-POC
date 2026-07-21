"""Runtime configuration, all overridable via environment variables (.env)."""
import os


def _get(name: str, default: str) -> str:
    return os.getenv(name, default)


class Settings:
    # MongoDB
    mongo_uri: str = _get("MONGO_URI", "mongodb://admin:mysecurepassword@localhost:27017/")
    print("=====================", mongo_uri)
    db_name: str = _get("FACE_DB_NAME", "rean_face_poc")
    # Real migrated collections (from sample-data). Face embeddings live in their
    # own collection so we never mutate the real student documents.
    students_coll: str = _get("FACE_STUDENTS_COLL", "students")
    embeddings_coll: str = _get("FACE_EMBEDDINGS_COLL", "face_embeddings")
    attendance_coll: str = _get("FACE_ATTENDANCE_COLL", "attendance")
    subjects_coll: str = _get("FACE_SUBJECTS_COLL", "subjects")
    logins_coll: str = _get("FACE_LOGINS_COLL", "logins")
    institutes_coll: str = _get("FACE_INSTITUTES_COLL", "institutes")

    # InsightFace model
    # buffalo_l = accurate (ArcFace r100, 512-d) · buffalo_s = light/fast
    model_pack: str = _get("MODEL_PACK", "buffalo_l")
    det_size: int = int(_get("DET_SIZE", "640"))
    device: str = _get("DEVICE", "cpu").lower()          # "cpu" | "gpu"

    # Matching (cosine similarity on L2-normalized embeddings, range 0..1)
    match_threshold: float = float(_get("MATCH_THRESHOLD", "0.35"))

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
