# Rean · Face Attendance — Proof of Concept

A single-page, browser-only demo of the face-recognition attendance flow. It runs the
detection + recognition model **entirely in the browser** (no backend, no data leaves the
device) so we can validate the concept before building the production embedding service.

> This is a POC to prove the workflow and accuracy feel. Production will use a self-hosted
> InsightFace/ArcFace microservice (see `../docs/face-attendance-plan.md`), not this in-browser
> model.

## What it does

- Shows a roster of **sample students** — some flagged as already having a profile photo
  ("Profile expected"), some brand-new ("New student").
- **Enroll** a student's profile by uploading an image or capturing from the camera. This
  simulates the photo the school already holds. A 128-d face descriptor is computed and stored
  in memory.
- **Detect & match**: the teacher runs recognition from the **computer or phone camera** (live)
  or by capturing/uploading a photo. Each detected face is matched against enrolled students.
- **Returns the match + accuracy**: student name, similarity %, raw descriptor distance, and a
  present/unknown verdict, with an adjustable strictness threshold.

## Run it

The live camera needs a **secure context** (HTTPS or `localhost`). Opening the file directly
(`file://`) will block the webcam — use the tiny local server:

```bash
cd face-poc
./serve.sh              # → http://localhost:8000  (camera works)
```

Or manually: `python3 -m http.server 8000` then open `http://localhost:8000`.

The first load pulls the face models (~7 MB) from a CDN, so you need internet the first time.

## Test on a phone

Two options:

1. **Photo capture (no setup)** — on the phone, use the **"Capture / upload photo"** button.
   This opens the phone camera and returns a still image for recognition. Works over plain
   `http://<your-computer-ip>:8000` because it doesn't need the live-camera secure context.
2. **Live phone camera** — needs HTTPS. Expose localhost with the ngrok config already in the
   repo (`../ngrok.yml`), e.g. `ngrok http 8000`, then open the `https://…ngrok…` URL on the
   phone. The "Start camera" + "Flip camera" buttons will then work.

## How to demo

1. Wait for **"Models ready"** (top-right dot turns green).
2. Enroll 2–3 of the "Profile expected" students — click **Upload** and pick a clear,
   front-facing photo of each (or use **Camera**). Leave the "New student" ones empty.
3. Click **Start camera** (or **Capture / upload photo**) and point it at one of the enrolled
   faces → it should show the name + a high match %.
4. Try an un-enrolled face → it should report **Unknown**.
5. Slide **Match strictness** to see how the threshold changes accept/reject behavior.

## Notes & limitations

- In-browser model (face-api.js / TinyFaceDetector + FaceRecognitionNet, 128-d) is lighter and
  less accurate than the production ArcFace (512-d). Treat the accuracy here as indicative.
- No liveness / anti-spoofing in the POC — a photo of a photo can pass. That's handled in the
  production plan.
- Everything is in-memory: refreshing the page clears enrollments. Nothing is uploaded or saved.
