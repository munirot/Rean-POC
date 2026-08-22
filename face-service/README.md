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

Interactive docs at **http://localhost:8000/docs**. Everything except `/api/health`,
`/api/institutes` and `/api/auth/login` needs `Authorization: Bearer <token>`; the
caller's role and course scope are recomputed from the live `logins` document on
every request, so a token never asserts more than the login currently grants.

**Faces & recognition**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/face/pose` | per-frame head pose + quality + liveness, for guided enrollment |
| POST | `/api/students/{sid}/enroll` | multi-angle enrollment; every frame re-validated server-side |
| DELETE | `/api/students/{sid}/enroll` | remove a face profile |
| POST | `/api/recognize` | per-face `{bbox, name, similarity, gap, recognized, live, reason}` |

**Attendance**

| Method | Path | Purpose |
|---|---|---|
| POST/GET | `/api/attendance` | mark (face/kiosk/self) · list. Marking obeys the capture window |
| PUT | `/api/attendance` | staff correction — deliberately never window-gated |
| DELETE | `/api/attendance/{id}` | undo a record |
| GET | `/api/attendance/summary`, `/roster` | dashboard counts · per-student roster for a date |
| GET | `/api/attendance/policy` | effective capture mode + which period is live right now |

**Student-initiated workflows**

| Method | Path | Purpose |
|---|---|---|
| POST/GET | `/api/attendance/disputes` | student challenges a mark · scoped queue |
| POST | `/api/attendance/disputes/{id}/resolve` | staff approve (corrects the row) / reject |
| POST/GET | `/api/attendance/leave` | request excused absence · scoped queue |
| POST | `/api/attendance/leave/{id}/resolve` | staff approve — reclassifies absences to `E` |

**Whole-class camera** (off unless `CLASS_CAM_ENABLED=true`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/class-sessions` | staff | open a sitting for a class/period/room(s) |
| POST | `/api/class-sessions/{id}/frame` | **device token** | ingest one frame → detect → match → accumulate |
| GET | `/api/class-sessions/{id}` | staff | live buckets: confirmed / ambiguous / not detected |
| POST | `/api/class-sessions/{id}/close` | staff | auto-mark the confirmed; return the exception list |

**Insight & admin**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/students`, `/{sid}/profile`, `/stats`, `/plan` | roster · profile · figures · improvement plan |
| GET | `/api/analytics/cohort` | at-risk signals for a class |
| POST/GET/DELETE | `/api/chat*` | grounded chat, scoped to the caller |
| GET | `/api/courses` | the caller's own courses (staff) |
| GET/PUT | `/api/admin/attendance-periods` | capture windows (admin) |
| GET/PUT/DELETE | `/api/admin/attendance-policies` | capture mode per institute/course/section (admin) |
| GET/PUT/DELETE | `/api/admin/class-rooms` | per-room camera calibration (admin) |
| POST | `/api/admin/class-devices` | issue a room camera token (admin) |
| GET | `/api/admin/attendance-sessions`, `/courses` | pre-rollout data audit · course list (admin) |

Quick check with curl:
```bash
curl -s localhost:8000/api/health | python3 -m json.tool
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/students
```

## Configuration (`.env`)

`app/config.py` loads `face-service/.env` itself, and real environment variables win
over the file — so `CLASS_CAM_ENABLED=true ./run-all.sh` overrides it for one run.
Every setting has a default, so a missing `.env` is silent: check `/api/health` and
the startup log to see what is actually in force.

**Model & matching**
- `MODEL_PACK` — `buffalo_l` (accurate, default) or `buffalo_s` (light/fast). Change and
  restart; re-enroll students since embeddings are model-specific (`embVer` tracks this).
- `DEVICE` — `cpu` (default), `gpu`/`cuda` (NVIDIA), or `coreml` on Apple Silicon.
- `MATCH_THRESHOLD` — cosine cutoff (0..1). A face is accepted when similarity **>=**
  this, so **higher = stricter**: raising it cuts false accepts and costs some real
  matches. (At 0.20 a typical set accepts ~40% of impostors; at 0.40, ~0.2%.)
- `MATCH_MARGIN` — the top identity must beat the runner-up by this much, else the
  match is refused as `ambiguous` rather than coin-flipping between look-alikes.
  Calibrate both with `python -m eval.benchmark`; see `../docs/face-matching-tuning.md`.

**Liveness** — `ANTISPOOF_ENABLED`, `LIVENESS_THRESHOLD` (+ `_KIOSK`/`_PHONE`),
`ANTISPOOF_FAIL_CLOSED`, `SELF_CHECKIN_CHALLENGE` (require a live head-turn for
unsupervised student self check-in). Calibrate with `python -m eval.antispoof_bench`.

**Attendance policy** — `ATTENDANCE_ENFORCE_WINDOW` (confine automated marking to the
configured capture periods; staff corrections are never gated), `ATTENDANCE_DEFAULT_MODE`.
See `../docs/attendance-policy-plan.md`.

**Whole-class camera** — `CLASS_CAM_ENABLED` (master switch, off by default),
`CLASS_CAM_CONFIRM_HITS`, `CLASS_CAM_TILES`, `CLASS_CAM_TILE_OVERLAP`,
`CLASS_CAM_FRAME_INTERVAL`. Per-room overrides live in the `class_rooms` collection.
See `../docs/class-camera-attendance-plan.md`.

**Chat** — `CHAT_ENABLED`, `CHAT_BASE_URL`, `CHAT_MODEL`, `CHAT_MODEL_CONTEXT`,
`CHAT_MAX_CONCURRENCY`, `CHAT_RATE_PER_MIN`. See `../docs/chat-model.md`.

**Security** — set `AUTH_SECRET` in production. Without it a random per-process key
signs session tokens, so every restart logs everyone out and tokens do not verify
across `uvicorn --workers`.

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
- Recognition is brute-force cosine over the **scoped** gallery (institute, and a
  teacher's courses), not the whole roster — a kiosk can never match a student who
  cannot be in that room. Brute force is exact and stays comfortable well past a
  typical campus: ~4ms at 100k vectors. The pressure that arrives first is memory,
  since each uvicorn worker holds its own copy; an ANN index is a scaling answer,
  not a speed one.
- Whole-class camera capture is built (session, tiled detection, frame ingest,
  teacher review, per-room calibration) but **off by default** and unproven: the
  Phase 0 coverage measurement in a real room has not been run. It can only ever add
  "present" — it never marks anyone absent — so a badly-placed camera produces a
  longer exception list, not wrong attendance.
- This standalone service maps directly onto the planned production `face-service`; the MongoDB
  documents here are the POC form of the `face_embeddings` collection in the plan.
