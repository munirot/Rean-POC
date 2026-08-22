"""Whole-class camera: Phase 0 coverage measurement (the gate).

This is the check that decides whether `docs/class-camera-attendance-plan.md` gets
built at all. It is a MEASUREMENT, not product code: it writes nothing, marks no
attendance, and touches no collection.

The question it answers is not "how good is the model on one frame" — a frame
where all 30 faces are cleanly visible may never exist. It is:

    over a seating window, what fraction of the students who were actually
    present does the camera confirm, and does it ever confirm the wrong one?

So it replays frames in order, accumulates per-student evidence exactly the way
the plan proposes (a student is CONFIRMED once they clear threshold+margin in
`--confirm-hits` separate frames), and reports coverage at several window lengths.

Inputs
------
  --frames DIR    full-resolution stills from one class, in chronological order
                  (sorted by filename). Do NOT feed these through the 640px live
                  path — recognition quality tracks original face pixels.
  --truth FILE    ground truth: the students who were REALLY in the room, one per
                  line, taken manually. An optional second column groups them for
                  the by-distance breakdown:
                      2301
                      2302,front
                      2317,back

Run (from face-service/, inside the venv)
-----------------------------------------
    .venv/bin/python -m eval.class_coverage --frames ./class_frames --truth ./truth.csv \
        --interval 4 --course CR003 --compare-tiling
    .venv/bin/python -m eval.class_coverage --synthetic 30      # no camera, no model

Everything below the tiling helpers is pure and unit-tested in
tests/test_class_coverage.py, so the verdict can be trusted before the first real
classroom frame exists.
"""
import argparse
import json
import os
import sys
from glob import glob

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
CHECKPOINTS_MIN = (1, 3, 5, 10)


# --------------------------------------------------------------------------- #
# Tiled detection — the mitigation for small, distant faces (plan §6.1).
# It now lives in the product engine (app/engine.py) because the ingest path uses
# it too; re-exported here so this script and its tests keep one implementation.
# --------------------------------------------------------------------------- #
from app.engine import (tile_rects, iou, merge_detections,   # noqa: E402,F401
                        detect_tiled as detect_faces)


# --------------------------------------------------------------------------- #
# Evidence accumulation + metrics (pure — no model, no Mongo, no images)
# --------------------------------------------------------------------------- #
def confirmed_by(observations, min_hits, upto_frame=None):
    """Students confirmed present: recognized in >= min_hits distinct frames.

    observations: iterable of {frame, sid, similarity, height}. Multiple hits for
    one student WITHIN a frame count once — otherwise a duplicate detection could
    confirm someone on its own."""
    per_frame = {}
    for o in observations:
        if upto_frame is not None and o["frame"] > upto_frame:
            continue
        per_frame.setdefault(o["sid"], set()).add(o["frame"])
    return {sid for sid, frames in per_frame.items() if len(frames) >= min_hits}


def coverage_report(observations, present, min_hits, upto_frame=None):
    """Coverage (of students really there) and the false marks that matter more."""
    present = set(present)
    conf = confirmed_by(observations, min_hits, upto_frame)
    hit = conf & present
    return {
        "confirmed": len(conf),
        "covered": len(hit),
        "present": len(present),
        "coverage": (len(hit) / len(present)) if present else 0.0,
        "missed": sorted(present - conf),
        "false_marks": sorted(conf - present),   # confirmed but NOT in the room
    }


def frames_for_minutes(minutes, interval_s):
    """Last frame index (0-based) inside a window of `minutes`."""
    if interval_s <= 0:
        return 0
    return max(0, int(round(minutes * 60.0 / interval_s)) - 1)


def coverage_curve(observations, present, min_hits, interval_s,
                   checkpoints=CHECKPOINTS_MIN):
    """Coverage at each window length — this is what tells you how long the camera
    must watch, which is the single most actionable number in the report."""
    return [{"minutes": m,
             **coverage_report(observations, present, min_hits,
                               frames_for_minutes(m, interval_s))}
            for m in checkpoints]


def coverage_by_group(observations, truth, min_hits, upto_frame=None):
    """Coverage split by the truth file's group column (front/middle/back...).
    Separates 'the camera can't see the back row' (placement) from 'the model is
    weak everywhere' (model) — a distinction that changes what you fix."""
    groups = {}
    for sid, g in truth.items():
        groups.setdefault(g or "ungrouped", []).append(sid)
    conf = confirmed_by(observations, min_hits, upto_frame)
    out = []
    for g, sids in sorted(groups.items()):
        covered = sum(1 for s in sids if s in conf)
        out.append({"group": g, "present": len(sids), "covered": covered,
                    "coverage": covered / len(sids) if sids else 0.0})
    return out


def face_size_summary(observations):
    """Observed face heights in pixels — the physical read on whether the camera is
    close/high-res enough. ArcFace aligns to 112px, so faces far below that are
    being upsampled from mush."""
    hs = [o["height"] for o in observations if o.get("height")]
    if not hs:
        return None
    a = np.array(hs, dtype=np.float32)
    return {"n": int(a.size), "min": float(a.min()), "p10": float(np.percentile(a, 10)),
            "median": float(np.median(a)), "max": float(a.max())}


def verdict(coverage, false_marks):
    """The plan's success / kill criteria, applied. Wrong marks dominate: silent
    attendance corruption is worse than no automation at all."""
    if false_marks:
        return ("BLOCKED", "Wrong-student marks occurred. Raise MATCH_MARGIN or "
                           "--confirm-hits and re-measure before anything else.")
    if coverage >= 0.90:
        return ("BUILD", "Coverage >= 90% with no wrong marks — the exception list "
                         "is short enough that the teacher's job stays trivial.")
    if coverage >= 0.70:
        return ("ITERATE", "Coverage 70-90%: real savings but a meaningful review "
                           "burden. Improve camera placement, resolution or tiling, "
                           "then re-measure.")
    return ("STOP", "Coverage < 70%: the teacher would hand-confirm a third of the "
                    "class — no better than marking manually.")


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def load_truth(path):
    """{sid: group}. One student per line; optional ',group' second column."""
    truth = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            truth[parts[0]] = parts[1] if len(parts) > 1 and parts[1] else None
    if not truth:
        raise SystemExit(f"No students listed in {path}")
    return truth


def frame_paths(folder):
    paths = sorted(p for p in glob(os.path.join(folder, "*"))
                   if p.lower().endswith(IMG_EXT))
    if not paths:
        raise SystemExit(f"No images found in {folder}")
    return paths


# --------------------------------------------------------------------------- #
# Real run
# --------------------------------------------------------------------------- #
def observe_frames(paths, scope, tiles, overlap, threshold, margin, quiet=False):
    """Replay frames through detect -> embed -> match, returning observations."""
    from app import engine as eng
    from app.db import get_store

    engine = eng.get_engine()
    store = get_store()
    mat, meta = store.gallery_matrix(scope=scope)
    if mat.shape[0] == 0:
        raise SystemExit("The scoped gallery is empty — enroll this class first.")
    if not quiet:
        print(f"Gallery: {mat.shape[0]} vectors "
              f"({len({m['sid'] for m in meta})} students in scope)")

    obs = []
    for i, p in enumerate(paths):
        with open(p, "rb") as fh:
            img = engine.decode(fh.read())
        for bbox, _score, face in detect_faces(engine, img, tiles, overlap):
            emb = engine.embedding(face)
            m = eng.best_match_vec(emb, mat, meta, threshold, margin=margin)
            if m.get("recognized") and m.get("sid"):
                obs.append({"frame": i, "sid": m["sid"],
                            "similarity": m.get("similarity", 0.0),
                            "height": bbox[3] - bbox[1]})
        if not quiet and (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(paths)} frames · {len(obs)} recognitions so far")
    return obs


# --------------------------------------------------------------------------- #
# Synthetic mode — validates the accumulation/metric math with no camera
# --------------------------------------------------------------------------- #
def synth_observations(n_students, frames, seed=0, absent_frac=0.1,
                       wrong_rate=0.0):
    """Fabricate observations for a class. Visibility falls off by row, which is
    the real-world failure mode: back-row faces are small and often occluded."""
    rng = np.random.default_rng(seed)
    sids = [f"S{i:03d}" for i in range(n_students)]
    rng.shuffle(sids)
    n_absent = int(round(n_students * absent_frac))
    absent = set(sids[:n_absent])
    present = [s for s in sids if s not in absent]

    rows = ("front", "middle", "back")
    p_visible = {"front": 0.55, "middle": 0.35, "back": 0.15}
    height = {"front": 90.0, "middle": 55.0, "back": 32.0}
    truth = {}
    for k, sid in enumerate(present):
        truth[sid] = rows[k * len(rows) // max(1, len(present))]

    obs = []
    for f in range(frames):
        for sid, row in truth.items():
            if rng.random() < p_visible[row]:
                obs.append({"frame": f, "sid": sid,
                            "similarity": float(rng.uniform(0.45, 0.8)),
                            "height": height[row] * float(rng.uniform(0.85, 1.15))})
        # occasionally mis-identify someone who isn't even in the room
        if wrong_rate and absent and rng.random() < wrong_rate:
            obs.append({"frame": f, "sid": sorted(absent)[0],
                        "similarity": 0.4, "height": 40.0})
    return obs, truth


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def print_report(obs, truth, interval, min_hits, label=""):
    present = set(truth)
    print(f"\n=== Coverage over the window {label}".rstrip() + " ===")
    print(f"{'window':>8} {'covered':>9} {'coverage':>9} {'wrong marks':>12}")
    print("-" * 42)
    for row in coverage_curve(obs, present, min_hits, interval):
        print(f"{row['minutes']:>6}min {row['covered']:>4}/{row['present']:<4} "
              f"{row['coverage'] * 100:>8.1f}% {len(row['false_marks']):>12}")

    final = coverage_report(obs, present, min_hits)
    by_group = coverage_by_group(obs, truth, min_hits)
    if len(by_group) > 1 or by_group[0]["group"] != "ungrouped":
        print("\n=== Coverage by group (placement vs model) ===")
        for g in by_group:
            print(f"  {g['group']:<10} {g['covered']:>3}/{g['present']:<3} "
                  f"{g['coverage'] * 100:>6.1f}%")

    sizes = face_size_summary(obs)
    if sizes:
        print(f"\nObserved face heights (px): median {sizes['median']:.0f} · "
              f"p10 {sizes['p10']:.0f} · min {sizes['min']:.0f}")
        if sizes["p10"] < 60:
            print("  ! A tenth of detections are under 60px — ArcFace aligns to 112px, "
                  "so these are upsampled. Move the camera closer or raise resolution.")

    print("\n=== Sensitivity to --confirm-hits ===")
    for k in (1, 2, 3, 5):
        r = coverage_report(obs, present, k)
        print(f"  {k} hit(s): coverage {r['coverage'] * 100:5.1f}%  "
              f"wrong marks {len(r['false_marks'])}")

    if final["missed"]:
        shown = ", ".join(final["missed"][:10])
        print(f"\nNever confirmed ({len(final['missed'])}): {shown}"
              + (" …" if len(final["missed"]) > 10 else ""))
    if final["false_marks"]:
        print(f"WRONG MARKS ({len(final['false_marks'])}): "
              f"{', '.join(final['false_marks'][:10])}")

    tag, why = verdict(final["coverage"], final["false_marks"])
    print(f"\n>>> VERDICT: {tag} — {why}")
    return final


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Whole-class camera coverage (Phase 0)")
    ap.add_argument("--frames", help="directory of chronological full-res stills")
    ap.add_argument("--truth", help="ground-truth file: sid[,group] per line")
    ap.add_argument("--synthetic", type=int, metavar="N",
                    help="simulate an N-student class instead of reading images")
    ap.add_argument("--interval", type=float, default=4.0,
                    help="seconds between frames (default 4)")
    ap.add_argument("--confirm-hits", type=int, default=3,
                    help="frames a student must clear to be auto-marked (default 3)")
    ap.add_argument("--tiles", default="3x2",
                    help="tiled-detection grid COLSxROWS, or 'off' (default 3x2)")
    ap.add_argument("--overlap", type=float, default=0.15)
    ap.add_argument("--compare-tiling", action="store_true",
                    help="run with and without tiling and compare coverage")
    ap.add_argument("--institute", help="InId to scope the gallery to")
    ap.add_argument("--course", help="CrID to scope the gallery to (the section's course)")
    ap.add_argument("--threshold", type=float, default=None,
                    help="override MATCH_THRESHOLD")
    ap.add_argument("--margin", type=float, default=None, help="override MATCH_MARGIN")
    ap.add_argument("--wrong-rate", type=float, default=0.0,
                    help="synthetic-only: chance per frame of a wrong-student match")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="class_coverage_report")
    args = ap.parse_args()

    def parse_tiles(v):
        if not v or str(v).lower() in ("off", "none", "0"):
            return None
        try:
            c, r = str(v).lower().split("x")
            c, r = int(c), int(r)
        except Exception:
            raise SystemExit("--tiles must look like 3x2, or 'off'")
        if c < 1 or r < 1:
            raise SystemExit("--tiles grid must be at least 1x1")
        return c, r

    # Validate up front so a typo is caught in either mode, not silently ignored
    # on the path that happens not to use it.
    tiles_cfg = parse_tiles(args.tiles)

    if args.synthetic:
        frames = max(1, int(round(max(CHECKPOINTS_MIN) * 60 / args.interval)))
        print(f"== Synthetic class: {args.synthetic} students, {frames} frames "
              f"@ {args.interval}s ==")
        obs, truth = synth_observations(args.synthetic, frames, seed=args.seed,
                                        wrong_rate=args.wrong_rate)
        runs = [("synthetic", obs)]
        model_info = "synthetic"
    else:
        if not (args.frames and args.truth):
            ap.error("provide --frames DIR and --truth FILE (or --synthetic N)")
        from app.config import settings
        truth = load_truth(args.truth)
        paths = frame_paths(args.frames)
        thr = settings.match_threshold if args.threshold is None else args.threshold
        margin = settings.match_margin if args.margin is None else args.margin
        inid = args.institute
        if not inid:
            from app.db import get_store
            s = get_store().students.find_one({}, {"_id": 0, "InId": 1}) or {}
            inid = s.get("InId")
        scope = {"InId": inid, "type": "staff" if args.course else "admin",
                 "courses": {args.course} if args.course else None}
        model_info = f"{settings.model_pack}/{settings.device} thr={thr} margin={margin}"
        print(f"== Class coverage · {len(paths)} frames · {model_info} ==")
        print(f"   ground truth: {len(truth)} students present")

        configs = [(("tiled " + args.tiles) if tiles_cfg else "whole-frame", tiles_cfg)]
        if args.compare_tiling:
            configs.append(("whole-frame", None))
        runs = []
        for name, tiles in configs:
            print(f"\n-- pass: {name} --")
            runs.append((name, observe_frames(paths, scope, tiles, args.overlap,
                                              thr, margin)))

    summaries = {}
    for name, obs in runs:
        summaries[name] = print_report(obs, truth, args.interval, args.confirm_hits,
                                       label=f"({name})" if len(runs) > 1 else "")

    if len(runs) > 1:
        print("\n=== Tiling comparison ===")
        for name, s in summaries.items():
            print(f"  {name:<16} coverage {s['coverage'] * 100:5.1f}%  "
                  f"wrong marks {len(s['false_marks'])}")

    out = {"model": model_info, "interval_s": args.interval,
           "confirm_hits": args.confirm_hits, "students_present": len(truth),
           "runs": {name: {**s, "curve": coverage_curve(
               dict(runs)[name], set(truth), args.confirm_hits, args.interval)}
               for name, s in summaries.items()}}
    with open(args.out + ".json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {args.out}.json")


if __name__ == "__main__":
    main()
