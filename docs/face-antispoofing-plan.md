# Anti-Spoofing (Liveness) — Improve the Recognition Model

**Project:** Rean / mycamu — `face-service` (FastAPI + InsightFace)
**Problem:** The current model recognizes *who* a face belongs to but not *whether it is a live person*. A printed photo, or a face shown on a phone/laptop screen, is accepted as a real check-in.
**Goal:** Add a passive anti-spoofing (presentation-attack detection) layer so a "simple photo" is rejected, at both the kiosk and phone capture points — with no extra effort from the user.
**Status:** Draft for review · Date: 2026-07-21
**Companion to:** `docs/face-attendance-plan.md` (this fills in §8 "liveness" and Phase 4 "anti-spoof model").

---

## 1. Why this is needed

Today `POST /api/recognize` (`face-service/app/main.py`) does exactly three things per face: detect → embed (512-d ArcFace) → cosine-match against enrolled students. ArcFace is trained to be *robust* — it deliberately ignores lighting, print texture, and screen moiré so it can still recognize the same identity across conditions. That robustness is the problem: a photo of Sophea embeds almost identically to real Sophea, so the match succeeds. The README already flags this: *"POC has no liveness / anti-spoofing — a photo of a photo can pass."*

Recognition answers **"who is this?"**. Anti-spoofing answers a separate question: **"is this a live face in front of the camera, or a re-presentation (print / screen / mask)?"** These need a *different* model, run *before* we trust the identity match.

Attacks we must stop, cheapest first:

- **Print attack** — a printed photo or ID card held to the camera.
- **Screen / replay attack** — a face shown on a phone or laptop screen (still or short video).
- **Cutout / mask** (lower priority for v1) — paper cutout or crude mask.

## 2. Approach: passive silent anti-spoofing (chosen)

A **passive (silent) anti-spoofing model** classifies a single captured frame as `real` vs `spoof` from image cues alone — screen moiré and reflection, print texture and paper edges, lack of micro-depth, color/frequency artifacts. No blink/turn prompt, so it works identically for the supervised kiosk and the phone selfie, and adds only a few tens of milliseconds per frame.

Rationale versus the alternatives (see companion plan): active challenges (blink/turn) slow the flow and are beaten by replay video; depth/IR needs specific hardware and limits deployment. Passive PAD is the best coverage-per-effort default. The design leaves a clean seam to *add* an active challenge later for the highest-risk phone flow (§9).

### 2.1 Model choice

Primary recommendation: **MiniFASNet — the "Silent-Face-Anti-Spoofing" model (MiniVision)**, run under ONNX Runtime — the same runtime InsightFace already uses, so no new heavy dependency.

- Small (a few MB), CPU-real-time, permissive license, widely used as the open-source PAD baseline.
- Two-branch (2.7 + 4.0 scale) variant improves robustness; can start with a single branch for speed and add the second if accuracy needs it.
- Consumes a detector-provided crop, which we already have from RetinaFace, so it slots directly behind detection.

Fallback / upgrade options, in order:

1. **InsightFace's own anti-spoof** if a compatible packaged model is available in the installed version — least integration friction (already in the `insightface` package surface). Verify availability before committing; otherwise use MiniFASNet.
2. **DeepPixBiS / CDCN-family** if evaluation shows MiniFASNet's screen-replay recall is too low for the deployment cameras. Heavier, better on hard replay.
3. **Commercial SDK** (e.g. iBeta Level-2 certified) only if an audited/liability-grade guarantee is later required.

> Decision to confirm before build: lock the exact model weights + source repo and record the license and its provenance, since biometrics + third-party weights carry compliance weight (see companion §8).

## 3. Where it plugs in

The change is contained inside `face-service`; the Node monolith and the attendance write path are untouched (consistent with the companion plan's trust boundary).

```
POST /api/recognize (main.py)
  detect (RetinaFace)                     ← unchanged
    └─ for each face:
         [NEW] liveness = antispoof.score(img, face)   ← new step, BEFORE trusting identity
         embed (ArcFace 512-d)            ← unchanged
         best_match(emb, gallery, thr)    ← unchanged
         decision = recognized AND live   ← new combined gate
```

New module `face-service/app/antispoof.py`, mirroring the structure of `engine.py`:

```python
# antispoof.py  (sketch — validate against the chosen model's I/O)
class AntiSpoof:
    def __init__(self):
        import onnxruntime as ort
        self.sess = ort.InferenceSession(settings.antispoof_model_path,
                                         providers=settings.providers)

    def score(self, img, face) -> dict:
        crop = self._crop_with_margin(img, face.bbox)   # model-specific margin
        x = self._preprocess(crop)                       # resize + normalize per model card
        prob_real = float(self._infer(x))                # 0..1, higher = more live
        return {"live": prob_real >= settings.liveness_threshold,
                "score": round(prob_real, 4)}
```

A lazy singleton `get_antispoof()` parallels `get_engine()`; the model warms at startup in `main.py`'s `lifespan` right after the face engine.

### 3.1 `/api/recognize` change

Per detected face, add the liveness fields and make the accept decision require **both** identity match and liveness:

```python
for face in engine.detect(img):
    live = antispoof.score(img, face)               # NEW
    emb  = engine.embedding(face)
    m    = eng.best_match(emb, gallery, thr)
    recognized = m.get("recognized", False) and live["live"]   # combined gate
    faces_out.append({
        ...existing fields...,
        "live": live["live"],            # NEW
        "liveness_score": live["score"], # NEW
        "recognized": recognized,        # now spoof-gated
        "reason": ("spoof_suspected" if (m.get("recognized") and not live["live"])
                   else m.get("reason")),
    })
```

Key point: when identity matches but liveness fails, return `recognized=false` with `reason="spoof_suspected"` — distinct from `unknown`, so the UI/audit can tell a spoof attempt apart from a stranger.

### 3.2 `/api/enroll` change

Enrollment must also reject spoofed templates (someone enrolling from a photo of a target). Apply the *same* liveness gate on the largest face before calling `set_embedding`, returning a clear `message="Liveness check failed — capture a live face."` on failure. Enrollment can use a **stricter** threshold than recognition since it happens once and quality matters most.

### 3.3 Config additions (`config.py`)

```python
antispoof_enabled:   bool  = _get("ANTISPOOF_ENABLED", "true") == "true"
antispoof_model_path: str  = _get("ANTISPOOF_MODEL_PATH", "models/antispoof.onnx")
liveness_threshold:  float = float(_get("LIVENESS_THRESHOLD", "0.60"))   # recognize
enroll_liveness_threshold: float = float(_get("ENROLL_LIVENESS_THRESHOLD", "0.75"))
```

`ANTISPOOF_ENABLED=false` gives an instant kill-switch/rollback that reverts to today's behavior, and lets the model be shipped dark for A/B calibration.

## 4. Thresholds, scoring & the accuracy trade-off

Two independent scores now gate a check-in: **cosine similarity** (identity, existing `MATCH_THRESHOLD≈0.35`) and **liveness probability** (new, `LIVENESS_THRESHOLD`). Tune them separately.

- Anti-spoofing has its own error trade-off: **FAR** (spoof accepted as live — the attack we care about) versus **BPCER/FRR** (real person wrongly rejected — the annoyance). Bias toward **low spoof-accept**, but not so hard that real students get bounced, because a rejected real user falls back to manual attendance and erodes trust in the feature.
- Start at `LIVENESS_THRESHOLD ≈ 0.6`, then calibrate on a **local** labeled set (real check-ins + printed + on-screen samples from the *actual* kiosk cameras and common student phones). Report ROC and pick the point that meets the target spoof-accept rate. Lighting and camera differ per room, so re-tune per deployment.
- **Kiosk vs phone can differ.** A supervisor watches the kiosk, so it can run slightly looser (fewer false rejects); the unsupervised phone flow should run tighter. Support a per-source threshold via request param, defaulting from env.
- **Temporal smoothing (kiosk live camera):** the kiosk streams frames, so require liveness to pass on *N of the last M frames* before marking present. This kills single-frame flukes and makes a static held-up photo consistently fail. The phone single-shot uses the single-frame score plus quality gates.
- Keep the existing **ambiguity guard** idea from the companion plan (reject if top-1/top-2 identity scores are too close) — orthogonal to liveness, still worth adding.

## 5. Audit & telemetry

Extend the recognition audit (the companion plan's `mfaceattendancelog` / `face_attendance_audit`) so every attempt records `liveness: { passed, score, model_ver }` alongside the identity `score` and `decision`. Add `SPOOF_SUSPECTED` to the decision enum. This gives a tamper-evidence trail, lets us watch the real-world spoof-reject rate, and provides labeled data to recalibrate thresholds. Store a short-retention probe snapshot only for flagged spoof attempts, access-controlled, per the existing retention policy — do not keep images by default.

## 6. Testing & verification

- **Attack test set:** collect, from the real hardware, a labeled set — genuine faces, printed photos (matte + glossy), phone-screen replays, laptop-screen replays — and measure spoof-accept and live-reject rates. This is the acceptance gate, not a lab number from the model's paper.
- **Unit:** `antispoof.score` returns `live=false` on screen/print fixtures and `live=true` on genuine fixtures; `/api/recognize` sets `recognized=false, reason="spoof_suspected"` when identity matches but liveness fails.
- **Regression:** genuine recognition accuracy and latency stay within budget with anti-spoofing on (measure added ms/frame on the target CPU/GPU).
- **Kill-switch:** `ANTISPOOF_ENABLED=false` fully restores current behavior.
- **Metric to publish:** attack-presentation-accept rate before vs after, on the local test set.

## 7. Latency & deployment notes

MiniFASNet adds roughly tens of ms/frame on CPU on top of detection+embedding; the kiosk live loop is the tightest budget. If it lags, the companion plan's existing levers apply: `DEVICE=gpu`, or `MODEL_PACK=buffalo_s` for detection. Bake the anti-spoof ONNX weights into the image / mounted volume like the InsightFace pack; no browser CDN. The model stays behind the private-network trust boundary — clients never call it directly.

## 8. Phased rollout

**Phase A — Model spike (2–3 days).** Pick and download MiniFASNet (or confirm an InsightFace-packaged anti-spoof), stand up `antispoof.py`, verify inference on a handful of real + spoof images. Deliverable: a working `score()` and a go/no-go on accuracy.

**Phase B — Integrate, shipped dark (3–4 days).** Wire the gate into `/api/recognize` and `/api/enroll` behind `ANTISPOOF_ENABLED`, add config + audit fields, warm the model at startup. Log liveness scores but don't yet block, to gather live calibration data.

**Phase C — Calibrate & enable (2–3 days + data collection).** Build the local attack test set, tune `LIVENESS_THRESHOLD` per source (kiosk/phone), add temporal smoothing on the kiosk loop, then flip enforcement on. Surface `spoof_suspected` in the UI.

**Phase D — Harden (ongoing).** Watch spoof-reject telemetry, add the second MiniFASNet branch or upgrade to CDCN/DeepPixBiS if replay recall lags, and add an **active-challenge** step (blink/turn) for the phone flow as a second factor if proxy attendance persists.

## 9. Open questions to confirm before build

1. **Exact model + license** — lock MiniFASNet weights/source (or an InsightFace-packaged model) and record license/provenance for the biometric-compliance review.
2. **Target hardware** — CPU-only or GPU on the deployment host? Drives whether single- or two-branch MiniFASNet and how much temporal smoothing the kiosk can afford.
3. **Calibration data** — can we collect real + spoof samples from the actual kiosk cameras and representative student phones before enabling enforcement? Threshold quality depends on it.
4. **Kiosk vs phone strictness** — confirm we want separate thresholds (supervised looser, unsupervised tighter).
5. **Fail-open vs fail-closed** — if the anti-spoof model errors at runtime, do we mark present anyway (fail-open, availability) or refuse and fall back to manual (fail-closed, security)? Recommendation: fail-closed with a clear manual fallback, matching the companion plan's degrade behavior.

## 10. Files touched (summary)

**New:** `face-service/app/antispoof.py` (MiniFASNet ONNX wrapper), anti-spoof `.onnx` weights, a small `tests/` attack fixture set.

**Changed:** `face-service/app/main.py` (liveness step + combined gate in `/api/recognize` and `/api/enroll`; warm model in `lifespan`), `face-service/app/config.py` (anti-spoof settings), `face-service/app/schemas.py` (`live`, `liveness_score`, `reason="spoof_suspected"` fields), `requirements.txt` (only if a package beyond `onnxruntime` is needed), the recognition audit schema in the companion plan (`liveness` block + `SPOOF_SUSPECTED` decision).

**Unchanged:** ArcFace embedding + cosine matching, the attendance write path, the Node monolith, MongoDB student documents.
```
