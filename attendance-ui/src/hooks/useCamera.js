import { useRef, useState, useCallback, useEffect } from 'react'

// Shared camera control: start/stop/flip + grab a JPEG blob of the current frame.
export function useCamera() {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const [active, setActive] = useState(false)
  const [facing, setFacing] = useState('user')
  const [error, setError] = useState('')

  const secure = typeof window !== 'undefined' &&
    (window.isSecureContext || ['localhost', '127.0.0.1'].includes(location.hostname))

  const stop = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
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

  // Grab a JPEG of the current frame. Pass `maxWidth` to downscale before encoding
  // — smaller frames upload and decode faster, which is what keeps live recognition
  // responsive. Omit it (enrollment) to keep full resolution/quality.
  const grabBlob = useCallback(async (maxWidth) => {
    const v = videoRef.current
    if (!v || !v.videoWidth) return null
    let w = v.videoWidth, h = v.videoHeight
    if (maxWidth && w > maxWidth) { const s = maxWidth / w; w = Math.round(w * s); h = Math.round(h * s) }
    const c = document.createElement('canvas')
    c.width = w; c.height = h
    c.getContext('2d').drawImage(v, 0, 0, w, h)
    return new Promise((res) => c.toBlob(res, 'image/jpeg', maxWidth ? 0.8 : 0.92))
  }, [])

  // Grab a JPEG of just a sub-rect of the frame (video-pixel coords), downscaled so
  // its width <= maxWidth. Live recognition sends the region around the detected
  // face(s) instead of the whole frame — smaller to encode/upload/decode, and it
  // trims empty background the model would otherwise scan.
  const grabRegion = useCallback(async (rect, maxWidth) => {
    const v = videoRef.current
    if (!v || !v.videoWidth) return null
    const sx = Math.max(0, Math.round(rect.x)), sy = Math.max(0, Math.round(rect.y))
    const sw = Math.min(v.videoWidth - sx, Math.round(rect.w))
    const sh = Math.min(v.videoHeight - sy, Math.round(rect.h))
    if (sw <= 0 || sh <= 0) return null
    let dw = sw, dh = sh
    if (maxWidth && dw > maxWidth) { const s = maxWidth / dw; dw = Math.round(dw * s); dh = Math.round(dh * s) }
    const c = document.createElement('canvas')
    c.width = dw; c.height = dh
    c.getContext('2d').drawImage(v, sx, sy, sw, sh, 0, 0, dw, dh)
    return new Promise((res) => c.toBlob(res, 'image/jpeg', 0.8))
  }, [])

  useEffect(() => stop, [stop])

  return { videoRef, active, facing, error, secure, start, stop, flip, grabBlob, grabRegion }
}
