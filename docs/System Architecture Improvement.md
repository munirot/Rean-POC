# System Architecture Improvement — original concerns, and what happened

The five notes this file started as were the early architecture worries about the
face pipeline. All five are now resolved; this records the answer to each so the
reasoning isn't lost, and so nobody re-opens a settled question.

---

### 1. "Integrate faceID at enrollment instead of just image upload, like Apple TrueDepth"

**Done, minus the depth sensor.** Enrollment no longer accepts a single uploaded
photo. `GuidedEnroll` walks the student through a pose sequence (centre → left →
right) in front of the live camera, and every frame is re-validated **server-side**
— face present, detector quality, passive liveness, and the measured yaw actually
matching the requested pose. The accepted captures must also span a real yaw range,
which is the cross-frame liveness signal: a flat photo or a screen cannot present
correctly-shaped left *and* right profiles.

Each angle is stored as its own embedding, so a turned face still matches later.

This is motion/pseudo-3D liveness, **not** structured-light depth. TrueDepth
projects thousands of IR dots and reconstructs geometry; we infer from parallax
across frames. Comparable intent, weaker guarantee — worth being precise about when
this is described to an institution.
See `docs/face-antispoofing-plan.md`, `app/main.py` (`/api/face/pose`, `/enroll`).

### 2. "Optimize real-time recognition — more efficient embedding model, GPU"

**Done, but the bottleneck was not the model.** Measured: matching 100k gallery
vectors takes ~4ms, against 50–200ms for detection. Swapping the embedding model
would have optimised ~3% of the pipeline.

What actually moved the needle:
- **Vectorized matching** — one BLAS `mat @ emb` over the whole gallery instead of a
  per-row loop, plus a cached gallery invalidated on enrollment change.
- **Off-loop inference** — CV work runs in a threadpool, so one recognition no longer
  stalls every other request (health, chat, other kiosks).
- **Scoped gallery** — recognition searches only the caller's institute/courses.
- **GPU/CoreML** — supported via `DEVICE=cuda|coreml`; see `docs/gpu-acceleration.md`.

`MODEL_PACK` still switches `buffalo_l`↔`buffalo_s` if a smaller model is ever wanted;
embeddings are model-specific, so changing it requires re-enrollment (`embVer` tracks it).

### 3. "Detect on the frontend, recognize on the backend, so the stream doesn't lag"

**Done, and the lag had two causes, not one.** The browser runs BlazeFace at ~30fps
purely to draw the box, so the overlay tracks the face with no network round-trip;
identity still comes from the server, which stays authoritative.

Beyond that:
- The client now sends **only the cropped region** around the detected face(s), not
  the whole frame.
- It sends **nothing at all** when no face is in view — an empty room used to cost a
  full detection pass every 500ms.

> **Never** move ArcFace itself into the browser to "just send the embedding". A
> client that can post a vector can post *any* vector and match anyone. Recognition
> must stay server-side; the efficiency win is the crop, not the embedding.

### 4. "Check if the vector encodes only the face or the whole image"

**Only the face.** InsightFace aligns each detection to a 112×112 crop using the
five facial landmarks before the recognition model runs, so the 512-d vector encodes
the aligned face — not the background, not the clothing, not the rest of the frame.
That is also why *original* face pixels matter: a 20px back-row face is upsampled to
112px and embeds mushily. Nothing to change here.

### 5. "For the chatbot, check whether each chat is one session"

**Yes — threads are explicit.** Every turn is stored with a `conversationId`;
`/api/chat/conversations` lists threads and `/api/chat/history?conversationId=` reads
one. History is keyed to the caller's `loginId`, so a user only ever sees their own.

Each turn replays recent history within a **token budget derived from the model's
real context window**, rather than a fixed number of messages — so memory can't
silently overrun a small self-hosted window. That trap, and how to size it, is
documented in `docs/chat-model.md`.

---

## Still open

- **Anti-spoofing strength.** The passive scorer ships as a classical-CV baseline —
  a functional scaffold, not a strong detector. Install a MiniFASNet ONNX model and
  calibrate with `python -m eval.antispoof_bench` before relying on it.
- **Threshold calibration on real photos.** `MATCH_THRESHOLD` / `MATCH_MARGIN` still
  sit at defaults; `python -m eval.benchmark --data <dir>` recommends both from your
  own data. See `docs/face-matching-tuning.md`.
- **Per-worker gallery memory.** Each uvicorn worker holds its own copy of the
  gallery. At ~33k students that is ~205MB × workers — the first scaling pressure
  that will actually bite, well before matching latency does.
- **Embedding-version migration.** Changing `MODEL_PACK` silently compares
  incompatible vectors; there is no guard that excludes mismatched `embVer` rows
  from the gallery or reports who needs re-enrollment.
