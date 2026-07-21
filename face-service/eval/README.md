# Accuracy benchmark

Measures how well the face model separates the **right** student from **everyone else**, and
recommends a `MATCH_THRESHOLD` to put in `.env`. Uses the same InsightFace model as the service.

## 1. Arrange a labeled dataset

One folder per person, 2+ photos each (more is better; vary angle/lighting/day):

```
dataset/
  Sophea_Chan/   1.jpg  2.jpg  3.jpg
  Dara_Kim/      a.jpg  b.jpg
  Vichea_Ngo/    p1.jpg p2.jpg p3.jpg p4.jpg
  ...
```

Good sources for a first run: capture 3–5 photos of ~10–20 volunteers on the actual kiosk
camera, or use a public set (e.g. LFW) for a sanity check. The more the dataset looks like
your real deployment (same camera, lighting, phone selfies), the more trustworthy the number.

### No photos and no volunteers? Use the public LFW set

`eval/get_lfw.py` downloads **LFW** (Labeled Faces in the Wild — thousands of real people,
already one-folder-per-person) and builds a ready-to-benchmark subset:

```bash
python -m eval.get_lfw                     # ~60 identities with >=4 photos each
python -m eval.benchmark --data ./dataset_lfw --far-target 0.01
```

Options: `--max-ids`, `--min-images`, or `--tgz /path/to/lfw.tgz` if you've already downloaded
it (or the auto-download is blocked on your network). This gives a real model accuracy number
with zero setup. Caveat: LFW is varied web photos, so treat it as a **model sanity check** —
your production accuracy still depends on your own camera/lighting, which is why a few
self-captured photos are worth adding later.

An even quicker option to just eyeball separation is the built-in synthetic mode
(`--synthetic 50`), but that validates the math, not the real model.

## 2. Run it

From `face-service/`, inside the venv:

```bash
python -m eval.benchmark --data /path/to/dataset
```

Useful flags:

- `--gallery-per-id 1` — how many photos build each enrolled template (1 = one profile photo,
  like production). Increase to simulate multi-shot enrollment.
- `--far-target 0.01` — the operating point: the maximum false-accept rate you'll tolerate.
  Attendance should bias low (never mark the wrong student present); 0.01 or 0.005 are typical.
- `--tmin/--tmax/--tstep` — threshold sweep range (default 0.20–0.70).
- `--out myreport` — output prefix for `.json`, `.csv`, and (if matplotlib is installed)
  `_scores.png` + `_roc.png`.

Validate the harness itself without any images:

```bash
python -m eval.benchmark --synthetic 50 --spread 0.08
```

## 3. Read the results

```
Score separation:  genuine mean 0.62 ... | impostor mean 0.09 ...
Rank-1 identification accuracy: 98.5%
Equal Error Rate (EER): 1.20% at threshold 0.41
Operating point @ FAR<=0.010: threshold 0.38 -> FAR 0.90% FRR 3.10% (TAR 96.9%)
>>> RECOMMENDED MATCH_THRESHOLD = 0.38
```

- **Genuine vs impostor separation** — the bigger the gap, the more reliable. Overlap between
  the two distributions (see `_scores.png`) is where errors live.
- **Rank-1 accuracy** — of all probe faces, how often the top match is the correct student
  (closed-set, kiosk-style). Want high-90s%.
- **FAR** (False Accept Rate) — impostors wrongly accepted → *marking the wrong student present.*
  Keep this low; it's the costly error for attendance.
- **FRR** (False Reject Rate) — genuine students rejected → they retry or mark manually. Annoying
  but safe.
- **EER** — where FAR == FRR; a single-number quality score (lower = better model/data).
- **Recommended threshold** — the strictest-usable cutoff meeting your FAR target. Put it in
  `.env` as `MATCH_THRESHOLD` and restart the service.

## 4. Act on it

- If accuracy is high → lock the threshold, move on to liveness / Node integration.
- If FRR is too high at your FAR target → collect better/more enrollment photos, raise
  `--gallery-per-id`, or improve capture quality (lighting, face size). Model/threshold changes
  come after data quality.
- If even the best threshold looks weak → try `MODEL_PACK=buffalo_l` (if not already), ensure
  faces are large/frontal, and consider GPU so you can use higher `DET_SIZE`.

> Reminder: this measures recognition accuracy only. It does **not** test liveness — a printed
> photo can still score as genuine. Anti-spoofing is a separate step in the plan.
