"""Liveness / anti-spoofing calibration.

The classical baseline (and any MiniFASNet ONNX you drop in) outputs P(live). This
picks the LIVENESS_THRESHOLD that best separates real faces from presentation
attacks on YOUR samples, the same way eval/benchmark.py picks MATCH_THRESHOLD for
recognition. `live = score >= threshold`, so a higher threshold is stricter:
fewer spoofs accepted, but more real faces rejected.

Dataset layout (folders of face images):
    live/    real faces captured at the kiosk / on phones
    spoof/   printed photos, phone/screen replays, masks

Run (from face-service/, inside the venv):
    .venv/bin/python -m eval.antispoof_bench --live ./live --spoof ./spoof --far-target 0.01
    .venv/bin/python -m eval.antispoof_bench --synthetic 400          # no model, validates math

Reports, per threshold: live-accept rate (TAR), spoof-accept rate (the FAR we want
low), the Equal-Error point, and a recommended threshold at a target spoof-accept.
"""
import argparse
import json
import os
import sys
from glob import glob

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


# --------------------------------------------------------------------------- #
# Metrics (pure — unit-testable without images or the model)
# --------------------------------------------------------------------------- #
def sweep_liveness(live, spoof, thresholds):
    """Per threshold: live_accept (real faces passed, want high) and spoof_accept
    (attacks passed, want low). live = score >= threshold."""
    rows = []
    for t in thresholds:
        la = float(np.mean(live >= t)) if live.size else 0.0
        sa = float(np.mean(spoof >= t)) if spoof.size else 0.0
        rows.append({"threshold": round(float(t), 4),
                     "live_accept": la, "spoof_accept": sa,
                     "live_reject": 1.0 - la,
                     "balanced_acc": (la + (1.0 - sa)) / 2.0})
    return rows


def find_eer_liveness(rows):
    """Equal-Error point: where live-reject rate == spoof-accept rate."""
    best = min(rows, key=lambda r: abs(r["live_reject"] - r["spoof_accept"]))
    return best, (best["live_reject"] + best["spoof_accept"]) / 2.0


def threshold_at_spoof_accept(rows, target):
    """Loosest usable: the lowest threshold whose spoof-accept <= target, which
    maximizes live-accept while keeping attacks within budget."""
    ok = [r for r in rows if r["spoof_accept"] <= target]
    return min(ok, key=lambda r: r["threshold"]) if ok else None


# --------------------------------------------------------------------------- #
# Score sources
# --------------------------------------------------------------------------- #
def build_synthetic_scores(n, seed, separation):
    """Fabricate P(live) for live vs spoof sets so the sweep math can be validated
    with no model or images. Real faces score higher; `separation` is the gap
    between the two means."""
    rng = np.random.default_rng(seed)
    live = np.clip(rng.normal(0.70, 0.12, n), 0, 1)
    spoof = np.clip(rng.normal(0.70 - separation, 0.12, n), 0, 1)
    return live, spoof


def _images(folder):
    return sorted(p for p in glob(os.path.join(folder, "*")) if p.lower().endswith(IMG_EXT))


def score_folder(folder):
    """Run every image in a folder through the live anti-spoof scorer, returning
    the array of P(live). Uses the detected face if present, else the whole image."""
    from app import antispoof as A
    from app import engine as eng
    anti = A.get_antispoof()
    engine = eng.get_engine()

    class _Box:
        def __init__(self, bbox):
            self.bbox = bbox

    scores, skipped = [], 0
    for p in _images(folder):
        with open(p, "rb") as f:
            img = engine.decode(f.read())
        faces = engine.detect(img)
        face = faces[0] if faces else _Box([0, 0, img.shape[1], img.shape[0]])
        r = anti.score(img, face)
        if r.get("score") is None:
            skipped += 1
        else:
            scores.append(float(r["score"]))
    if skipped:
        print(f"  {folder}: {skipped} image(s) could not be assessed (skipped)")
    return np.array(scores)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Anti-spoofing threshold calibration")
    ap.add_argument("--live", help="folder of real-face images")
    ap.add_argument("--spoof", help="folder of print/screen/replay images")
    ap.add_argument("--synthetic", type=int, metavar="N",
                    help="fabricate N live + N spoof scores instead of reading images")
    ap.add_argument("--separation", type=float, default=0.25,
                    help="synthetic-only: gap between live and spoof score means")
    ap.add_argument("--far-target", type=float, default=0.01,
                    help="max acceptable spoof-accept rate at the operating point")
    ap.add_argument("--tmin", type=float, default=0.20)
    ap.add_argument("--tmax", type=float, default=0.90)
    ap.add_argument("--tstep", type=float, default=0.01)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="antispoof_report")
    args = ap.parse_args()

    if args.synthetic:
        print(f"== Synthetic anti-spoof benchmark: {args.synthetic} per class "
              f"(separation={args.separation}) ==")
        live, spoof = build_synthetic_scores(args.synthetic, args.seed, args.separation)
        source = f"synthetic(sep={args.separation})"
    else:
        if not (args.live and args.spoof):
            ap.error("provide --live DIR and --spoof DIR (or --synthetic N)")
        from app.antispoof import get_antispoof
        source = get_antispoof().backend
        print(f"== Anti-spoof benchmark · backend {source} ==")
        live, spoof = score_folder(args.live), score_folder(args.spoof)
        if live.size == 0 or spoof.size == 0:
            raise SystemExit("Need scorable images in BOTH --live and --spoof.")

    thresholds = np.arange(args.tmin, args.tmax + 1e-9, args.tstep)
    rows = sweep_liveness(live, spoof, thresholds)
    eer_row, eer = find_eer_liveness(rows)
    op_row = threshold_at_spoof_accept(rows, args.far_target)
    recommended = op_row["threshold"] if op_row else eer_row["threshold"]

    print(f"\nScores:  live  mean {live.mean():.3f} (min {live.min():.3f})   |  "
          f"spoof mean {spoof.mean():.3f} (max {spoof.max():.3f})")
    print(f"Equal Error Rate: {eer*100:.2f}%  at threshold {eer_row['threshold']}")
    if op_row:
        print(f"Operating point @ spoof-accept <= {args.far_target:.3f}:  threshold "
              f"{op_row['threshold']}  ->  live-accept {op_row['live_accept']*100:.1f}%  "
              f"spoof-accept {op_row['spoof_accept']*100:.2f}%")
    else:
        print(f"No threshold reaches spoof-accept <= {args.far_target}; using EER threshold.")

    print("\n threshold | live-acc | spoof-acc")
    print(" ----------+----------+----------")
    for r in rows:
        if abs((r["threshold"] * 100) % 5) < (args.tstep * 100) / 2:
            print(f"   {r['threshold']:.2f}    |  {r['live_accept']*100:5.1f}%  |  "
                  f"{r['spoof_accept']*100:5.1f}%")

    print(f"\n>>> RECOMMENDED LIVENESS_THRESHOLD = {recommended}  "
          f"(raise ENROLL_/*_KIOSK/PHONE variants from here as needed)")

    summary = {"source": source, "live_n": int(live.size), "spoof_n": int(spoof.size),
               "eer": eer, "eer_threshold": eer_row["threshold"],
               "far_target": args.far_target, "recommended_threshold": recommended,
               "operating_point": op_row, "sweep": rows}
    with open(args.out + ".json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nWrote {args.out}.json")


if __name__ == "__main__":
    main()
