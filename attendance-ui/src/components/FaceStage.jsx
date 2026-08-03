import { useEffect, useRef } from 'react'

// Overlay for the live camera. Boxes and labels come from two refs updated at
// different rates, so we read them in a persistent animation loop rather than via
// React state (which would thrash at framerate):
//
//   boxesRef  — real-time face boxes from in-browser detection (video-pixel coords,
//               ~30fps). This is what makes the box track the face with no lag.
//   labelsRef — identity from the backend (/api/recognize), a few times/sec, stored
//               NORMALIZED [{ nx,ny,nw,nh, ncx,ncy, recognized, name, accuracy, ts }].
//
// Each frame we draw the local boxes and attach the nearest fresh label by
// normalized centre. If in-browser detection isn't available (boxesRef empty),
// we fall back to drawing the backend's own boxes so the feature degrades safely.
const LABEL_TTL = 2500      // ms a backend label stays valid before it's ignored
const MATCH_DIST = 0.18     // max normalized centre distance to attach a label

const dist = (ax, ay, bx, by) => Math.hypot(ax - bx, ay - by)

export default function FaceStage({ videoRef, boxesRef, labelsRef, placeholder }) {
  const canvasRef = useRef(null)

  useEffect(() => {
    let raf = 0
    const draw = () => {
      const cv = canvasRef.current
      const v = videoRef?.current
      if (cv) {
        const W = (v && v.videoWidth) || 640
        const H = (v && v.videoHeight) || 480
        if (cv.width !== W) cv.width = W
        if (cv.height !== H) cv.height = H
        const ctx = cv.getContext('2d')
        ctx.clearRect(0, 0, W, H)
        ctx.lineWidth = Math.max(2, W / 240)
        ctx.font = `${Math.max(13, W / 42)}px system-ui`
        ctx.textBaseline = 'bottom'

        const now = performance.now()
        const labels = (labelsRef?.current || []).filter((l) => now - l.ts < LABEL_TTL)
        const local = boxesRef?.current || []

        // Prefer real-time local boxes; otherwise fall back to the backend's boxes.
        const boxes = local.length
          ? local.map((b) => ({ b, forced: null }))
          : labels.map((l) => ({ b: { x: l.nx * W, y: l.ny * H, w: l.nw * W, h: l.nh * H }, forced: l }))

        for (const { b, forced } of boxes) {
          const ncx = (b.x + b.w / 2) / W, ncy = (b.y + b.h / 2) / H
          let label = forced
          if (!label) {
            let bd = MATCH_DIST
            for (const l of labels) { const d = dist(ncx, ncy, l.ncx, l.ncy); if (d < bd) { bd = d; label = l } }
          }
          const known = label && label.recognized
          ctx.strokeStyle = known ? '#16a34a' : label ? '#dc2626' : '#6b7280'
          ctx.fillStyle = ctx.strokeStyle
          ctx.strokeRect(b.x, b.y, b.w, b.h)
          const text = label
            ? (known ? `${label.name} · ${label.accuracy}%` : `Unknown${label.accuracy ? ' · ' + label.accuracy + '%' : ''}`)
            : 'Detecting…'
          const tw = ctx.measureText(text).width + 12
          ctx.fillRect(b.x, Math.max(0, b.y - 24), tw, 22)
          ctx.fillStyle = '#fff'
          ctx.fillText(text, b.x + 6, Math.max(18, b.y - 4))
        }
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [videoRef, boxesRef, labelsRef])

  return (
    <div className="stage">
      {!videoRef && <div className="ph">{placeholder}</div>}
      {videoRef && <video ref={videoRef} autoPlay muted playsInline />}
      <canvas ref={canvasRef} className="ovl" />
    </div>
  )
}
