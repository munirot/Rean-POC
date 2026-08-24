import { useRef, useState, useCallback, useEffect } from 'react'

// Shared camera control: start/stop/flip + grab a JPEG blob of the current frame.
// Digital-zoom fallback range, used when the camera exposes no native `zoom`
// capability (most laptop webcams). 1× = no zoom; 4× center-crop keeps enough
// pixels for the detector while letting a distant face fill the frame.
const DIGITAL_ZOOM = { min: 1, max: 4, step: 0.1, native: false }

export function useCamera() {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const trackRef = useRef(null)
  const [active, setActive] = useState(false)
  const [facing, setFacing] = useState('user')
  const [error, setError] = useState('')
  // Zoom. `zoomCaps` describes the live control (native sensor zoom when the
  // track supports it, else a digital center-crop). `zoom` is the current level.
  const [zoom, setZoomState] = useState(1)
  const [zoomCaps, setZoomCaps] = useState(null)
  // Refs mirror the zoom state so the memoized grab* callbacks (deps: []) read
  // the current value without being re-created on every zoom change.
  const zoomRef = useRef(1)
  const digitalRef = useRef(false)

  const secure = typeof window !== 'undefined' &&
    (window.isSecureContext || ['localhost', '127.0.0.1'].includes(location.hostname))

  const stop = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
    trackRef.current = null
    // Release the last frame so nothing (e.g. an in-browser detector) keeps
    // reading a frozen image after the camera is stopped.
    if (videoRef.current) videoRef.current.srcObject = null
    setActive(false)
  }, [])

  const start = useCallback(async (face = facing) => {
    setError('')
    if (!secure) { setError('Live camera needs HTTPS or localhost. Use photo upload instead.'); return false }
    try {
      stop()
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: face }, audio: false })
      streamRef.current = stream
      // Probe the video track for a native `zoom` capability. Phone back-cameras
      // usually expose real sensor zoom; laptop webcams don't, so we fall back to
      // a digital center-crop (applied in grab* below).
      const track = stream.getVideoTracks()[0]
      trackRef.current = track
      const caps = track?.getCapabilities?.() || {}
      if (caps.zoom && caps.zoom.max > (caps.zoom.min ?? 1)) {
        const z = caps.zoom
        setZoomCaps({ min: z.min ?? 1, max: z.max, step: z.step || 0.1, native: true })
        const cur = track.getSettings?.().zoom ?? z.min ?? 1
        setZoomState(cur); zoomRef.current = cur; digitalRef.current = false
      } else {
        setZoomCaps(DIGITAL_ZOOM)
        setZoomState(1); zoomRef.current = 1; digitalRef.current = true
      }
      // If the <video> is already mounted (e.g. on flip) attach now; otherwise
      // the effect below attaches it once the element mounts (active -> render).
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        videoRef.current.play().catch(() => {})
      }
      setFacing(face)
      setActive(true)
      return true
    } catch (e) {
      setError('Camera blocked: ' + e.message)
      return false
    }
  }, [facing, secure, stop])

  // Attach the stream once the video element actually exists. This covers the
  // common case where the <video> is only rendered after `active` becomes true,
  // so it isn't in the DOM yet when start() runs.
  useEffect(() => {
    const v = videoRef.current
    if (active && v && streamRef.current && v.srcObject !== streamRef.current) {
      v.srcObject = streamRef.current
      v.play().catch(() => {})
    }
  }, [active])

  const flip = useCallback(() => start(facing === 'user' ? 'environment' : 'user'), [facing, start])

  // Set the zoom level. Native zoom magnifies the sensor output directly (best
  // quality); digital zoom just records the target level and the grab* helpers
  // center-crop the frame to it. Clamped to the live capability range.
  const setZoom = useCallback((z) => {
    const caps = zoomCaps
    if (!caps) return
    const v = Math.min(caps.max, Math.max(caps.min, Number(z) || caps.min))
    setZoomState(v); zoomRef.current = v
    if (caps.native && trackRef.current) {
      trackRef.current.applyConstraints({ advanced: [{ zoom: v }] }).catch(() => {})
    }
  }, [zoomCaps])

  // Center-crop rect for the current digital zoom, in source-video pixels.
  // Returns null when there's nothing to crop (native zoom, or 1×), so callers
  // fall back to the full frame.
  const zoomCrop = (v) => {
    if (!digitalRef.current || zoomRef.current <= 1) return null
    const sw = v.videoWidth / zoomRef.current, sh = v.videoHeight / zoomRef.current
    return { sx: (v.videoWidth - sw) / 2, sy: (v.videoHeight - sh) / 2, sw, sh }
  }

  // The source-video sub-rect that makes up the current "processed frame" — the
  // digital-zoom center-crop, or the whole frame otherwise. Detection, the live
  // preview and the capture all sample this SAME rect, so a digitally-zoomed face
  // is genuinely larger to the detector and the server, not just on screen.
  const sourceRect = useCallback(() => {
    const v = videoRef.current
    if (!v || !v.videoWidth) return null
    return zoomCrop(v) || { sx: 0, sy: 0, sw: v.videoWidth, sh: v.videoHeight }
  }, [])

  // Grab a JPEG of the current frame. Pass `maxWidth` to downscale before encoding
  // — smaller frames upload and decode faster, which is what keeps live recognition
  // responsive. Omit it (enrollment) to keep full resolution/quality.
  const grabBlob = useCallback(async (maxWidth) => {
    const v = videoRef.current
    if (!v || !v.videoWidth) return null
    // Digital zoom: sample a centered sub-rect and upscale it to fill the output,
    // so the encoded frame is zoomed (the detector sees an enlarged face), not
    // just the on-screen preview. Native zoom / 1× samples the whole frame.
    const crop = zoomCrop(v)
    const sx = crop ? crop.sx : 0, sy = crop ? crop.sy : 0
    const sw = crop ? crop.sw : v.videoWidth, sh = crop ? crop.sh : v.videoHeight
    let w = v.videoWidth, h = v.videoHeight
    if (maxWidth && w > maxWidth) { const s = maxWidth / w; w = Math.round(w * s); h = Math.round(h * s) }
    const c = document.createElement('canvas')
    c.width = w; c.height = h
    c.getContext('2d').drawImage(v, sx, sy, sw, sh, 0, 0, w, h)
    return new Promise((res) => c.toBlob(res, 'image/jpeg', maxWidth ? 0.8 : 0.92))
  }, [])

  // Grab a JPEG of a sub-rect of the PROCESSED frame, downscaled so its width <=
  // maxWidth. `rect` is in processed-frame coords (same space the detector's boxes
  // are in) sized to the full video; we map it back through the zoom crop to the
  // real source pixels. So recognition receives the zoomed-in face, matching what
  // the detector saw and the user sees.
  const grabRegion = useCallback(async (rect, maxWidth) => {
    const v = videoRef.current
    if (!v || !v.videoWidth) return null
    const src = sourceRect()                          // processed frame = this source rect, scaled to Vw×Vh
    const kx = src.sw / v.videoWidth, ky = src.sh / v.videoHeight   // processed px -> source px
    const rx = Math.max(0, rect.x), ry = Math.max(0, rect.y)
    const rw = Math.min(rect.w, v.videoWidth - rx), rh = Math.min(rect.h, v.videoHeight - ry)
    const sx = src.sx + rx * kx, sy = src.sy + ry * ky
    const sw = rw * kx, sh = rh * ky
    if (sw <= 0 || sh <= 0) return null
    let dw = rw, dh = rh                               // output keeps the (enlarged) processed size
    if (maxWidth && dw > maxWidth) { const s = maxWidth / dw; dw = Math.round(dw * s); dh = Math.round(dh * s) }
    const c = document.createElement('canvas')
    c.width = Math.max(1, Math.round(dw)); c.height = Math.max(1, Math.round(dh))
    c.getContext('2d').drawImage(v, sx, sy, sw, sh, 0, 0, c.width, c.height)
    return new Promise((res) => c.toBlob(res, 'image/jpeg', 0.8))
  }, [sourceRect])

  useEffect(() => stop, [stop])

  return { videoRef, active, facing, error, secure, start, stop, flip, grabBlob, grabRegion,
           sourceRect, zoom, zoomCaps, setZoom }
}
