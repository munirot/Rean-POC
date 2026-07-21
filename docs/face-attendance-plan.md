# Face Attendance Recognition — Architecture & Implementation Plan

**Project:** Rean / mycamu (Node.js + Express + MongoDB + React)
**Feature:** Face-recognition attendance, with the ML model running as a separate, embeddable service
**Status:** Draft for review · Date: 2026-07-07

---

## 1. Goal & scope

Add face-recognition attendance to the existing mycamu system. A student's face is captured at attendance time, matched against a pre-enrolled face embedding, and — on a confident match — attendance is written through the **existing** attendance pipeline (`mattendance`, `mattendancelog`).

Two capture points are in scope:

1. **Classroom kiosk / tablet** — a supervised shared device at the room entrance captures faces (single or few at a time).
2. **Student phone selfie** — self check-in from the React app camera, guarded by liveness + location/session checks to deter proxy attendance.

The face **detection + embedding model runs as its own service** (Python / InsightFace), so it can be deployed, scaled, GPU-accelerated, and versioned independently of the Node monolith — and swapped without touching business logic. The Node app never loads an ML model; it only exchanges JSON/images with the face-service over HTTP.

Out of scope for v1: CCTV/passive multi-face recognition (design leaves room for it), and any change to how attendance percentages are computed.

---

## 2. Key decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Face engine | **InsightFace** (RetinaFace detect + ArcFace `buffalo_l`, 512-d embeddings), self-hosted | Best open-source accuracy, no per-call cost, satisfies "embed the model" |
| Model service | **Separate Python FastAPI microservice**, containerized | Keeps Node monolith JS-only; independent scaling/GPU; swappable model |
| Embedding store | **Existing MongoDB** (new collection) | No new infra; student counts are manageable; brute-force cosine match is fine at this scale |
| Capture points | **Kiosk/tablet + phone selfie** | Covers supervised and self-service flows |
| Matching location | In the **face-service** (it holds vectors in memory) with MongoDB as source of truth | Keeps heavy vector math out of Node; Node stays orchestration-only |

---

## 3. High-level architecture

```
                                 ┌─────────────────────────────────────┐
   Kiosk / Tablet ──┐            │            Node monolith             │
                    │  HTTPS     │              (app.js)                │
   React web app ───┼──────────► │  routes/rfaceattendance.js (NEW)     │
   (phone selfie)   │  (image +  │  routes/rfaceenroll.js     (NEW)     │
                    │   context) │  models/mfaceembedding.js  (NEW)     │
                    ┘            │  ── reuses ──                        │
                                 │  rattendance.js / mattendance        │
                                 │  rstudent / mstudent (PhotoImgID)    │
                                 └──────────────┬──────────────────────┘
                                                │  internal HTTP (JSON, base64/multipart)
                                                │  service-to-service token
                                                ▼
                                 ┌─────────────────────────────────────┐
                                 │       face-service (NEW, Python)     │
                                 │  FastAPI + InsightFace (ONNX)        │
                                 │  POST /detect   /embed   /match      │
                                 │  in-memory ANN index per institute   │
                                 └──────────────┬──────────────────────┘
                                                │  loads/refreshes vectors
                                                ▼
                                        MongoDB  (face_embeddings)
```

**Trust boundary:** the face-service is **internal only** — never exposed to the public internet. Browsers/kiosks talk only to the Node app; Node talks to the face-service over a private network with a shared service token. This keeps auth, rate-limiting, and audit in one place (Node) and keeps raw biometric processing behind the wall.

---

## 4. The face-service (separate, embeddable)

### 4.1 Responsibilities
- **Detect** faces in an image (bounding boxes, quality/pose score).
- **Embed** a detected face into a 512-d L2-normalized vector.
- **Match** a probe embedding against a candidate set and return the best student + similarity.
- **Liveness (basic)** passive checks (blur, single-face, face size, optional anti-spoof model) — see §8.
- Hold an **in-memory index** of enrolled embeddings, scoped per institute/section, refreshed from MongoDB.

The service is **stateless w.r.t. business data** — MongoDB is the source of truth. It can be killed/restarted and rebuild its index on boot.

### 4.2 API contract (internal)

All endpoints require header `X-Service-Token: <shared secret>`. Images sent as multipart or base64 JSON.

```
POST /v1/embed
  in:  { image (jpeg), maxFaces?: 1 }
  out: { faces: [ { bbox, det_score, embedding: float[512], quality } ] }

POST /v1/match
  in:  { image | embedding, scope: { InId, PrID?, CrID?, SecID? }, topK?: 3, threshold?: 0.35 }
  out: { matched: bool, candidates: [ { StuID, score } ], quality, liveness }

POST /v1/enroll-embed        # used during face enrollment; returns embedding only, no store
  in:  { image }
  out: { embedding: float[512], quality, faces: n }

POST /v1/index/refresh       # Node calls after enroll/unenroll to hot-reload vectors
  in:  { scope: { InId } }
  out: { count }

GET  /v1/healthz             # model loaded, index sizes, version
```

Matching uses **cosine similarity** on normalized ArcFace vectors. Default decision threshold ≈ **0.35–0.40** (tune on real data; see §9). Return `matched=false` if best score < threshold or if the top-2 gap is too small (ambiguous).

### 4.3 Model & packaging
- `insightface` with the `buffalo_l` model pack (RetinaFace-10GF detector + ArcFace r100), running via **ONNX Runtime** (CPU works; GPU/`onnxruntime-gpu` strongly recommended for kiosk latency and enrollment throughput).
- FastAPI + Uvicorn/Gunicorn, packaged in its own `Dockerfile` (Python 3.11 slim + system libs for OpenCV). Model weights baked into the image or mounted volume.
- Config via env: `MONGO_URI`, `SERVICE_TOKEN`, `MATCH_THRESHOLD`, `DEVICE=cpu|cuda`, `INDEX_SCOPE`.
- Add to `docker-compose`/deployment alongside the Node app on the private network. Health-gated startup (Node tolerates the service being briefly down).

### 4.4 Why a separate service (vs. in-Node)
- Node has no first-class InsightFace binding; running ArcFace in JS (`face-api.js`) is markedly less accurate.
- GPU scheduling, model memory (~hundreds of MB), and heavy CPU are isolated from the request-serving monolith.
- The model can be upgraded (new embedding version) and re-benchmarked without redeploying `app.js`.

---

## 5. Data model (new — in existing MongoDB)

Follows the project's existing conventions (short field names, `InId/PrID/CrID/DeptID/SemID/SecID`, `StFl`, `CrAt/MoAt/CrBy/MdBy`, `camu_db_connect` wrapper).

### 5.1 `models/mfaceembedding.js` — one enrolled template per student
```
FaceEmbeddingSchema {
  StuID:   String (indexed)        // ties to mstudent.CmStudID
  InId, CurPrID, CurCrID,
  CurDeptID, CurSemID, CurSecID     // scope copied from student for fast index filtering
  emb:      [Number]                // 512-d ArcFace vector (L2-normalized)
  embVer:   String                  // e.g. "buffalo_l/arcface_r100" — model version for re-enroll on upgrade
  quality:  Number                  // detector/quality score at enrollment
  srcImgId: String                  // reference to stored enrollment image (mimage / S3 PhotoImgID)
  enrollStatus: String              // 'PENDING' | 'ACTIVE' | 'REJECTED'
  StFl: 'A', CrAt, MoAt, CrBy, MdBy
}
```
Indexes: `{ StuID: 1 }` unique-ish (one active template per student), `{ InId:1, CurSecID:1, StFl:1 }` for scoped index loads.

### 5.2 `models/mfaceattendancelog.js` — audit of every recognition attempt
```
FaceAttnAuditSchema {
  StuID, InId, CrID, SecID, SubjId,
  captureSource: 'KIOSK' | 'PHONE',
  deviceId, sessionId,
  score:   Number,                  // match similarity
  decision: 'MATCH' | 'NO_MATCH' | 'AMBIGUOUS' | 'MANUAL_OVERRIDE',
  liveness: { passed: Bool, checks: {} },
  geo:      { lat, lng, accuracy },  // phone flow
  probeImgId: String,               // optional, retention-limited snapshot for disputes
  attnWritten: Bool,                 // did it post to mattendance?
  CrAt, CrBy
}
```
This is the tamper-evidence and dispute trail. It is **separate** from `mattendance` so recognition telemetry never pollutes the attendance percentage math.

**No raw face images stored by default.** Store the 512-d vector, not photos. If a probe snapshot is kept for dispute resolution, keep it short-retention and access-controlled (§8).

---

## 6. Enrollment flow (build the template gallery)

Students already have `PhotoImgID`. Two enrollment paths:

1. **Bulk from existing student photos** — a one-off/batch job: for each active student with a usable photo, Node fetches the image (Mongo `mimage` or S3), calls `POST /v1/enroll-embed`, and stores the returned vector in `face_embeddings` with `enrollStatus='ACTIVE'` (or `PENDING` for review if quality is low). New route surface: `routes/rfaceenroll.js`.
2. **Guided capture** — a React enrollment screen prompts the student/admin to capture 1–3 frames; the best-quality embedding is stored. Preferred for phone check-in accuracy.

Enrollment endpoints (Node, mounted in `app.js` like other routes):
```
POST /api/face/enroll/student        { StuID, image }      -> creates/updates template
POST /api/face/enroll/bulk           { InId, PrID, ... }   -> batch job, returns job id
GET  /api/face/enroll/status/:StuID
POST /api/face/enroll/reindex        { InId }              -> triggers /v1/index/refresh
```
Quality gate at enrollment: exactly one face, min face size, `det_score` above threshold, not blurry. Reject and re-prompt otherwise — enrollment quality dominates downstream accuracy.

---

## 7. Attendance (recognition) flow

### 7.1 Kiosk / tablet
1. Kiosk (a React screen in kiosk mode, or a thin tablet client) authenticates as a **device session** (device token issued by Node; tied to a room + the active timetable period).
2. Camera captures a frame → sends to Node `POST /api/face/attendance/mark` with `{ image, deviceId, InId, CrID, SecID, SubjId, period }`.
3. Node validates the device session + that a class/period is currently active (reusing timetable logic in `rtimetable`/`rattendance`).
4. Node forwards image to face-service `POST /v1/match` with scope narrowed to the section's enrolled students.
5. On `matched=true` above threshold and liveness pass → Node writes attendance via the **existing** path (the same functions `rattendance.js` uses to build `mattendance`/`mattendancelog` entries for `StuID`, subject, date, period) and appends a `face_attendance_audit` record.
6. Kiosk shows the recognized student's name/photo for visual confirmation; supervisor can reject a wrong match (logged as `MANUAL_OVERRIDE`).

### 7.2 Phone selfie
1. Student opens the React check-in screen during an open attendance window (Node validates the JWT session → `StuID` is known, so this is **1:1 verification**, not 1:N — much easier and safer).
2. App captures selfie (+ optional active-liveness prompt: blink/turn) and device geolocation → `POST /api/face/attendance/selfmark`.
3. Node calls face-service `/v1/match` scoped to a **single candidate** (`StuID` from the session) → verify similarity ≥ threshold.
4. Node checks the guardrails: attendance window open, geofence within campus/room radius, one submission per session, device/session not flagged.
5. On pass → write attendance + audit; else return a clear reason (out of window, out of area, liveness failed, low match).

### 7.3 Node → existing attendance write
Attendance stays keyed exactly as today (`InId/PrID/CrID/DeptID/SemID/SecID`, `StuID`, subject, `ForDate`, period). The face flow is only a **new way to trigger** the same write, so percentages, reports, and downstream logic are unchanged. Face marking sets a provenance flag on the audit (not on `mattendance`) so face-marked vs. manually-marked can be distinguished for review.

---

## 8. Security, privacy & anti-fraud

- **Biometric data is sensitive.** Store embeddings, not photos, by default. Treat `face_embeddings` as restricted: encrypt at rest (Mongo encryption / disk), restrict collection access, and never return raw vectors to clients.
- **Consent & retention:** capture student consent at enrollment; define retention (e.g., purge templates on graduation/withdrawal). Provide a delete path (`DELETE /api/face/enroll/:StuID`). Align with applicable data-protection rules (Cambodia PDP / institutional policy).
- **Face-service is private-network only**, guarded by `X-Service-Token`; never internet-facing. Node remains the only authenticated public surface (reuses existing JWT + rate-limit + `express-mongo-sanitize` + helmet CSP, which already allow-lists S3/blob for images).
- **Anti-proxy (phone flow):** liveness (passive blur/single-face + optional active blink/turn or a lightweight anti-spoof model), geofencing, one-submission-per-session, device binding, and open-window enforcement. Because the phone flow is 1:1 against the logged-in student, a spoof must both defeat liveness and match that specific student.
- **Audit everything** in `face_attendance_audit`; keep short-retention probe snapshots only for disputes, access-controlled.
- **Fail closed / graceful degrade:** if the face-service is unavailable, kiosk/phone marking returns a clear error and faculty fall back to manual attendance (existing UI). Attendance is never silently dropped.

---

## 9. Accuracy, thresholds & evaluation

- Start with cosine threshold ~**0.35–0.40**; calibrate against a labeled internal set to hit the desired FAR/FRR trade-off (bias toward **low false-accept** — never mark the wrong student present).
- Add an **ambiguity guard**: reject if top-1 and top-2 scores are within a small margin.
- Track per-cohort accuracy in the audit data; re-tune per institute if lighting/camera differs.
- **Model-version field (`embVer`)** enables safe upgrades: when the model changes, re-embed enrolled students in the background and switch over once coverage is complete.

---

## 10. Frontend (React) work

In `mycamu-react`:
- **Enrollment component:** camera capture, quality feedback, consent checkbox, submit to `/api/face/enroll/student`.
- **Kiosk screen:** full-screen capture loop, recognized-student confirmation card, supervisor override, device-session pairing.
- **Phone self check-in:** camera + liveness prompt, geolocation, window countdown, success/failure states.
- Reuse existing image/S3 upload conventions and the `getApiOutput` response shape used across routes.

---

## 11. Phased roadmap

**Phase 0 — Foundations (1 wk)**
Stand up the face-service skeleton (FastAPI + InsightFace, `/healthz`, `/embed`), Dockerize, wire onto the private network. Add `mfaceembedding` / `mfaceattendancelog` models and the service-token config. No user-facing change.

**Phase 1 — Enrollment (1–2 wks)**
`rfaceenroll.js` + bulk job from existing `PhotoImgID`s + React enrollment screen + quality gating + index refresh. Deliverable: a populated, reviewable template gallery.

**Phase 2 — Kiosk attendance (2 wks)**
`/v1/match`, `rfaceattendance.js`, device sessions tied to timetable periods, kiosk React screen, write-through to existing attendance, audit log. Pilot in a few rooms.

**Phase 3 — Phone self check-in (2 wks)**
1:1 verification flow, liveness, geofence, anti-proxy guardrails, self check-in screen.

**Phase 4 — Hardening & scale (ongoing)**
Threshold calibration, anti-spoof model, monitoring/alerting on face-service, retention/purge jobs, model-version upgrade path, optional move to a vector DB if student counts grow large.

---

## 12. New/changed files (summary)

**New — face-service (own repo/dir, e.g. `face-service/`)**
`app/main.py`, `app/engine.py` (InsightFace wrapper), `app/index.py` (in-memory ANN + Mongo load), `Dockerfile`, `requirements.txt`.

**New — Node**
`models/mfaceembedding.js`, `models/mfaceattendancelog.js`,
`routes/rfaceenroll.js`, `routes/rfaceattendance.js`,
`routes/facesvcClient.js` (thin HTTP client + service token),
route mounts added in `app.js`.

**New — React (`mycamu-react/src`)**
Enrollment, kiosk, and self check-in components.

**Reused unchanged**
`rattendance.js` / `mattendance` / `mattendancelog` (attendance write), `rstudent` / `mstudent` (`PhotoImgID`), S3/`mimage` image storage, JWT auth, rate-limit, helmet CSP.

---

## 13. Open questions to confirm before build

1. **Deployment target for the face-service** — is a GPU host available, or CPU-only? (Drives latency expectations and batch enrollment time.)
2. **Expected student count per institute** — confirms MongoDB brute-force matching is sufficient (comfortable to ~tens of thousands; beyond that, revisit a vector DB).
3. **Consent & retention policy** — institutional/legal requirements for storing biometric templates and any dispute snapshots.
4. **Geofence source of truth** for phone check-in — room/campus coordinates and acceptable radius.
5. **Do existing `PhotoImgID` photos meet quality** (frontal, recent, single face) for bulk enrollment, or is guided re-capture required?
