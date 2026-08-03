import { useCallback, useEffect, useRef, useState } from 'react'

// In-browser face detection for a real-time bounding box. MediaPipe's BlazeFace
// (short-range) runs on the video every animation frame and writes boxes to a ref,
// so the overlay tracks the face at camera framerate with no network round-trip.
// Identity/liveness still come from the backend (/api/recognize) at a slower cadence.
//
// The library + model load from a CDN at runtime (no student data is involved —
// this is a generic face-*detection* model, not recognition). If loading fails
// (offline, CDN blocked), status becomes 'error' and the caller falls back to
// drawing the backend's boxes. To self-host, vendor these three assets and point
// the URLs at your own origin.
const VISION_MJS = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.18/vision_bundle.mjs'
const WASM_ROOT = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.18/wasm'
const MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite'

const DETECT_INTERVAL_MS = 33   // ~30 fps detection (plenty smooth, lighter than 60)

export function useFaceDetection() {
  const detectorRef = useRef(null)
  const videoRefRef = useRef(null)     // holds the <video> ref object passed to start()
  const boxesRef = useRef([])          // [{x,y,w,h}] in video-intrinsic pixels, latest frame
  const rafRef = useRef(0)
  const activeRef = useRef(false)      // false once stopped — kills any in-flight/racing loop
  const lastTsRef = useRef(-1)
  const lastDetRef = useRef(0)
  const [status, setStatus] = useState('idle')   // idle|loading|ready|error

  const ensureLoaded = useCallback(async () => {
    if (detectorRef.current) return true
    setStatus('loading')
    try {
      const vision = await import(/* @vite-ignore */ VISION_MJS)
      const fileset = await vision.FilesetResolver.forVisionTasks(WASM_ROOT)
      detectorRef.current = await vision.FaceDetector.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MODEL_URL },
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

  const start = useCallback(async (videoRef) => {
    videoRefRef.current = videoRef
    activeRef.current = true
    const ok = await ensureLoaded()
    // If stop() ran while the model was loading, don't launch the loop.
    if (!ok || !activeRef.current) return false
    const loop = () => {
      if (!activeRef.current) return          // stopped — don't detect or reschedule
      const v = videoRefRef.current?.current
      const det = detectorRef.current
      const now = performance.now()
      if (det && v && v.readyState >= 2 && v.videoWidth && now - lastDetRef.current >= DETECT_INTERVAL_MS) {
        lastDetRef.current = now
        // detectForVideo needs strictly increasing timestamps.
        let ts = now
        if (ts <= lastTsRef.current) ts = lastTsRef.current + 1
        lastTsRef.current = ts
        try {
          const res = det.detectForVideo(v, ts)
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
