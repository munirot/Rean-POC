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
    setActive(false)
  }, [])

  const start = useCallback(async (face = facing) => {
    setError('')
    if (!secure) { setError('Live camera needs HTTPS or localhost. Use photo upload instead.'); return false }
    try {
      stop()
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: face }, audio: false })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
      }
      setFacing(face)
      setActive(true)
      return true
    } catch (e) {
      setError('Camera blocked: ' + e.message)
      return false
    }
  }, [facing, secure, stop])

  const flip = useCallback(() => start(facing === 'user' ? 'environment' : 'user'), [facing, start])

  const grabBlob = useCallback(async () => {
    const v = videoRef.current
    if (!v || !v.videoWidth) return null
    const c = document.createElement('canvas')
    c.width = v.videoWidth; c.height = v.videoHeight
    c.getContext('2d').drawImage(v, 0, 0, c.width, c.height)
    return new Promise((res) => c.toBlob(res, 'image/jpeg', 0.92))
  }, [])

  useEffect(() => stop, [stop])

  return { videoRef, active, facing, error, secure, start, stop, flip, grabBlob }
}
