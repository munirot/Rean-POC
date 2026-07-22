# Rean · Face Attendance — Python service (POC)

A real face-recognition backend: **FastAPI + InsightFace (ArcFace, 512-d)** with the model
**hosted and run in Python** (no browser CDN). Enrolled face embeddings are stored in
**MongoDB**. The web UI in `web/` calls the service over HTTP.

```
Browser (web/index.html)  ──HTTP──►  FastAPI (app/)  ──►  InsightFace model (~/.insightface)
   camera / photo / roster              /api/*                     │
                                          └────────────────►  MongoDB (students + embeddings)
```

## Architecture

| Layer | File | Role |
|---|---|---|
| Config | `app/config.py` | env-driven settings (Mongo, model pack, threshold, device) |
| Model | `app/engine.py` | InsightFace wrapper: detect, 512-d embed, crop thumb, cosine match |
| Store | `app/db.py` | MongoDB repo + sample roster |
| API | `app/main.py` | FastAPI routes + CORS + serves the frontend |
| Schemas | `app/schemas.py` | typed request/response models |
| Frontend | `web/index.html` | camera/photo capture, roster, results — pure fetch calls |

## Data source (wired to the migrated DB)

The service now reads the **real migrated collections** in `rean_face_poc`:

- `students` — roster shown in the UI (mapped: `StuID`→sid, `FNa`+`LNa`→name, `CurCrNm`→class).
- `face_embeddings` — **new**, one doc per enrolled student (`{StuID, emb[512], embVer, quality,
  thumb}`). Kept separate so real student documents are never modified.
- `attendance` — recognized check-ins are written here in the real schema (`InId/PrID/CrID/
  DeptID/SemID/SecID/AcYr, StuID, StuNa, SubID, SubNa, date, session, status:'P', source:'face'`),
  and the dashboard/records read from it (present = distinct `StuID` with `status:'P'`).
- `subjects` — used to resolve a `SubID/SubNa` for a student's course when marking.

Load the data first with `../sample-data/seed_sample_data.py`. Collection names are overridable
via env (see `.env.example`).

## Share the seeded database with your team

So teammates don't have to re-seed, share one snapshot of the `rean_face_poc` database. Each
teammate restores it into **their own** local Mongo container — everyone starts from identical
data, but the copies are independent (one person's edits don't affect anyone else's; re-share a
new snapshot when the canonical data changes).

> **Gotcha:** the root user lives in Mongo's `admin` database, so dump/restore commands **must**
> include `?authSource=admin` in the URI. Without it you get
> `AuthenticationFailed ... SCRAM-SHA-1` even though the password is correct — `mongodump --db`
> otherwise tries to authenticate against `rean_face_poc`, where the user doesn't exist.

**Maintainer — publish a snapshot** (`my-mongodb` is the container name; swap in yours):

```bash
docker exec my-mongodb mongodump \
  --uri="mongodb://admin:mysecurepassword@localhost:27017/?authSource=admin" \
  --db=rean_face_poc --archive=/tmp/rean.archive --gzip
docker cp my-mongodb:/tmp/rean.archive ./rean_face_poc.archive
```

Share `rean_face_poc.archive` (shared drive / S3; or git-LFS to version it with the repo — don't
commit the raw binary without LFS). Check `ls -lh ./rean_face_poc.archive` first: the
`face_embeddings` collection holds 512-d vectors, so it can get large.

**Teammate — restore the snapshot.** With a local Mongo running (same creds as below) and
`rean_face_poc.archive` in the current folder:

```bash
# 1. start a local Mongo if you don't have one (creds must match the archive's URI)
docker run -d --name rean-mongo \
  -e MONGO_INITDB_ROOT_USERNAME=admin -e MONGO_INITDB_ROOT_PASSWORD=mysecurepassword \
  -v rean-mongo-data:/data/db -p 27017:27017 mongo:latest

# 2. copy the archive into the container, then restore
docker cp ./rean_face_poc.archive rean-mongo:/tmp/rean.archive
docker exec rean-mongo mongorestore \
  --uri="mongodb://admin:mysecurepassword@localhost:27017/?authSource=admin" \
  --archive=/tmp/rean.archive --gzip --drop
```

`--drop` clears each collection before loading, so restores are repeatable (a re-run resets to
the snapshot instead of duplicating docs) — but it **overwrites the teammate's local changes** to
those collections. Verify with:

```bash
docker exec rean-mongo mongosh \
  "mongodb://admin:mysecurepassword@localhost:27017/rean_face_poc?authSource=admin" \
  --quiet --eval 'db.students.countDocuments()'
```

A non-zero count means the data loaded. No `.env` change is needed — the default
`MONGO_URI` already targets `localhost:27017`. If a teammate names their container something
other than `rean-mongo`, or uses different credentials, adjust the name and URI in all commands
to match. (For a single **shared, live** database instead of per-person copies, host Mongo on
Atlas or a VM and point everyone's `MONGO_URI` at it.)

## Prerequisites

- **Python 3.9–3.11**
- **MongoDB** running locally (e.g. `docker run -d -p 27017:27017 mongo:7`)
- Internet on first run (installs deps + downloads the model pack once, ~300 MB for `buffalo_l`)

## Run

```bash
cd face-service
cp .env.example .env          # optional — defaults work out of the box
./run.sh                      # creates .venv, installs, starts uvicorn
```

Then open **http://localhost:8000** (served by the API, so live camera works via localhost).

Manual alternative:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | — | model/device/db status + counts |
| GET | `/api/students` | — | roster with enrollment state + thumbnails |
| POST | `/api/students/{sid}/enroll` | multipart `file` | detects largest face, stores 512-d embedding |
| DELETE | `/api/students/{sid}/enroll` | — | removes the student's face profile |
| POST | `/api/students/seed?force=true` | — | (re)seed sample roster |
| POST | `/api/students/reset` | — | clear all embeddings |
| POST | `/api/recognize` | multipart `file`, optional `threshold`, `source` | per-face `{bbox, name, similarity, accuracy, recognized, live, liveness_score}` |

Interactive docs at **http://localhost:8000/docs**.

Quick check with curl:
```bash
curl -s localhost:8000/api/health | python3 -m json.tool
curl -s -F file=@sophea.jpg localhost:8000/api/students/S-1001/enroll
curl -s -F file=@classroom.jpg -F threshold=0.35 localhost:8000/api/recognize
```

## Configuration (`.env`)

- `MODEL_PACK` — `buffalo_l` (accurate, default) or `buffalo_s` (light/fast). Change and
  restart; re-enroll students since embeddings are model-specific (`embVer` tracks this).
- `DEVICE` — `cpu` (default) or `gpu` (install `onnxruntime-gpu`, needs CUDA).
- `MATCH_THRESHOLD` — cosine-similarity cutoff (0..1); lower = stricter. The UI slider
  overrides this per request.

## Demo flow

1. Open http://localhost:8000 — top-right shows model pack, device, and enrolled count.
2. Enroll 2–3 "Profile expected" students via **Upload** (clear front-facing photo) or
   **Camera**. Leave the "New student" rows empty.
3. **Start camera** (or **Capture / upload photo** on a phone) and point at an enrolled face
   → name + accuracy %. An un-enrolled face reads **Unknown**.
4. Adjust **Match strictness** to see accept/reject change.

### Phone testing
- **Photo capture** works over plain `http://<computer-ip>:8000` (native camera picker).
- **Live camera** needs HTTPS — tunnel with the repo's `../ngrok.yml` (`ngrok http 8000`) and
  open the `https://…` URL on the phone.

## Troubleshooting

**`Could not find a version that satisfies the requirement onnxruntime==...`**
The requirements are now unpinned, so `pip install -r requirements.txt` will grab whatever
current wheel matches your Python. If you still see it, upgrade pip first:
`./.venv/bin/pip install --upgrade pip`.

**`No matching distribution for numpy<2` (usually Python 3.13)**
numpy 1.x has no wheels for Python 3.13. Two options:
- **Recommended:** use **Python 3.11** for this service (best InsightFace compatibility):
  `python3.11 -m venv .venv` then reinstall.
- Or drop the cap: change `numpy<2` to `numpy` in `requirements.txt`. Current onnxruntime /
  opencv wheels support numpy 2; if InsightFace then errors on a removed numpy alias, fall
  back to Python 3.11.

**`insightface` tries to build from source / needs a C++ compiler**
Prebuilt wheels don't exist for every OS+Python combo. Easiest fix is again **Python 3.11**,
which has wheels. On Linux you can also `apt-get install build-essential`; on macOS install
Xcode command-line tools (`xcode-select --install`).

**First `/api/recognize` is slow, or times out**
The very first call loads + downloads the model. After that, `buffalo_l` on CPU is still
heavier per frame — set `MODEL_PACK=buffalo_s` or `DEVICE=gpu` if the live camera lags.

**`OperationFailure: createIndexes requires authentication` (code 13, Unauthorized)**
Your MongoDB has authentication enabled but `MONGO_URI` has no credentials. Either:
- Point at your Mongo with a user: in `.env`, set
  `MONGO_URI=mongodb://USER:PASSWORD@localhost:27017/?authSource=admin`
  (the user needs read/write on the `rean_face_poc` DB), **or**
- Run a throwaway auth-less Mongo for the POC on a different port so it won't clash with your
  existing one:
  `docker run -d --name rean-mongo -p 27018:27017 mongo:7`
  then set `MONGO_URI=mongodb://localhost:27018`.
The app no longer crashes on this — it logs the warning and `/api/health` reports `mongo: down`
until the URI is fixed.

**Roster loads but enroll/recognize returns 503**
The model failed to load — check the server log at startup. Usually a numpy/onnxruntime
mismatch from the cases above.

## Anti-spoofing (liveness)

The service now runs a passive **presentation-attack check** before trusting any identity
match, so a printed photo or a face shown on a screen is rejected instead of marked present.
Details and rollout in `../docs/face-antispoofing-plan.md`.

- **Combined gate:** `/api/recognize` returns `recognized=true` only when the identity
  matches **and** the face passes liveness. A matched student presented as a photo comes
  back `recognized=false, reason="spoof_suspected"` (distinct from `unknown`), plus
  `live` and `liveness_score` fields. Enrollment applies a stricter liveness cutoff.
- **Two backends, automatic:** if an ONNX model exists at `ANTISPOOF_MODEL_PATH`
  (e.g. MiniFASNet / "Silent-Face-Anti-Spoofing"), it is used. Otherwise a built-in
  classical-CV baseline (frequency-domain moiré, micro-texture, chroma, specular, focus)
  runs so the check works out of the box. **Drop in the ONNX weights for production-grade
  accuracy** — the baseline should be calibrated on real kiosk/phone samples.
- **Config (`.env`):** `ANTISPOOF_ENABLED` (kill-switch → instant rollback),
  `LIVENESS_THRESHOLD` / `ENROLL_LIVENESS_THRESHOLD`, per-source
  `LIVENESS_THRESHOLD_KIOSK` / `_PHONE`, and `ANTISPOOF_FAIL_CLOSED` (refuse vs. allow on
  model error). Pass `source=kiosk|phone` on `/api/recognize` to pick the per-source cutoff.

```bash
# a live face passes, a printout/screen is rejected:
curl -s -F file=@live.jpg   -F source=phone localhost:8000/api/recognize | python3 -m json.tool
```

## Notes & limitations

- Liveness ships with a **classical-CV baseline** that is a functional scaffold, not a
  strong detector — install a MiniFASNet ONNX model and calibrate thresholds on real data
  before relying on it. See `../docs/face-antispoofing-plan.md`.
- Recognition matches every detected face against all enrolled embeddings (brute-force cosine).
  Fine for a POC roster; production scopes the gallery per section and can use a vector index.
- This standalone service maps directly onto the planned production `face-service`; the MongoDB
  documents here are the POC form of the `face_embeddings` collection in the plan.
