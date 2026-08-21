"""Anti-spoof: unknown-liveness signalling (Part A) + calibration math (Part B).

    cd face-service
    python -m pytest tests/test_antispoof_calibration.py -v
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTISPOOF_MODEL_PATH", "/nonexistent.onnx")  # force classical path

from app.antispoof import get_antispoof                              # noqa: E402
from eval.antispoof_bench import (sweep_liveness, find_eer_liveness,  # noqa: E402
                                  threshold_at_spoof_accept, build_synthetic_scores)


class _Face:
    def __init__(self, bbox):
        self.bbox = bbox


# --- Part A: "couldn't assess" is distinct from "spoof" -------------------- #
def test_empty_crop_reports_unknown_not_spoof():
    a = get_antispoof()
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    face = _Face([100, 100, 110, 110])         # fully outside the image -> empty crop
    r = a.score(img, face)
    assert r["score"] is None                  # not a low live-probability...
    assert r["assessed"] is False              # ...but an explicit "unknown"


def test_real_crop_is_assessed_with_a_score():
    a = get_antispoof()
    img = (np.random.default_rng(0).integers(60, 200, (120, 120, 3))).astype(np.uint8)
    r = a.score(img, _Face([20, 20, 100, 100]))
    assert r["score"] is not None and 0.0 <= r["score"] <= 1.0


# --- Part B: liveness sweep / EER / operating point ------------------------ #
def test_sweep_reports_live_and_spoof_accept():
    live = np.array([0.8, 0.9, 0.7])
    spoof = np.array([0.2, 0.3, 0.1])
    r = sweep_liveness(live, spoof, [0.5])[0]
    assert r["live_accept"] == 1.0 and r["spoof_accept"] == 0.0


def test_operating_point_keeps_spoofs_within_budget():
    live = np.linspace(0.6, 0.9, 50)
    spoof = np.linspace(0.1, 0.6, 50)
    rows = sweep_liveness(live, spoof, np.arange(0.2, 0.9, 0.05))
    op = threshold_at_spoof_accept(rows, 0.05)
    assert op is not None and op["spoof_accept"] <= 0.05
    # loosest such threshold -> nothing lower also meets the budget with more live-accept
    lower = [r for r in rows if r["threshold"] < op["threshold"] and r["spoof_accept"] <= 0.05]
    assert not lower


def test_eer_balances_live_reject_and_spoof_accept():
    live, spoof = build_synthetic_scores(2000, seed=0, separation=0.3)
    rows = sweep_liveness(live, spoof, np.arange(0.2, 0.9, 0.01))
    row, eer = find_eer_liveness(rows)
    assert 0.0 <= eer < 0.5
    assert abs(row["live_reject"] - row["spoof_accept"]) < 0.05


def test_wider_separation_lowers_eer():
    easy = sweep_liveness(*build_synthetic_scores(3000, 0, 0.45), np.arange(0.2, 0.9, 0.01))
    hard = sweep_liveness(*build_synthetic_scores(3000, 0, 0.10), np.arange(0.2, 0.9, 0.01))
    _, eer_easy = find_eer_liveness(easy)
    _, eer_hard = find_eer_liveness(hard)
    assert eer_easy < eer_hard


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
