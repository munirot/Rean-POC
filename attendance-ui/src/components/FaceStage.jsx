import { useEffect, useRef } from 'react'

// Renders a video (or still image) with a face-box overlay. `faces` come from
// /api/recognize; `srcW/srcH` are the source pixel dims for correct box scaling.
export default function FaceStage({ videoRef, imgSrc, faces = [], srcW, srcH, placeholder }) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const cv = canvasRef.current
    if (!cv) return
    const w = srcW || 640, h = srcH || 480
    cv.width = w; cv.height = h
    const ctx = cv.getContext('2d')
    ctx.clearRect(0, 0, w, h)
    ctx.lineWidth = Math.max(2, w / 240)
    ctx.font = `${Math.max(13, w / 42)}px system-ui`
    ctx.textBaseline = 'bottom'
    for (const f of faces) {
      const b = f.bbox, ok = f.recognized
      ctx.strokeStyle = ok ? '#16a34a' : '#dc2626'
      ctx.fillStyle = ctx.strokeStyle
      ctx.strokeRect(b.x, b.y, b.w, b.h)
      const label = ok ? `${f.name} · ${f.accuracy}%` : `Unknown${f.accuracy ? ' · ' + f.accuracy + '%' : ''}`
      const tw = ctx.measureText(label).width + 12
      ctx.fillRect(b.x, Math.max(0, b.y - 24), tw, 22)
      ctx.fillStyle = '#fff'
      ctx.fillText(label, b.x + 6, Math.max(18, b.y - 4))
    }
  }, [faces, srcW, srcH])

  return (
    <div className="stage">
      {!videoRef && !imgSrc && <div className="ph">{placeholder}</div>}
      {videoRef && <video ref={videoRef} autoPlay muted playsInline />}
      {imgSrc && <img src={imgSrc} alt="" />}
      <canvas ref={canvasRef} className="ovl" />
    </div>
  )
}
