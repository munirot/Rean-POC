"""Passive anti-spoofing (presentation-attack detection).

Recognition answers "who is this?". Anti-spoofing answers a separate question:
"is this a live face, or a re-presentation (printed photo / screen replay)?"
ArcFace is deliberately robust to print texture and lighting, so a photo of a
photo embeds almost identically to the real person and passes the identity match.
This module runs BEFORE we trust that match and rejects the spoof.

Two backends, chosen automatically:

  1. ONNX model  — if a weights file exists at settings.antispoof_model_path
     (e.g. MiniFASNet / "Silent-Face-Anti-Spoofing"), it is loaded and used.
     This is the production path; drop the weights in to upgrade with no code change.

  2. Classical-CV baseline — otherwise, a dependency-light detector combines
     several passive cues (frequency-domain moiré, micro-texture richness, chroma
     diversity, specular-highlight uniformity, focus) into a live-probability.
     It makes the pipeline functional out of the box; its weights/threshold should
     be calibrated on real kiosk/phone samples (see docs/face-antispoofing-plan.md).

Both return: {"live": bool, "score": float(0..1), "backend": str, "cues": {...}}
where score is P(live) — higher means more likely a real, present person.
"""
import os
import numpy as np
import cv2

from .config import settings

_antispoof = None


class AntiSpoof:
    def __init__(self):
        self.backend = "classical"
        self.sess = None
        self._input_name = None
        self._input_size = (80, 80)  # MiniFASNet convention; overridden from model
        path = settings.antispoof_model_path
        if path and os.path.isfile(path):
            try:
                import onnxruntime as ort
                self.sess = ort.InferenceSession(path, providers=settings.providers)
                inp = self.sess.get_inputs()[0]
                self._input_name = inp.name
                # NCHW -> (H, W) if the model declares a fixed shape
                shape = inp.shape
                if len(shape) == 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
                    self._input_size = (int(shape[3]), int(shape[2]))  # (W, H)
                self.backend = "onnx"
            except Exception as e:  # pragma: no cover - degrade to classical
                print(f"[antispoof] ONNX load failed ({e}); using classical baseline.")
                self.sess = None

    # -- public API ----------------------------------------------------------
    def score(self, img: np.ndarray, face) -> dict:
        """Return {live, score, backend, cues} for one detected face."""
        crop = self._crop(img, face)
        if crop is None or crop.size == 0:
            # Can't assess — treat per fail-closed policy at the call site by
            # returning a neutral-low score; caller decides.
            return {"live": False, "score": 0.0, "backend": self.backend,
                    "cues": {"error": "empty_crop"}}
        if self.sess is not None:
            return self._score_onnx(crop)
        return self._score_classical(crop)

    # -- crop with margin (spoof cues live at the face border: paper edge,
    #    phone bezel, screen reflection) --------------------------------------
    @staticmethod
    def _crop(img: np.ndarray, face, margin: float = 0.6):
        h, w = img.shape[:2]
        x1, y1, x2, y2 = [float(v) for v in face.bbox]
        bw, bh = (x2 - x1), (y2 - y1)
        mx, my = bw * margin, bh * margin
        X1 = max(0, int(x1 - mx)); Y1 = max(0, int(y1 - my))
        X2 = min(w, int(x2 + mx)); Y2 = min(h, int(y2 + my))
        if X2 <= X1 or Y2 <= Y1:
            return None
        return img[Y1:Y2, X1:X2]

    # -- ONNX backend (MiniFASNet-style) -------------------------------------
    def _score_onnx(self, crop: np.ndarray) -> dict:
        try:
            w, h = self._input_size
            face = cv2.resize(crop, (w, h))
            x = face.astype(np.float32)
            x = (x - 127.5) / 128.0                 # common MiniFASNet normalization
            x = np.transpose(x, (2, 0, 1))[None]    # NCHW
            out = self.sess.run(None, {self._input_name: x})[0][0]
            prob = self._softmax(np.asarray(out, dtype=np.float32))
            # Live probability = 1 - P(spoof). MiniFASNet 3-class packs the two
            # attack classes at indices 0 and 2 and "real" at 1; a 2-class head
            # puts "real" at index 1. Handle both. Override via ANTISPOOF_REAL_INDEX.
            real_idx = int(os.getenv("ANTISPOOF_REAL_INDEX", "1"))
            real_idx = min(real_idx, len(prob) - 1)
            p_live = float(prob[real_idx])
            return {"live": p_live >= settings.liveness_threshold,
                    "score": round(p_live, 4), "backend": "onnx",
                    "cues": {"probs": [round(float(p), 4) for p in prob]}}
        except Exception as e:  # pragma: no cover
            # Runtime inference error → let the caller apply fail-open/closed policy.
            return {"live": not settings.antispoof_fail_closed, "score": 0.0,
                    "backend": "onnx", "cues": {"error": str(e)}}

    @staticmethod
    def _softmax(v: np.ndarray) -> np.ndarray:
        v = v - np.max(v)
        e = np.exp(v)
        return e / (np.sum(e) + 1e-9)

    # -- classical-CV baseline -----------------------------------------------
    def _score_classical(self, crop: np.ndarray) -> dict:
        std = cv2.resize(crop, (128, 128))
        gray = cv2.cvtColor(std, cv2.COLOR_BGR2GRAY)

        texture = self._texture_entropy(gray)      # real skin -> rich micro-texture
        chroma = self._chroma_diversity(std)       # prints -> narrow color gamut
        moire = self._moire_energy(gray)           # screens -> periodic FFT peaks
        specular = self._specular_ratio(std)       # screens -> uniform bright backlight
        focus = self._focus_score(gray)            # defocused print / pixel-grid screen

        # Normalize each cue toward [0,1] using rough operating points. These are
        # baseline priors — recalibrate on real data (see the plan doc).
        tex_n = _clamp((texture - 3.0) / 3.5)          # entropy ~3..6.5
        chroma_n = _clamp((chroma - 12.0) / 30.0)      # sat std ~12..42
        focus_n = _clamp((focus - 40.0) / 260.0)       # laplacian var
        moire_pen = _clamp((moire - 2.2) / 3.5)        # peakiness ratio, spoof high
        spec_pen = _clamp((specular - 0.02) / 0.14)    # bright-pixel fraction, spoof high

        # Weighted logistic: positive cues raise P(live), penalties lower it.
        z = (-0.6
             + 2.2 * tex_n
             + 1.4 * chroma_n
             + 1.0 * focus_n
             - 2.4 * moire_pen
             - 1.6 * spec_pen)
        p_live = float(1.0 / (1.0 + np.exp(-z)))

        return {
            "live": p_live >= settings.liveness_threshold,
            "score": round(p_live, 4),
            "backend": "classical",
            "cues": {
                "texture_entropy": round(float(texture), 3),
                "chroma_std": round(float(chroma), 3),
                "moire_peakiness": round(float(moire), 3),
                "specular_ratio": round(float(specular), 4),
                "focus_var": round(float(focus), 2),
            },
        }

    # -- individual cues ------------------------------------------------------
    @staticmethod
    def _texture_entropy(gray: np.ndarray) -> float:
        """Shannon entropy of a simple 8-neighbour LBP — real skin scores higher."""
        g = gray.astype(np.int16)
        c = g[1:-1, 1:-1]
        code = np.zeros_like(c, dtype=np.uint8)
        neigh = [(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]
        for i, (dy, dx) in enumerate(neigh):
            shifted = g[1 + dy:g.shape[0] - 1 + dy, 1 + dx:g.shape[1] - 1 + dx]
            code |= ((shifted >= c).astype(np.uint8) << i)
        hist = np.bincount(code.ravel(), minlength=256).astype(np.float64)
        p = hist / (hist.sum() + 1e-9)
        p = p[p > 0]
        return float(-(p * np.log2(p)).sum())

    @staticmethod
    def _chroma_diversity(bgr: np.ndarray) -> float:
        """Std of saturation — printed/screen faces have a narrower color spread."""
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        return float(hsv[:, :, 1].std())

    @staticmethod
    def _moire_energy(gray: np.ndarray) -> float:
        """Peakiness of the high-frequency FFT band. Screen replays add periodic
        moiré → a sharp off-center peak, so peak/median runs high."""
        f = np.fft.fftshift(np.fft.fft2(gray.astype(np.float32)))
        mag = np.log1p(np.abs(f))
        h, w = mag.shape
        cy, cx = h // 2, w // 2
        yy, xx = np.ogrid[:h, :w]
        r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        band = (r > 12) & (r < min(cy, cx))   # exclude DC / low-freq structure
        vals = mag[band]
        if vals.size == 0:
            return 0.0
        med = np.median(vals) + 1e-6
        return float((vals.max() - med) / med)

    @staticmethod
    def _specular_ratio(bgr: np.ndarray) -> float:
        """Fraction of near-saturated bright pixels — a screen's uniform backlight
        blows out highlights more than diffuse skin under room light."""
        v = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[:, :, 2]
        return float((v > 245).mean())

    @staticmethod
    def _focus_score(gray: np.ndarray) -> float:
        """Variance of the Laplacian (focus/detail). Defocused prints score low."""
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _clamp(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def get_antispoof() -> "AntiSpoof":
    global _antispoof
    if _antispoof is None:
        _antispoof = AntiSpoof()
    return _antispoof
