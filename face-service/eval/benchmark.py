"""Accuracy benchmark for the face-attendance model.

Enrolls one template per identity from a labeled photo set, then scores probe
images to measure how well the model separates genuine matches from impostors.
Reports FAR / FRR across thresholds, the Equal-Error-Rate point, rank-1
identification accuracy, and a recommended operating threshold.

Dataset layout (one folder per person):
    dataset/
        Sophea_Chan/  img1.jpg  img2.jpg  img3.jpg
        Dara_Kim/     a.jpg     b.jpg
        ...

Run (from the face-service/ directory, inside the venv):
    python -m eval.benchmark --data /path/to/dataset
    python -m eval.benchmark --data ./dataset --gallery-per-id 1 --far-target 0.01
    python -m eval.benchmark --synthetic 40          # no model, validates the math

Notes:
- Genuine score  = cosine(probe, template of the SAME person)
- Impostor score = cosine(probe, template of a DIFFERENT person)
- HIGHER threshold = stricter: fewer false accepts (FAR down), more false rejects
  (FRR up). A match is accepted when cosine >= threshold, so lowering it lets more
  through — at 0.20 a typical set accepts ~40% of impostors, at 0.40 about 0.2%.
"""
import argparse
import json
import os
import sys
from glob import glob

import numpy as np

# allow "python eval/benchmark.py" as well as "-m eval.benchmark"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


# --------------------------------------------------------------------------- #
# Embedding extraction (real model)
# --------------------------------------------------------------------------- #
def build_dataset_embeddings(data_dir, gallery_per_id, min_images, seed):
    """Return (templates, probes) where
    templates: {identity: unit_vector(512)}   one enrolled template per identity
    probes:    list of (identity, unit_vector) held-out images
    """
    from app import engine as eng  # heavy import only when actually using the model

    engine = eng.get_engine()
    rng = np.random.default_rng(seed)

    identities = sorted(
        d for d in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, d))
    )
    if not identities:
        raise SystemExit(f"No identity subfolders found in {data_dir}")

    def embed_path(path):
        with open(path, "rb") as f:
            img = engine.decode(f.read())
        faces = engine.detect(img)
        if not faces:
            return None
        return engine.embedding(faces[0])  # largest face, L2-normalized

    templates, probes = {}, []
    skipped_ids, no_face = [], 0
    for ident in identities:
        files = sorted(
            p for p in glob(os.path.join(data_dir, ident, "*")) if p.lower().endswith(IMG_EXT)
        )
        embs = []
        for p in files:
            e = embed_path(p)
            if e is None:
                no_face += 1
            else:
                embs.append(e)
        if len(embs) < min_images:
            skipped_ids.append((ident, len(embs)))
            continue
        idx = rng.permutation(len(embs))
        g_idx, p_idx = idx[:gallery_per_id], idx[gallery_per_id:]
        template = _unit(np.mean([embs[i] for i in g_idx], axis=0))
        templates[ident] = template
        for i in p_idx:
            probes.append((ident, embs[i]))

    print(f"Identities used: {len(templates)} | probe images: {len(probes)} | "
          f"images with no detectable face: {no_face}")
    if skipped_ids:
        print(f"Skipped {len(skipped_ids)} identities with < {min_images} usable images: "
              + ", ".join(f"{i}({n})" for i, n in skipped_ids[:8])
              + (" …" if len(skipped_ids) > 8 else ""))
    if len(templates) < 2:
        raise SystemExit("Need at least 2 identities with enough images to benchmark.")
    return templates, probes


# --------------------------------------------------------------------------- #
# Synthetic embeddings (validate the metric math with no model)
# --------------------------------------------------------------------------- #
def build_synthetic_embeddings(n_ids, imgs_per_id, gallery_per_id, spread, seed):
    """Each identity = a random center on the unit sphere; images = center + noise.
    `spread` is per-dimension noise sigma. In 512-d the noise vector has norm
    ~ sigma*sqrt(512), so realistic values are small (~0.04 easy .. ~0.12 hard);
    higher = more intra-identity scatter = harder / more overlap."""
    rng = np.random.default_rng(seed)
    dim = 512
    templates, probes = {}, []
    for k in range(n_ids):
        center = _unit(rng.normal(size=dim))
        embs = [_unit(center + spread * rng.normal(size=dim)) for _ in range(imgs_per_id)]
        template = _unit(np.mean(embs[:gallery_per_id], axis=0))
        templates[f"id_{k:03d}"] = template
        for e in embs[gallery_per_id:]:
            probes.append((f"id_{k:03d}", e))
    return templates, probes


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _unit(v):
    n = np.linalg.norm(v)
    return (v / n).astype(np.float32) if n else v.astype(np.float32)


def score_pairs(templates, probes):
    """Return arrays of genuine and impostor cosine scores, plus rank-1 accuracy."""
    ids = list(templates.keys())
    mat = np.stack([templates[i] for i in ids])          # (N, 512)
    id_index = {i: k for k, i in enumerate(ids)}

    genuine, impostor = [], []
    rank1_correct = 0
    for true_id, emb in probes:
        sims = mat @ emb                                  # cosine vs every template
        gi = id_index[true_id]
        genuine.append(float(sims[gi]))
        impostor.extend(float(sims[k]) for k in range(len(ids)) if k != gi)
        if ids[int(np.argmax(sims))] == true_id:
            rank1_correct += 1
    rank1 = rank1_correct / len(probes) if probes else 0.0
    return np.array(genuine), np.array(impostor), rank1


def genuine_margins(templates, probes):
    """Per genuine probe, the lead of the true identity over the best OTHER
    identity: margin = cosine(true) - max cosine(any wrong identity).

    This is exactly what the runtime ambiguity guard (MATCH_MARGIN) tests. A small
    or negative margin is a near-confusion; a healthy one is a confident match. The
    distribution lets us pick a MATCH_MARGIN that rejects near-ties while passing
    the vast majority of real matches."""
    ids = list(templates.keys())
    mat = np.stack([templates[i] for i in ids])
    idx = {i: k for k, i in enumerate(ids)}
    out = []
    for true_id, emb in probes:
        sims = mat @ emb
        gi = idx[true_id]
        best_other = np.max(np.delete(sims, gi)) if len(ids) > 1 else -1.0
        out.append(float(sims[gi] - best_other))
    return np.array(out)


def recommend_margin(margins, percentile):
    """MATCH_MARGIN at the given low percentile of genuine margins (clamped >=0):
    setting the guard here rejects roughly `percentile`% of real matches (the
    least-separated ones) while catching ambiguous look-alikes below it."""
    if margins.size == 0:
        return 0.0
    return round(max(0.0, float(np.percentile(margins, percentile))), 3)


def sweep(genuine, impostor, thresholds):
    """FAR = impostors accepted; FRR = genuine rejected; TAR = 1-FRR."""
    rows = []
    for t in thresholds:
        far = float(np.mean(impostor >= t)) if impostor.size else 0.0
        frr = float(np.mean(genuine < t)) if genuine.size else 0.0
        rows.append({"threshold": round(float(t), 4), "FAR": far, "FRR": frr,
                     "TAR": 1 - frr, "accuracy": 1 - (far + frr) / 2})
    return rows


def find_eer(rows):
    best = min(rows, key=lambda r: abs(r["FAR"] - r["FRR"]))
    return best, (best["FAR"] + best["FRR"]) / 2


def threshold_at_far(rows, far_target):
    """Best operating point: the LOWEST threshold whose FAR is still within budget.

    Anything higher would reject more genuine students for no extra safety, so this
    is the point that keeps false accepts inside `far_target` while losing the
    fewest real matches."""
    ok = [r for r in rows if r["FAR"] <= far_target]
    return min(ok, key=lambda r: r["threshold"]) if ok else None


# --------------------------------------------------------------------------- #
# Optional plots
# --------------------------------------------------------------------------- #
def maybe_plot(genuine, impostor, rows, out_prefix):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("(matplotlib not installed — skipping plots)")
        return []
    saved = []
    # score histogram
    plt.figure(figsize=(7, 4))
    plt.hist(impostor, bins=50, alpha=.6, label="impostor", color="#ef4444", density=True)
    plt.hist(genuine, bins=50, alpha=.6, label="genuine", color="#22c55e", density=True)
    plt.xlabel("cosine similarity"); plt.ylabel("density"); plt.legend()
    plt.title("Genuine vs impostor score distributions"); plt.tight_layout()
    p1 = out_prefix + "_scores.png"; plt.savefig(p1, dpi=120); plt.close(); saved.append(p1)
    # ROC (FAR vs TAR)
    far = [r["FAR"] for r in rows]; tar = [r["TAR"] for r in rows]
    plt.figure(figsize=(5, 5))
    plt.plot(far, tar, "-o", ms=3, color="#0ea5e9")
    plt.xlabel("FAR"); plt.ylabel("TAR (1-FRR)"); plt.title("ROC"); plt.grid(alpha=.3)
    plt.tight_layout()
    p2 = out_prefix + "_roc.png"; plt.savefig(p2, dpi=120); plt.close(); saved.append(p2)
    return saved


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Face-recognition accuracy benchmark")
    ap.add_argument("--data", help="dataset dir (one subfolder per identity)")
    ap.add_argument("--synthetic", type=int, metavar="N",
                    help="run on N synthetic identities instead of real images")
    ap.add_argument("--gallery-per-id", type=int, default=1,
                    help="images used to build each enrolled template (default 1)")
    ap.add_argument("--min-images", type=int, default=2,
                    help="skip identities with fewer usable images (default 2)")
    ap.add_argument("--far-target", type=float, default=0.01,
                    help="operating point: max acceptable false-accept rate (default 0.01)")
    ap.add_argument("--margin-percentile", type=float, default=1.0,
                    help="MATCH_MARGIN is set at this percentile of genuine margins "
                         "(default 1.0 => ~1%% of real matches rejected by the guard)")
    ap.add_argument("--tmin", type=float, default=0.20)
    ap.add_argument("--tmax", type=float, default=0.70)
    ap.add_argument("--tstep", type=float, default=0.01)
    ap.add_argument("--spread", type=float, default=0.06,
                    help="synthetic-only: per-dim noise sigma (~0.04 easy .. ~0.12 hard)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="eval_report",
                    help="output prefix for .json / .csv / .png (default eval_report)")
    args = ap.parse_args()

    if args.synthetic:
        print(f"== Synthetic benchmark: {args.synthetic} identities (spread={args.spread}) ==")
        templates, probes = build_synthetic_embeddings(
            args.synthetic, imgs_per_id=6, gallery_per_id=args.gallery_per_id,
            spread=args.spread, seed=args.seed)
        model_info = f"synthetic(spread={args.spread})"
    else:
        if not args.data:
            ap.error("provide --data DIR (or --synthetic N)")
        from app.config import settings
        model_info = f"{settings.model_pack}/{settings.device}"
        print(f"== Benchmark on {args.data} · model {model_info} ==")
        templates, probes = build_dataset_embeddings(
            args.data, args.gallery_per_id, args.min_images, args.seed)

    genuine, impostor, rank1 = score_pairs(templates, probes)
    margins = genuine_margins(templates, probes)
    rec_margin = recommend_margin(margins, args.margin_percentile)
    thresholds = np.arange(args.tmin, args.tmax + 1e-9, args.tstep)
    rows = sweep(genuine, impostor, thresholds)
    eer_row, eer = find_eer(rows)
    op_row = threshold_at_far(rows, args.far_target)

    # ---- console summary ----
    print(f"\nScore separation:  genuine  mean {genuine.mean():.3f} (min {genuine.min():.3f})"
          f"   |  impostor mean {impostor.mean():.3f} (max {impostor.max():.3f})")
    print(f"Rank-1 identification accuracy: {rank1*100:.1f}%   "
          f"({len(templates)} identities, {len(probes)} probes)")
    print(f"Equal Error Rate (EER): {eer*100:.2f}%  at threshold {eer_row['threshold']}")
    if op_row:
        print(f"Operating point @ FAR<= {args.far_target:.3f}:  threshold "
              f"{op_row['threshold']}  ->  FAR {op_row['FAR']*100:.2f}%  "
              f"FRR {op_row['FRR']*100:.2f}%  (TAR {op_row['TAR']*100:.1f}%)")
        recommended = op_row["threshold"]
    else:
        print(f"No threshold reaches FAR<= {args.far_target}; falling back to EER threshold.")
        recommended = eer_row["threshold"]

    print("\n threshold |   FAR   |   FRR   |   TAR")
    print(" ----------+---------+---------+--------")
    for r in rows:
        if abs((r["threshold"] * 100) % 5) < (args.tstep * 100) / 2:  # every ~0.05
            print(f"   {r['threshold']:.2f}    | {r['FAR']*100:6.2f}% | "
                  f"{r['FRR']*100:6.2f}% | {r['TAR']*100:6.2f}%")

    print(f"\nGenuine margin (true-id lead over best other id):  mean "
          f"{margins.mean():.3f}   p5 {np.percentile(margins, 5):.3f}   "
          f"p1 {np.percentile(margins, 1):.3f}")

    print(f"\n>>> RECOMMENDED MATCH_THRESHOLD = {recommended}  "
          f"(set this in .env; bias toward low FAR for attendance)")
    print(f">>> RECOMMENDED MATCH_MARGIN    = {rec_margin}  "
          f"(rejects near-ties at the p{args.margin_percentile:g} genuine margin)")

    # ---- artifacts ----
    summary = {
        "model": model_info, "identities": len(templates), "probes": len(probes),
        "gallery_per_id": args.gallery_per_id,
        "rank1_accuracy": rank1,
        "genuine_mean": float(genuine.mean()), "impostor_mean": float(impostor.mean()),
        "eer": eer, "eer_threshold": eer_row["threshold"],
        "far_target": args.far_target,
        "operating_threshold": recommended,
        "operating_point": op_row, "sweep": rows,
        "genuine_margin_mean": float(margins.mean()),
        "genuine_margin_p1": float(np.percentile(margins, 1)),
        "genuine_margin_p5": float(np.percentile(margins, 5)),
        "margin_percentile": args.margin_percentile,
        "recommended_margin": rec_margin,
    }
    with open(args.out + ".json", "w") as f:
        json.dump(summary, f, indent=2)
    with open(args.out + ".csv", "w") as f:
        f.write("threshold,FAR,FRR,TAR,accuracy\n")
        for r in rows:
            f.write(f"{r['threshold']},{r['FAR']},{r['FRR']},{r['TAR']},{r['accuracy']}\n")
    plots = maybe_plot(genuine, impostor, rows, args.out)
    print(f"\nWrote {args.out}.json, {args.out}.csv"
          + (", " + ", ".join(plots) if plots else ""))


if __name__ == "__main__":
    main()
