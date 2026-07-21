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
| POST | `/api/recognize` | multipart `file`, optional `threshold` | per-face `{bbox, name, similarity, accuracy, recognized}` |

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

## Notes & limitations

- POC has **no liveness / anti-spoofing** — a photo of a photo can pass. Covered in the
  production plan (`../docs/face-attendance-plan.md`).
- Recognition matches every detected face against all enrolled embeddings (brute-force cosine).
  Fine for a POC roster; production scopes the gallery per section and can use a vector index.
- This standalone service maps directly onto the planned production `face-service`; the MongoDB
  documents here are the POC form of the `face_embeddings` collection in the plan.
