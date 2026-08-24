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

        # On an accelerated device, make onnxruntime load the CUDA/cuDNN libraries
        # that ship as pip packages (nvidia-*-cuXX). Without this, a plain import
        # doesn't find libcudnn.so and CUDA inference errors at the first Conv
        # instead of running — so DEVICE=gpu "just works" with no LD_LIBRARY_PATH.
        if settings.device in ("gpu", "cuda", "coreml", "mps", "ane"):
            try:
                import onnxruntime as ort
                if hasattr(ort, "preload_dlls"):
                    ort.preload_dlls()
            except Exception as e:      # never let this block CPU/fallback startup
                print(f"[engine] onnxruntime.preload_dlls() skipped: {e}")

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
    def head_pose(face) -> dict:
        """Approximate head pose in degrees: {yaw, pitch, roll, source}.

        Yaw sign convention: positive = subject turned toward their own left.
        Prefers InsightFace's 3D-landmark pose (buffalo_l ships the landmark_3d_68
        model, which sets face.pose = [pitch, yaw, roll]); falls back to a robust
        5-keypoint geometric estimate so lighter packs still work.
        """
        pose = getattr(face, "pose", None)
        if pose is not None and len(pose) == 3:
            return {"pitch": float(pose[0]), "yaw": float(pose[1]),
                    "roll": float(pose[2]), "source": "model"}
        return FaceEngine._pose_from_kps(face)

    @staticmethod
    def _pose_from_kps(face) -> dict:
        """Geometric yaw/roll/pitch from the 5 detector keypoints (eyes, nose,
        mouth corners). An approximation — good enough to gate a head turn."""
        kps = getattr(face, "kps", None)
        if kps is None or len(kps) < 3:
            return {"pitch": 0.0, "yaw": 0.0, "roll": 0.0, "source": "none"}
        le, re_, nose = kps[0], kps[1], kps[2]
        eye_mid_x = (float(le[0]) + float(re_[0])) / 2.0
        eye_mid_y = (float(le[1]) + float(re_[1])) / 2.0
        interocular = float(np.hypot(re_[0] - le[0], re_[1] - le[1])) + 1e-6
        # Nose horizontal offset from the eye midpoint, normalized by interocular
        # distance, mapped to an approximate yaw angle (arctan for a soft, bounded
        # response). ratio ~0 frontal; a strong turn approaches ~±40°.
        ratio = (float(nose[0]) - eye_mid_x) / interocular
        yaw = float(np.degrees(np.arctan(ratio / 0.5)))
        roll = float(np.degrees(np.arctan2(float(re_[1]) - float(le[1]),
                                           float(re_[0]) - float(le[0]))))
        # Pitch proxy: nose sits ~0.6·interocular below the eye line when frontal;
        # deviation from that suggests up/down tilt.
        pitch = float(np.degrees(np.arctan((nose[1] - eye_mid_y) / interocular - 0.6)))
        return {"pitch": pitch, "yaw": yaw, "roll": roll, "source": "kps"}

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


# -- tiled detection ---------------------------------------------------------
# One wall camera puts back-row faces at 20-60px, where a single whole-frame SCRFD
# pass at det_size misses them outright. Splitting the frame into overlapping tiles
# and detecting per tile gives each face far more effective resolution, then the
# duplicates that overlapping tiles inevitably produce are merged by IoU.
def tile_rects(w, h, cols, rows, overlap):
    """Overlapping tiles covering a frame. Overlap keeps a face straddling a seam
    from being clipped in both neighbours and lost in both."""
    cols, rows = max(1, cols), max(1, rows)
    tw, th = w / cols, h / rows
    ox, oy = tw * overlap, th * overlap
    out = []
    for r in range(rows):
        for c in range(cols):
            x1 = max(0, int(c * tw - ox))
            y1 = max(0, int(r * th - oy))
            x2 = min(w, int((c + 1) * tw + ox))
            y2 = min(h, int((r + 1) * th + oy))
            if x2 > x1 and y2 > y1:
                out.append((x1, y1, x2, y2))
    return out


def iou(a, b):
    """Intersection-over-union of two (x1,y1,x2,y2) boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def merge_detections(items, thresh=0.35):
    """NMS over (bbox, score, payload) tuples, highest score first. The same face
    found in two overlapping tiles must not be embedded — or counted — twice."""
    kept = []
    for it in sorted(items, key=lambda t: -t[1]):
        if all(iou(it[0], k[0]) < thresh for k in kept):
            kept.append(it)
    return kept


def detect_tiled(engine, img, tiles=None, overlap=0.15):
    """Faces in one frame as [(bbox_xyxy, det_score, face)].

    `tiles` as (cols, rows) detects per tile and merges; None runs the ordinary
    single whole-frame pass, so callers can A/B the two without branching."""
    h, w = img.shape[:2]
    if not tiles:
        return [(tuple(float(v) for v in f.bbox),
                 float(getattr(f, "det_score", 0.0)), f)
                for f in engine.detect(img)]
    found = []
    for (x1, y1, x2, y2) in tile_rects(w, h, tiles[0], tiles[1], overlap):
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        for f in engine.detect(crop):
            b = [float(v) for v in f.bbox]
            found.append(((b[0] + x1, b[1] + y1, b[2] + x1, b[3] + y1),
                          float(getattr(f, "det_score", 0.0)), f))
    return merge_detections(found)


# -- matching ----------------------------------------------------------------
def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two L2-normalized vectors == dot product."""
    return float(np.dot(a, b))


def best_match(emb: np.ndarray, gallery, threshold: float):
    """gallery: list of dicts {sid, name, cls, emb(np.ndarray)}.
    Returns match info for the single best candidate. Kept for back-compat/tests;
    the recognition path uses the vectorized best_match_vec below."""
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


def best_match_vec(emb: np.ndarray, mat: np.ndarray, meta, threshold: float,
                   margin: float = 0.0):
    """Vectorized version of best_match, with an optional ambiguity guard.

    mat:  (N, D) float32 of L2-normalized gallery embeddings (one row per angle).
    meta: list of {sid, name, cls} aligned to mat's rows.
    Because both sides are L2-normalized, `mat @ emb` gives cosine similarity for
    every enrolled angle in a single BLAS call — O(1) Python, scales to large
    rosters far better than a per-row loop.

    `margin` (>0) rejects near-ties: the top identity must beat the best OTHER
    identity by at least `margin` cosine. Extra enrolled angles of the SAME
    student don't count as competition, so multi-angle enrollment never trips the
    guard. A rejected near-tie returns recognized=False, reason='ambiguous' — we'd
    rather mark nobody than mark a look-alike."""
    if mat is None or mat.shape[0] == 0:
        return {"recognized": False, "reason": "no_enrolled_students",
                "similarity": 0.0, "accuracy": 0.0, "gap": 0.0}
    sims = mat @ emb                       # (N,) cosine similarities
    i = int(np.argmax(sims))
    sim = float(sims[i])
    m = meta[i]
    # Runner-up from a different student (same-sid angles excluded). gap == 1.0
    # (unambiguous) when only one identity is enrolled.
    other = np.array([mm.get("sid") != m["sid"] for mm in meta], dtype=bool)
    runner_up = float(np.max(sims[other])) if other.any() else -1.0
    gap = (sim - runner_up) if runner_up > -1.0 else 1.0
    id_ok = sim >= threshold
    ambiguous = id_ok and margin > 0.0 and gap < margin
    out = {
        "sid": m["sid"], "name": m["name"], "cls": m.get("cls"),
        "recognized": id_ok and not ambiguous,
        "accuracy": round(max(0.0, min(1.0, sim)) * 100.0, 1),
        "similarity": round(sim, 4),
        "gap": round(gap, 4),
    }
    if ambiguous:
        out["reason"] = "ambiguous"
    return out
