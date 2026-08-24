import { useEffect, useRef } from 'react'

// Live camera view for recognition. We render the "processed frame" (the raw video,
// or its digital-zoom center-crop) onto a canvas and draw identity boxes on the same
// canvas — so what the user sees, what the detector detects, and what we send to the
// server are all the same zoomed pixels, in one coordinate space.
//
//   boxesRef  — face boxes from in-browser detection, in PROCESSED-frame pixels.
//   labelsRef — identity from the backend (/api/recognize), NORMALIZED to the
//               processed frame [{ nx,ny,nw,nh, ncx,ncy, recognized, name, accuracy, ts }].
//   sourceRect() — the source-video crop that forms the processed frame (from useCamera).
//
// The <video> stays in the DOM (the stream sink + the source we sample) but is
// covered by the opaque canvas.
const LABEL_TTL = 2500      // ms a backend label stays valid before it's ignored
const MATCH_DIST = 0.18     // max normalized centre distance to attach a label
const SMOOTH = 0.35         // per-frame easing toward the latest detection (0..1; lower = smoother)

const dist = (ax, ay, bx, by) => Math.hypot(ax - bx, ay - by)
const lerp = (a, b, t) => a + (b - a) * t

// Ease each on-screen box toward its latest detection, matching boxes across frames
// by nearest centre. Detection runs at a low rate; this 60fps interpolation is what
// makes the box glide instead of jumping (same trick as eventkh's render loop).
function smoothBoxes(prev, targets) {
  const used = new Set()
  return targets.map((t) => {
    const tcx = t.x + t.w / 2, tcy = t.y + t.h / 2
    let bi = -1, bd = Infinity
    for (let i = 0; i < prev.length; i++) {
      if (used.has(i)) continue
      const p = prev[i]
      const d = dist(p.x + p.w / 2, p.y + p.h / 2, tcx, tcy)
      if (d < bd) { bd = d; bi = i }
    }
    // Ease toward the target when it's plainly the same face (near, relative to size);
    // otherwise snap — a new face or a big jump shouldn't slide across the frame.
    if (bi >= 0 && bd < Math.max(t.w, t.h) * 0.8) {
      used.add(bi)
      const p = prev[bi]
      return { x: lerp(p.x, t.x, SMOOTH), y: lerp(p.y, t.y, SMOOTH),
               w: lerp(p.w, t.w, SMOOTH), h: lerp(p.h, t.h, SMOOTH), forced: t.forced }
    }
    return { x: t.x, y: t.y, w: t.w, h: t.h, forced: t.forced }
  })
}

// Draw an L-shaped bracket at each corner of the box (a viewfinder look) instead
// of a full rectangle.
function drawCorners(ctx, x, y, w, h, color, lw) {
  const len = Math.max(14, Math.min(w, h) * 0.22)
  ctx.strokeStyle = color
  ctx.lineWidth = lw
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.beginPath()
  ctx.moveTo(x, y + len); ctx.lineTo(x, y); ctx.lineTo(x + len, y)                     // top-left
  ctx.moveTo(x + w - len, y); ctx.lineTo(x + w, y); ctx.lineTo(x + w, y + len)         // top-right
  ctx.moveTo(x + w, y + h - len); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w - len, y + h) // bottom-right
  ctx.moveTo(x + len, y + h); ctx.lineTo(x, y + h); ctx.lineTo(x, y + h - len)         // bottom-left
  ctx.stroke()
}

export default function FaceStage({ videoRef, boxesRef, labelsRef, placeholder, sourceRect, useServerBoxes = false, singleBox = false }) {
  const canvasRef = useRef(null)
  const drawnRef = useRef([])     // smoothed boxes currently on screen (eased toward detections)

  useEffect(() => {
    let raf = 0
    const draw = () => {
      const cv = canvasRef.current
      const v = videoRef?.current
      if (cv && v && v.videoWidth) {
        const W = v.videoWidth, H = v.videoHeight
        if (cv.width !== W) cv.width = W
        if (cv.height !== H) cv.height = H
        const ctx = cv.getContext('2d')

        // Paint the processed frame: the zoom crop (if any) upscaled to fill W×H.
        const src = sourceRect?.() || { sx: 0, sy: 0, sw: W, sh: H }
        try { ctx.drawImage(v, src.sx, src.sy, src.sw, src.sh, 0, 0, W, H) }
        catch { ctx.clearRect(0, 0, W, H) }

        const now = performance.now()
        const labels = (labelsRef?.current || []).filter((l) => now - l.ts < LABEL_TTL)
        let local = boxesRef?.current || []
        // Individual mode: only ever the nearest (largest) face.
        if (singleBox && local.length > 1) {
          local = [local.reduce((a, b) => (b.w * b.h > a.w * a.h ? b : a))]
        }

        // Box geometry always comes from the fast LOCAL detector when it sees a face,
        // so near faces track smoothly at detector rate.
        let targets = local.map((b) => ({ x: b.x, y: b.y, w: b.w, h: b.h, forced: null }))
        if (useServerBoxes) {
          // Group: bind each server identity to its NEAREST local box (one-to-one), so
          // the name rides the fresh, smoothly-tracked box — no ghost box when the face
          // moves fast and the ~1s-old server position lags behind. Server faces with no
          // local box left over are the genuinely far ones; those get their own box.
          const usedLabels = new Set()
          for (const t of targets) {
            const cx = (t.x + t.w / 2) / W, cy = (t.y + t.h / 2) / H
            let bi = -1, bd = Infinity
            labels.forEach((l, i) => {
              if (usedLabels.has(i)) return
              const d = dist(cx, cy, l.ncx, l.ncy)
              if (d < bd) { bd = d; bi = i }
            })
            if (bi >= 0) { usedLabels.add(bi); t.forced = labels[bi] }
          }
          const extra = labels
            .filter((_, i) => !usedLabels.has(i))
            .map((l) => ({ x: l.nx * W, y: l.ny * H, w: l.nw * W, h: l.nh * H, forced: l }))
          targets = targets.concat(extra)
        } else if (!local.length) {
          // No local detection available (CDN blocked) — fall back to server boxes.
          targets = labels.map((l) => ({ x: l.nx * W, y: l.ny * H, w: l.nw * W, h: l.nh * H, forced: l }))
        }

        // Ease the drawn boxes toward the latest detections (smooth at 60fps).
        const boxes = smoothBoxes(drawnRef.current, targets)
        drawnRef.current = boxes

        const lw = Math.max(3, W / 200)
        const fontSize = Math.max(15, W / 38)

        for (const b of boxes) {
          const forced = b.forced
          const ncx = (b.x + b.w / 2) / W, ncy = (b.y + b.h / 2) / H
          let label = forced
          if (!label) {
            let bd = MATCH_DIST
            for (const l of labels) { const d = dist(ncx, ncy, l.ncx, l.ncy); if (d < bd) { bd = d; label = l } }
          }
          const known = label && label.recognized
          const color = known ? '#16a34a' : label ? '#dc2626' : '#6b7280'

          // corner-bracket box
          drawCorners(ctx, b.x, b.y, b.w, b.h, color, lw)

          // name on a pill centred above the box
          const text = known ? label.name : label ? 'Unknown' : 'Detecting…'
          ctx.font = `600 ${fontSize}px system-ui`
          ctx.textAlign = 'center'
          ctx.textBaseline = 'middle'
          const padX = 12, padY = 7
          const bw = ctx.measureText(text).width + padX * 2
          const bh = fontSize + padY * 2
          let bx = b.x + b.w / 2 - bw / 2
          bx = Math.max(2, Math.min(bx, W - bw - 2))       // keep the pill on-screen
          const by = Math.max(2, b.y - bh - 8)             // above the top edge
          ctx.fillStyle = color
          if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 999); ctx.fill() }
          else ctx.fillRect(bx, by, bw, bh)
          ctx.fillStyle = '#fff'
          ctx.fillText(text, bx + bw / 2, by + bh / 2 + 1)
        }
        ctx.textAlign = 'start'                            // reset for any later draws
      } else if (cv) {
        cv.getContext('2d').clearRect(0, 0, cv.width, cv.height)
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [videoRef, boxesRef, labelsRef, sourceRect, useServerBoxes, singleBox])

  return (
    <div className="stage">
      {!videoRef && <div className="ph">{placeholder}</div>}
      {/* Kept in the DOM as the stream sink + sample source; the opaque canvas covers it. */}
      {videoRef && <video ref={videoRef} autoPlay muted playsInline />}
      <canvas ref={canvasRef} className="ovl" />
    </div>
  )
}
