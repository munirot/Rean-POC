# Whole-Class Camera Attendance — PoC Plan

**Project:** Rean / mycamu — `face-service` (FastAPI + InsightFace) + `attendance-ui`
**Problem:** Attendance is taken one student at a time (kiosk scan or self check-in). A 30-student class is 30 interactions, and the queue is the bottleneck — not the recognition.
**Goal:** A room camera observes the seated class and marks the students it confidently recognizes, leaving a short **teacher-confirmed exception list** for everyone else.
**Status:** Draft for review · Date: 2026-08-21
**Companion to:** `docs/face-attendance-plan.md`, `docs/face-antispoofing-plan.md`, `docs/face-matching-tuning.md`
**Governed by:** `docs/attendance-policy-plan.md` — the admin decides *per section* whether a
class uses individual scan or this whole-class scan, and confines capture to a defined period.

---

## 1. Goal & scope

**In scope (PoC):** one classroom, one camera, one section. Periodic frames over the
seating window → per-student evidence accumulation → auto-mark the confident ones →
teacher reviews the rest.

**Out of scope (PoC):** multi-room rollout, timetable-driven automation, live video
streaming to the server, identifying non-enrolled people, behaviour/engagement analytics.

**Explicit non-goal:** replacing the teacher's judgement on **absence**. See §2.

---

## 2. Key decisions (locked)

1. **Teacher-confirmed exceptions, not fully automatic.** The camera may only ever
   *add* confidence that someone is **present**. It must never mark a student
   **absent**. A student can sit through an entire class and never cleanly face the
   camera; treating non-detection as absence manufactures false absences and (given
   the dispute workflow now exists) a queue of disputes to match.

   Every class session therefore resolves into three buckets:

   | Bucket | Meaning | Action |
   |---|---|---|
   | **Confirmed present** | Cleared threshold + margin in ≥K frames | Auto-marked `P`, `source="class_camera"` |
   | **Not detected** | Never confidently recognized | Teacher confirms absent, or marks present |
   | **Ambiguous** | Recognized but below the margin guard, or conflicting | Teacher confirms with a thumbnail |

2. **Temporal aggregation is the core mechanism** (§3). No single frame needs to see
   the whole class.

3. **Recognition stays server-authoritative.** The camera sends images, never
   embeddings — same trust boundary as the existing pipeline.

4. **Gallery is scoped to the section roster**, reusing `gallery_matrix(scope)`.
   Recognition should never search students who cannot be in that room.

5. **Per-face liveness is disabled for this source** (§6.3). It is unreliable on
   small distant faces, and the supervised classroom is the wrong threat model.

6. **No classroom video is retained.** Frames are processed and discarded; only
   decisions, scores, and (optionally, short-lived) crops for ambiguous review.

---

## 3. Why temporal aggregation makes this work

Individual check-in gets **one shot at one face**. A room camera gets **minutes and
hundreds of frames of everyone**. That inverts the accuracy requirement.

You do not need a frame where all 30 faces are simultaneously detectable — a frame
like that may never exist (heads down, occlusion by the row in front, someone
turning to a neighbour). You need each student to be clearly visible **at least a few
times across the window**, which is nearly guaranteed over 10 minutes.

So the metric that matters is **coverage over time**, not per-frame recall:

```
for each frame every ~3-5s over the seating window:
    detect faces -> embed -> match against the section roster
    for each confident match: accumulator[sid].hits += 1, track best similarity
at close:
    sid is CONFIRMED PRESENT if hits >= CONFIRM_HITS (and margin held)
```

This is the same idea as the N-of-M vote gate already used for live kiosk marking
(`CONFIRM_MIN_HITS` in `TakeAttendance.jsx`), scaled from ~1 second to a class period
and moved server-side. Per-frame misses stop mattering; only *systematic* invisibility
(a student the camera never sees well) reaches the exception list.

---

## 4. What already exists (reused, not rebuilt)

The individual-check-in work already built most of the backbone:

| Capability | Where | Reuse |
|---|---|---|
| Multi-face per frame | `_recognize_sync` loops `engine.detect(img)`, no face cap | A 30-face frame already returns 30 results |
| Off-loop inference | `run_in_threadpool` (G1) | A heavy batch frame won't stall the API |
| Roster-scoped gallery | `Store.gallery_matrix(scope)` (G2) | Scope to section → smaller, safer search set |
| Ambiguity guard | `best_match_vec(..., margin)` (G5) | Look-alike classmates are the main false-accept risk here |
| Idempotent marking | `Store.mark_attendance` unique per `(StuID, date, session)` | Re-observation is a no-op; safe to run repeatedly |
| Roster view | `Store.attendance_roster` returns every student + `checkedIn`/`status`/`source` | The exception list, essentially free |
| Manual override | `Store.set_attendance` (staff-scoped) | Teacher resolution path |
| Disputes | `attendance_disputes` (Story 3) | Student recourse if wrongly not marked |
| Threshold/margin calibration | `eval/benchmark.py` | Extends to a class-coverage metric (§8) |

**Net new:** a session/accumulator model, a frame-ingest endpoint, tiled detection,
and a teacher review screen.

---

## 5. Architecture

```
[Room camera / edge device]
      │  full-resolution JPEG every ~3-5s  (device token, not a user session)
      ▼
POST /api/class-sessions/{id}/frame
      │
      ├─ tiled detect (§6.1)  ── SCRFD per overlapping tile → merge + NMS
      ├─ embed each face (ArcFace 512-d)
      ├─ best_match_vec(scope = section roster, margin)      ← existing
      └─ accumulate per sid: hits, bestSim, bestGap, firstSeen, lastSeen
      ▼
POST /api/class-sessions/{id}/close
      │
      ├─ hits >= CONFIRM_HITS → mark_attendance(source="class_camera")   [idempotent]
      └─ everyone else → exception list
      ▼
[Teacher review screen]  Present (auto) | Not detected | Ambiguous
      └─ one tap per exception → set_attendance
```

### 5.1 Trust boundary
The camera device authenticates with a **per-room device token** scoped to
`{InId, room}` — *not* a teacher's login. It may only POST frames to a session that
a staff member opened. It can never read the roster, read attendance, or mark anyone.
This keeps a physically-accessible device from becoming a credential.

---

## 6. The three hard problems

### 6.1 Detecting small, distant faces — the real bottleneck

This is the risk that decides feasibility. One wall-mounted camera puts back-row faces
at **20–60 px**. Today the recognize path detects at `DET_SIZE=640` and the live client
even downscales to a 640px JPEG — a back-row face lands at ~15–20 px, where SCRFD
misses it outright and ArcFace's 112px alignment produces a mushy, low-similarity
embedding.

Mitigations in order of leverage:

1. **Capture at full resolution** (1080p minimum, 4K preferred). Never route class
   frames through the 640px live path. Recognition quality tracks *original* face pixels.
2. **Tiled detection** — split the frame into overlapping tiles (e.g. 3×2 with ~15%
   overlap), run detection per tile at normal `det_size`, map boxes back to full-frame
   coordinates, and merge duplicates with IoU-based NMS. This is the standard
   crowd-face technique and recovers small faces without an enormous `det_size`.
3. **Camera placement** — front-of-room, slightly elevated, angled down. A back-wall
   wide shot maximizes distance *and* occlusion. Two cameras for a wide/deep room.
4. **GPU** — `DEVICE=cuda` (or `coreml`) is already supported. Tiling × 30 faces on CPU
   is seconds per frame; acceptable at a 3–5s cadence, comfortable on GPU.

> **Phase 0 measures exactly this** before any product code is written (§8).

### 6.2 Non-detection ≠ absent
Resolved by decision §2.1 — the three-bucket model with a teacher-confirmed exception
list. No further mitigation needed, but it must not be "optimized away" later.

### 6.3 Liveness in a supervised room
The passive anti-spoof scorer is calibrated for a close-up kiosk/phone crop. On a
20–60px distant face its cues (micro-texture, moiré, specular ratio) are noise, and
`_liveness` would either reject real students or return `assessed=False` en masse.

**Decision:** disable per-face liveness for `source="class_camera"`. The threat it
defends against — holding up a photo — requires doing so **in front of the teacher and
the whole class**, which supervision handles better than a 30px texture analysis. This
is a deliberate, documented trade, not an oversight.

---

## 7. Data model & API

### 7.1 New collections

`class_sessions`
```
{ _id, InId, CrID, SecID, SubID, date, session,      // links to the attendance row shape
  camera, state: "open"|"closed", startedBy, startedAt, closedAt,
  frames: int, stats: { detected, confirmed, ambiguous } }
```

`class_observations` — one doc per (session, student), atomically `$inc`-ed per frame
```
{ sessionId, StuID, hits, bestSim, bestGap, firstSeen, lastSeen, thumb? }
```
Index: `{sessionId: 1, StuID: 1}` unique; `{sessionId: 1, hits: -1}` for the review view.

Keeping observations in their own collection (rather than a growing subdocument) keeps
frame ingest a single atomic upsert and the review query trivially indexable.

### 7.2 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/class-sessions` | staff | Open a session for a class/period |
| `POST` | `/api/class-sessions/{id}/frame` | device token | Ingest one frame; detect → match → accumulate |
| `GET` | `/api/class-sessions/{id}` | staff | Live tallies + buckets for the review UI |
| `POST` | `/api/class-sessions/{id}/close` | staff | Finalize: mark confirmed, return exceptions |

Resolution of exceptions reuses the existing `PUT /api/attendance` (`set_attendance`).

### 7.3 New settings
```
CLASS_CAM_ENABLED=false          # master switch, off by default
CLASS_CAM_CONFIRM_HITS=3         # frames a student must clear to be auto-marked
CLASS_CAM_FRAME_INTERVAL=4       # seconds between frames (device-side)
CLASS_CAM_TILES=3x2              # tiled-detection grid
CLASS_CAM_TILE_OVERLAP=0.15
CLASS_CAM_LIVENESS=false         # see §6.3
```

---

## 8. Phase 0 — de-risk before building (the gate)

**Nothing above gets built until this passes.** It is an offline measurement, not product code.

1. Mount one camera in a real classroom at the intended geometry.
2. Capture ~10 minutes of full-resolution frames of a seated class whose students are
   already enrolled, plus a ground-truth attendance list taken manually.
3. Run tiled detect → embed → `best_match_vec(scope=section, margin)` over the frames
   and accumulate hits. **The script exists: `face-service/eval/class_coverage.py`.**

   ```bash
   cd face-service
   # dry-run the metrics with no camera and no model
   .venv/bin/python -m eval.class_coverage --synthetic 30

   # the real measurement
   .venv/bin/python -m eval.class_coverage \
       --frames ./class_frames --truth ./truth.csv \
       --interval 4 --course CR003 --confirm-hits 3 --compare-tiling
   ```

   `truth.csv` is the manually-taken register, one student per line, with an
   optional group for the placement breakdown:

   ```
   2301,front
   2302,middle
   2317,back
   ```

   It writes nothing and marks no attendance — it only reads frames and the
   enrolled gallery, then prints the verdict below plus a JSON report.
4. Report:
   - **Coverage** — % of actually-present students confirmed within the window
     (measured at 1, 3, 5, 10 minutes, so we learn how long the window must be).
   - **Precision** — wrong-student auto-marks (must be ~0).
   - **Coverage by row/distance** — tells us whether it's a placement or a model problem.
   - Effect of tiling on/off, and of `CONFIRM_HITS`.

### Success / kill criteria

| Outcome | Interpretation | Action |
|---|---|---|
| Coverage ≥ 90%, 0 wrong marks | Exception list is 2–3 students; teacher's job is trivial | **Build it** |
| Coverage 70–90% | Real savings but a meaningful review burden | Improve placement/tiling/resolution, re-measure |
| Coverage < 70% | Teacher hand-confirms a third of the class | **Stop** — no better than manual marking |
| Any wrong-student marks | Silent attendance corruption | Raise `MATCH_MARGIN`/`CONFIRM_HITS` and re-measure before proceeding |

---

## 9. Phased roadmap (after Phase 0 passes)

| Phase | Deliverable |
|---|---|
| **1. Session + ingest** | `class_sessions`/`class_observations`, the four endpoints, tiled detection in `engine.py`, accumulation. Manual trigger via API; no UI. Tests for accumulation + confirmation logic (fake store, as with disputes/gallery scope). |
| **2. Teacher review UI** | New page: Present (auto) / Not detected / Ambiguous, with thumbnails for ambiguous and one-tap resolution via `set_attendance`. Reuses `attendance_roster`. |
| **3. Capture device** | Edge device (Pi + camera or IP/RTSP puller) posting frames with a device token; retry/backoff; health surface. |
| **4. Automation** | ~~Timetable-driven session open/close~~ (**deferred, see below**), multi-camera rooms ✅, per-room calibration ✅. |

### 4a. Why timetable automation is deferred

Multi-camera rooms and per-room calibration are built. Timetable-driven open/close
is not, and should not be built here yet.

`TIMETBL` is a **menu code only** — it appears in `roles.json` and each student's
`access_control.menus`, and nowhere else. There is no schedule collection: no
mapping of section → room → weekday → period → staff, no term dates, no holiday
calendar. Driving sessions from a timetable therefore means *inventing* one.

That is the wrong place to invent it. A class schedule is a system-of-record
concern for the campus platform (MyCAMU) — it is authored by a registrar, it
changes with room bookings and staff cover, and every other module (exams,
billing, reporting) needs the same truth. A schedule modelled inside face-service
would immediately be a second, diverging copy, and the first timetable change
nobody propagated would silently open sittings for the wrong room.

**The unlock is data, not code.** Once a real timetable exists — imported from the
campus system into a collection with `{InId, CrID, SecID, room, weekday, period}` —
the automation itself is small: a scheduled job opening sittings at period start
and closing them at period end, reusing `open_class_session` / `close_class_session`
unchanged. Both are already idempotent, which is exactly what a scheduler needs.

Until then the teacher tap is the trigger, and the capture period from
`attendance-policy-plan.md` already bounds when a sitting may mark anyone.

Phases 1–2 alone deliver the product value; 3–4 remove the manual trigger.

---

## 10. Privacy & governance (must clear before pilot)

A continuously-watching classroom camera is a materially larger regulatory surface
than opt-in kiosk enrollment, and should not be piloted on the strength of the
existing consent alone:

- **Consent & notice** — explicit biometric-processing consent covering *classroom
  observation*, plus visible signage in the room.
- **Data minimisation** — process frames in memory, persist **no classroom video**;
  crops only for ambiguous review, with a short retention (e.g. 24h) and automatic purge.
- **Purpose limitation** — attendance only. No engagement/behaviour inference. Worth
  stating in the doc *and* enforcing by not storing what would enable it.
- **Audit** — who opened a session, which students were auto-marked, and every teacher
  override, all queryable. The `source="class_camera"` tag on attendance rows makes
  camera-originated marks separable from manual ones after the fact.
- **Opt-out path** — a student who declines classroom biometric processing must still
  have a working attendance route (manual/kiosk), without penalty.

These are also the questions an institution will ask during procurement, so answering
them early doubles as sales readiness.

---

## 11. New/changed files (projected)

```
face-service/app/config.py         CLASS_CAM_* settings
face-service/app/db.py             class_sessions + class_observations, accumulate/confirm
face-service/app/engine.py         tiled detection (detect_tiled) + box merge/NMS
face-service/app/main.py           4 new endpoints, device-token auth dependency
face-service/app/schemas.py        session/observation/review models
face-service/eval/class_coverage.py  Phase 0 measurement script
face-service/tests/test_class_session.py  accumulation + confirmation + scoping tests
attendance-ui/src/pages/ClassScan.jsx     teacher review screen
attendance-ui/src/api.js           class-session helpers
docs/class-camera-attendance-plan.md      this document
```

---

## 12. Open questions to confirm before build

1. **Room geometry** — typical class size, room depth, existing camera/mounting options?
   This drives the entire Phase 0 result.
2. **Enrollment coverage** — whole-class attendance only works for *enrolled* students.
   What fraction of a section currently has a face profile? Un-enrolled students are
   permanent exception-list entries.
3. **Device & network** — IP cameras already in rooms, or new edge devices? Is the
   server reachable from the classroom network?
4. **Who opens the session** — teacher tap (Phase 1–2) is simplest; timetable
   automation (Phase 4) needs the unbuilt `TIMETBL` data to be real.
   *(Partly answered: the capture period from `attendance-policy-plan.md` bounds the
   session window; the teacher tap remains the trigger inside it for the PoC.)*
5. ~~**Late arrivals**~~ — **answered** in `attendance-policy-plan.md` §5.3: inside the
   period → `P`, inside grace → `L`, after → rejected.
6. **Legal sign-off owner** — who approves classroom biometric processing for the
   institution, and under what consent instrument?
