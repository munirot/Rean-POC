"""InsightFace wrapper: detect faces, extract 512-d embeddings, crop thumbnails.

The model pack (buffalo_l / buffalo_s / ...) is chosen via MODEL_PACK. On first
use InsightFace downloads the weights to ~/.insightface/models (one-time, needs
internet). Nothing is loaded from a browser CDN — inference runs here in Python.
"""
import base64
import numpy as np
import cv2

from .config import settings

_engine = None


class FaceEngine:
    def __init__(self):
        # Imported lazily so the module can be syntax-checked without the heavy dep.
        from insightface.app import FaceAnalysis

        self.model_pack = settings.model_pack
        self.app = FaceAnalysis(name=self.model_pack, providers=settings.providers)
        self.app.prepare(ctx_id=settings.ctx_id, det_size=(settings.det_size, settings.det_size))

    # -- image helpers -------------------------------------------------------
    @staticmethod
    def decode(image_bytes: bytes) -> np.ndarray:
        arr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Could not decode image (unsupported or corrupt file).")
        return img

    def detect(self, img: np.ndarray):
        """Return list of insightface Face objects, largest first."""
        faces = self.app.get(img)
        faces.sort(key=self._area, reverse=True)
        return faces

    @staticmethod
    def _area(face) -> float:
        x1, y1, x2, y2 = face.bbox
        return float((x2 - x1) * (y2 - y1))

    @staticmethod
    def embedding(face) -> np.ndarray:
        """L2-normalized 512-d ArcFace embedding."""
        return np.asarray(face.normed_embedding, dtype=np.float32)

    @staticmethod
    def bbox(face):
        x1, y1, x2, y2 = [float(v) for v in face.bbox]
        return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}

    @staticmethod
    def quality(face) -> float:
        return round(float(getattr(face, "det_score", 0.0)), 4)

    @staticmethod
    def thumbnail(img: np.ndarray, face, size: int = 112) -> str:
        """Base64 JPEG data-URL of the cropped face (for the roster UI)."""
        h, w = img.shape[:2]
        x1, y1, x2, y2 = face.bbox
        mx = (x2 - x1) * 0.25
        my = (y2 - y1) * 0.25
        x1 = max(0, int(x1 - mx)); y1 = max(0, int(y1 - my))
        x2 = min(w, int(x2 + mx)); y2 = min(h, int(y2 + my))
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            crop = img
        crop = cv2.resize(crop, (size, size))
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")


def get_engine() -> "FaceEngine":
    global _engine
    if _engine is None:
        _engine = FaceEngine()
    return _engine


# -- matching ----------------------------------------------------------------
def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two L2-normalized vectors == dot product."""
    return float(np.dot(a, b))


def best_match(emb: np.ndarray, gallery, threshold: float):
    """gallery: list of dicts {sid, name, cls, emb(np.ndarray)}.
    Returns match info for the single best candidate."""
    if not gallery:
        return {"recognized": False, "reason": "no_enrolled_students",
                "similarity": 0.0, "accuracy": 0.0}
    best = None
    for g in gallery:
        sim = cosine(emb, g["emb"])
        if best is None or sim > best["similarity"]:
            best = {"sid": g["sid"], "name": g["name"], "cls": g.get("cls"),
                    "similarity": sim}
    best["recognized"] = best["similarity"] >= threshold
    best["accuracy"] = round(max(0.0, min(1.0, best["similarity"])) * 100.0, 1)
    best["similarity"] = round(best["similarity"], 4)
    return best
