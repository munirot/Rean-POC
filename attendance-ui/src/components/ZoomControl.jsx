import MatIcon from './MatIcon'

// Reusable camera zoom control (native sensor zoom where available, else the
// digital center-crop applied by useCamera). Pass `cam` from useCamera; `className`
// selects placement/theme (e.g. "cam-zoom" for an overlay on a light page).
export default function ZoomControl({ cam, className = '' }) {
  if (!cam.zoomCaps) return null
  const { min, max, step } = cam.zoomCaps
  const nudge = (max - min) / 10 || 0.2
  const clamp = (z) => Math.min(max, Math.max(min, z))
  return (
    <div className={`zoom-ctl ${className}`}>
      <MatIcon name="zoom_out" onClick={() => cam.setZoom(clamp(cam.zoom - nudge))} title="Zoom out" />
      <input type="range" className="form-range zoom-range flex-grow-1" min={min} max={max} step={step || 0.1}
        value={cam.zoom} onChange={(e) => cam.setZoom(Number(e.target.value))} aria-label="Zoom" />
      <MatIcon name="zoom_in" onClick={() => cam.setZoom(clamp(cam.zoom + nudge))} title="Zoom in" />
      <span className="zoom-val">{cam.zoom.toFixed(1)}×</span>
    </div>
  )
}
