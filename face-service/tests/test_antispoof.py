"""Functional test for the anti-spoofing module.

Runs with just numpy + opencv (no insightface/mongo needed):

    cd face-service
    python -m pytest tests/test_antispoof.py -s      # or: python tests/test_antispoof.py

Validates cue direction + integration wiring for the classical-CV baseline. It
does NOT assert an absolute accuracy number — the baseline is a scaffold and must
be calibrated on real samples (or replaced by a MiniFASNet ONNX model). It checks
that a live-like face out-scores print/screen proxies and that per-source
thresholds resolve correctly.
"""
import os
import sys

import numpy as np
import cv2

# Make the app package importable when run directly, and force the classical path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTISPOOF_MODEL_PATH", "/nonexistent.onnx")

from app.antispoof import get_antispoof            # noqa: E402
from app.config import settings                    # noqa: E402


class DummyFace:
    """Mimics an InsightFace Face: only .bbox is read by AntiSpoof."""
    def __init__(self, bbox):
        self.bbox = bbox


def _make_real(seed=0):
    rng = np.random.default_rng(seed)
    base = rng.integers(60, 200, size=(220, 220, 3), dtype=np.uint8)
    yy, xx = np.mgrid[0:220, 0:220]
    tone = np.stack([120 + 40 * np.sin(xx / 40), 90 + 30 * np.cos(yy / 50),
                     150 + 20 * np.sin((xx + yy) / 60)], -1)
    return np.clip(0.5 * base + 0.5 * tone, 0, 255).astype(np.uint8)


def _make_print(real):
    hsv = cv2.cvtColor(real, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] *= 0.25
    img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    return cv2.GaussianBlur(img, (7, 7), 3)


def _make_screen(real):
    yy, xx = np.mgrid[0:220, 0:220]
    moire = (20 * np.sin(xx * 1.6) + 20 * np.sin(yy * 1.6))[..., None]
    img = np.clip(real.astype(np.float32) + moire, 0, 255)
    img[20:80, 20:80] = 252
    return img.astype(np.uint8)


def test_liveness_cue_direction():
    a = get_antispoof()
    assert a.backend == "classical"
    face = DummyFace([40, 40, 180, 180])
    real = _make_real()
    s_real = a.score(real, face)["score"]
    s_print = a.score(_make_print(real), face)["score"]
    s_screen = a.score(_make_screen(real), face)["score"]
    assert 0.0 <= s_real <= 1.0
    assert s_real > s_print
    assert s_real > s_screen


def test_per_source_threshold():
    settings.liveness_threshold_kiosk = "0.40"
    settings.liveness_threshold_phone = "0.70"
    assert settings.liveness_threshold_for("kiosk") == 0.40
    assert settings.liveness_threshold_for("phone") == 0.70
    assert settings.liveness_threshold_for(None) == settings.liveness_threshold


if __name__ == "__main__":
    test_liveness_cue_direction()
    test_per_source_threshold()
    print("ALL ASSERTIONS PASSED")
