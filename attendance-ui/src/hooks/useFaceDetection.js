import { useCallback, useEffect, useRef, useState } from 'react'

// In-browser face detection for a lightweight presence signal. MediaPipe's
// BlazeFace (short-range) runs on the video at a LOW cadence and writes boxes to a
// ref — just often enough to notice a face has arrived and is steady. It no longer
// drives per-frame recognition: identity comes from a single freeze-frame sent to
// the backend once a face is stable (see TakeAttendance).
//
// The library + model load from a CDN at runtime (no student data is involved —
// this is a generic face-*detection* model, not recognition). If loading fails
// (offline, CDN blocked), status becomes 'error' and the caller falls back to
// drawing the backend's boxes. To self-host, vendor these three assets and point
// the URLs at your own origin.
const VISION_MJS = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.18/vision_bundle.mjs'
const WASM_ROOT = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.18/wasm'
// short_range: fast, tuned for a near face (≤~2m). full_range: heavier, detects far
// faces too (≤~5m) — so a distant face is tracked LOCALLY at frame-rate instead of
// only by the slower server pass. Pick per use via the `model` option.
const MODEL_URLS = {
  short: 'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite',
  full: 'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_full_range/float16/1/blaze_face_full_range.tflite',
}

// Default cadence: ~4 checks/sec is enough to spot a face and confirm it's steady,
// at a fraction of the CPU the old 30fps loop drew. Override via the option arg.
const DEFAULT_INTERVAL_MS = 250

export function useFaceDetection({ intervalMs = DEFAULT_INTERVAL_MS, model = 'short' } = {}) {
  const detectorRef = useRef(null)
  const videoRefRef = useRef(null)     // holds the <video> ref object passed to start()
  const boxesRef = useRef([])          // [{x,y,w,h}] in video-intrinsic pixels, latest frame
  const rafRef = useRef(0)
  const activeRef = useRef(false)      // false once stopped — kills any in-flight/racing loop
  const lastTsRef = useRef(-1)
  const lastDetRef = useRef(0)
  const intervalRef = useRef(intervalMs)
  const srcRectRef = useRef(null)      // () => {sx,sy,sw,sh} of the processed frame
  const procRef = useRef(null)         // offscreen canvas holding the cropped frame
  const [status, setStatus] = useState('idle')   // idle|loading|ready|error

  useEffect(() => { intervalRef.current = intervalMs }, [intervalMs])

  const ensureLoaded = useCallback(async () => {
    if (detectorRef.current) return true
    setStatus('loading')
    try {
      const vision = await import(/* @vite-ignore */ VISION_MJS)
      const fileset = await vision.FilesetResolver.forVisionTasks(WASM_ROOT)
      detectorRef.current = await vision.FaceDetector.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MODEL_URLS[model] || MODEL_URLS.short },
        runningMode: 'VIDEO',
        minDetectionConfidence: 0.5,
      })
      setStatus('ready')
      return true
    } catch (e) {
      console.error('[facedet] load failed; falling back to server boxes:', e)
      setStatus('error')
      return false
    }
  }, [])

  // `getSourceRect` (optional) returns the processed-frame crop {sx,sy,sw,sh}. When
  // it describes less than the whole frame (digital zoom), we detect on the cropped
  // + upscaled frame so a distant/zoomed face is actually large enough to detect.
  // Boxes come back in processed-frame coords (0..videoWidth), matching the preview.
  const start = useCallback(async (videoRef, getSourceRect) => {
    videoRefRef.current = videoRef
    srcRectRef.current = getSourceRect || null
    activeRef.current = true
    const ok = await ensureLoaded()
    // If stop() ran while the model was loading, don't launch the loop.
    if (!ok || !activeRef.current) return false
    const loop = () => {
      if (!activeRef.current) return          // stopped — don't detect or reschedule
      const v = videoRefRef.current?.current
      const det = detectorRef.current
      const now = performance.now()
      if (det && v && v.readyState >= 2 && v.videoWidth && now - lastDetRef.current >= intervalRef.current) {
        lastDetRef.current = now
        // detectForVideo needs strictly increasing timestamps.
        let ts = now
        if (ts <= lastTsRef.current) ts = lastTsRef.current + 1
        lastTsRef.current = ts
        // Choose the detector input: the raw video, or a canvas holding the zoom crop.
        let input = v
        const W = v.videoWidth, H = v.videoHeight
        const src = srcRectRef.current?.()
        if (src && (src.sw < W || src.sh < H)) {
          const cv = procRef.current || (procRef.current = document.createElement('canvas'))
          if (cv.width !== W) cv.width = W
          if (cv.height !== H) cv.height = H
          cv.getContext('2d').drawImage(v, src.sx, src.sy, src.sw, src.sh, 0, 0, W, H)
          input = cv
        }
        try {
          const res = det.detectForVideo(input, ts)
          boxesRef.current = (res?.detections || []).map((d) => {
            const b = d.boundingBox
            return { x: b.originX, y: b.originY, w: b.width, h: b.height }
          })
        } catch { /* transient frame error — keep last boxes */ }
      }
      rafRef.current = requestAnimationFrame(loop)
    }
    cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(loop)
    return true
  }, [ensureLoaded])

  const stop = useCallback(() => {
    activeRef.current = false
    cancelAnimationFrame(rafRef.current); rafRef.current = 0
    boxesRef.current = []
    videoRefRef.current = null
  }, [])

  useEffect(() => () => {
    activeRef.current = false
    cancelAnimationFrame(rafRef.current)
    try { detectorRef.current?.close() } catch { /* noop */ }
    detectorRef.current = null
  }, [])

  return { start, stop, boxesRef, status }
}
