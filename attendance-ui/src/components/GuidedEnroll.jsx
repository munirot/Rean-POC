import { useEffect, useRef, useState } from 'react'
import MatIcon from './MatIcon'
import { useCamera } from '../hooks/useCamera'
import { api } from '../api'

// Apple-FaceID-style guided enrollment. The student's face sits in a circular
// frame; a glowing ring around it fills as they slowly move their head left,
// center, and right. Every frame is scored by the backend for head pose, face
// coverage and liveness; a real left/right turn (which a flat photo or screen
// can't fake) is required, and each captured angle is stored as its own
// embedding for robust recognition.

const ZONES = ['center', 'left', 'right']       // must all be covered to finish
const STEP_META = {
  center: { label: 'Center', icon: 'face' },
  left: { label: 'Left', icon: 'arrow_back' },
  right: { label: 'Right', icon: 'arrow_forward' },
}
const HOLD_FRAMES = 2        // consecutive good frames in a zone before we keep it
const POLL_MS = 350          // min gap between frame analyses
const YAW_MAX = 30           // deg mapped to the edge of the arc (cosmetic)

// ---- ring geometry (360×360 SVG user space; camera circle sits inside) ------
const STAGE = 360
const CX = 180, CY = 180, R_TICK_IN = 144, R_TICK_OUT = 163, R_DOT = 153
const TICKS = 48
const polar = (r, deg) => {
  const a = (deg * Math.PI) / 180
  return [CX + r * Math.cos(a), CY - r * Math.sin(a)]   // SVG y is down
}
// Upper semicircle carries the 3 target zones: right (3 o'clock) → center (top) → left (9 o'clock)
const tickZone = (deg) => {
  if (deg <= 0 || deg >= 180) return null               // lower half = decorative
  if (deg <= 60) return 'right'
  if (deg <= 120) return 'center'
  return 'left'
}
const yawToDeg = (yaw) => Math.max(3, Math.min(177, 90 + ((yaw || 0) / YAW_MAX) * 90))

// One short warning, or null when the frame is good enough to capture. Kept
// separate from the main instruction so the stage never becomes a wall of text.
function warning(fb) {
  if (!fb) return null
  if (!fb.face_found) return { icon: 'person_off', text: 'No face detected' }
  if (fb.live === false) return { icon: 'sensors', text: 'Checking liveness — hold still' }
  if (!fb.near) return { icon: 'zoom_in', text: 'Move closer or zoom in' }
  if (!fb.quality_ok) return { icon: 'wb_incandescent', text: 'Improve the lighting' }
  return null
}

// The single primary directive shown under the camera.
function instruction(phase, fb, done, warn) {
  if (phase === 'starting') return 'Getting ready…'
  if (phase === 'submitting') return 'Saving your face profile…'
  if (phase === 'done') return 'Enrolled ✓'
  const remaining = ZONES.filter((z) => !done[z])
  if (!remaining.length) return 'All set — finishing up'
  if (!warn && fb && ZONES.includes(fb.pose) && !done[fb.pose]) return 'Hold still…'
  const next = remaining[0]
  return next === 'center' ? 'Look straight at the camera'
    : next === 'left' ? 'Slowly turn your head to your left'
      : 'Slowly turn your head to your right'
}

function Ring({ curDeg, done, active }) {
  const ticks = []
  for (let i = 0; i < TICKS; i++) {
    const deg = (360 / TICKS) * i
    const zone = tickZone(deg)
    const [x1, y1] = polar(R_TICK_IN, deg)
    const [x2, y2] = polar(R_TICK_OUT, deg)
    let color = 'rgba(255,255,255,0.12)', w = 2.5, glow = false
    if (zone && done[zone]) { color = '#22c55e'; w = 4; glow = true }
    else if (zone) {
      // target area not yet done — brighten the tick nearest the live pointer
      const near = active && Math.abs(((deg - curDeg + 540) % 360) - 180) > 168
      color = near ? '#0d9be1' : 'rgba(255,255,255,0.28)'
      w = near ? 4 : 3
      glow = near
    }
    ticks.push(<line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={color}
      strokeWidth={w} strokeLinecap="round" filter={glow ? 'url(#enrollGlow)' : undefined} />)
  }
  const [dx, dy] = polar(R_DOT, curDeg)
  return (
    <svg className="enroll-ring" viewBox={`0 0 ${STAGE} ${STAGE}`}>
      <defs>
        <filter id="enrollGlow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="2.4" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>
      {ticks}
      {active && <circle cx={dx} cy={dy} r="8" fill="#0d9be1" filter="url(#enrollGlow)" />}
      {active && <circle cx={dx} cy={dy} r="3.2" fill="#eaf6ff" />}
    </svg>
  )
}

// Translucent zoom pill overlaid on the camera. Lets a student stand back: a
// distant face is too small for the detector, so magnifying it (native sensor
// zoom where available, else a digital center-crop) fills the frame and clears
// the coverage gate.
function ZoomControl({ cam }) {
  const { min, max, step } = cam.zoomCaps
  const nudge = (max - min) / 10 || 0.2
  const clamp = (z) => Math.min(max, Math.max(min, z))
  return (
    <div className="enroll-zoom">
      <MatIcon name="zoom_out" onClick={() => cam.setZoom(clamp(cam.zoom - nudge))} title="Zoom out" />
      <input type="range" className="form-range zoom-range flex-grow-1" min={min} max={max} step={step || 0.1}
        value={cam.zoom} onChange={(e) => cam.setZoom(Number(e.target.value))} aria-label="Zoom" />
      <MatIcon name="zoom_in" onClick={() => cam.setZoom(clamp(cam.zoom + nudge))} title="Zoom in" />
      <span className="zoom-val">{cam.zoom.toFixed(1)}×</span>
    </div>
  )
}

function StepChip({ zone, done, active, thumbUrl }) {
  const meta = STEP_META[zone]
  const cls = done ? 'is-done' : active ? 'is-active' : ''
  return (
    <div className={`enroll-step ${cls}`}>
      <span className="enroll-step-ico">
        {done && thumbUrl ? <img src={thumbUrl} alt={meta.label} /> : <MatIcon name={meta.icon} />}
      </span>
      <span>{meta.label}</span>
      {done && <MatIcon name="check_circle" className="enroll-step-check" />}
    </div>
  )
}

export default function GuidedEnroll({ sid, name, autoStart = false, onDone, onCancel }) {
  const cam = useCamera()
  // With autoStart the parent already clicked "Start guided enrollment", so we
  // begin in 'starting' (camera opening). idle|starting|running|submitting|done|error
  const [phase, setPhase] = useState(autoStart ? 'starting' : 'idle')
  const [fb, setFb] = useState(null)
  const [curDeg, setCurDeg] = useState(90)
  const [doneState, setDoneState] = useState({ center: false, left: false, right: false })
  const [captures, setCaptures] = useState([])   // [{pose, thumbUrl}] for display
  const [err, setErr] = useState('')

  const loopRef = useRef(null)
  const runningRef = useRef(false)
  const busyRef = useRef(false)
  const holdRef = useRef(0)
  const holdZoneRef = useRef(null)
  const doneRef = useRef({ center: false, left: false, right: false })
  const capturedRef = useRef([])

  const stopLoop = () => {
    runningRef.current = false
    if (loopRef.current) cancelAnimationFrame(loopRef.current)
    loopRef.current = null
  }

  async function submit(all) {
    setPhase('submitting')
    try {
      const r = await api.enroll(sid, all)
      if (r.ok) { setPhase('done'); onDone?.(r) }
      else { setPhase('error'); setErr(r.message || 'Enrollment could not be verified.') }
    } catch (e) { setPhase('error'); setErr('Enroll failed: ' + e.message) }
  }

  async function analyzeOnce() {
    const blob = await cam.grabBlob()
    if (!blob) return
    let res
    try { res = await api.analyzePose(blob) }
    catch (e) {
      console.error('[enroll] pose analysis failed:', e)
      setErr("Couldn't connect to the camera service. Please try again.")
      return
    }
    setErr(''); setFb(res)
    if (res.yaw != null) setCurDeg(yawToDeg(res.yaw))

    const zone = res.pose
    const ok = res.face_found && res.near && res.quality_ok && res.live !== false &&
      ZONES.includes(zone) && !doneRef.current[zone]
    if (!ok) {
      if (zone !== holdZoneRef.current) { holdRef.current = 0; holdZoneRef.current = null }
      return
    }
    if (zone === holdZoneRef.current) holdRef.current += 1
    else { holdZoneRef.current = zone; holdRef.current = 1 }
    if (holdRef.current < HOLD_FRAMES) return

    // Keep this zone.
    capturedRef.current = [...capturedRef.current, { pose: zone, blob }]
    doneRef.current = { ...doneRef.current, [zone]: true }
    setDoneState(doneRef.current)
    setCaptures((c) => [...c, { pose: zone, thumbUrl: URL.createObjectURL(blob) }])
    holdRef.current = 0; holdZoneRef.current = null

    if (ZONES.every((z) => doneRef.current[z])) {
      stopLoop(); cam.stop()
      await submit(capturedRef.current)
    }
  }

  function runLoop() {
    let last = 0
    const loop = async (t) => {
      if (!runningRef.current) return
      if (t - last >= POLL_MS && !busyRef.current && cam.videoRef.current?.readyState >= 2) {
        last = t; busyRef.current = true
        try { await analyzeOnce() } catch { /* transient */ } finally { busyRef.current = false }
      }
      if (runningRef.current) loopRef.current = requestAnimationFrame(loop)
    }
    loopRef.current = requestAnimationFrame(loop)
  }

  async function start() {
    capturedRef.current = []; doneRef.current = { center: false, left: false, right: false }
    holdRef.current = 0; holdZoneRef.current = null
    setCaptures([]); setDoneState(doneRef.current); setErr(''); setFb(null); setCurDeg(90)
    setPhase('starting')
    const ok = await cam.start()
    if (!ok) { setPhase('error'); setErr(cam.error || 'Camera unavailable'); return }
    setPhase('running'); runningRef.current = true
    runLoop()
  }

  function close() { stopLoop(); cam.stop(); onCancel?.() }

  // Open the camera immediately when the parent already triggered enrollment, so
  // there's no redundant second "Start" click. Guarded so React 18 StrictMode's
  // double-mount doesn't start the camera twice.
  const startedRef = useRef(false)
  useEffect(() => {
    if (autoStart && !startedRef.current) { startedRef.current = true; start() }
    return () => { stopLoop(); cam.stop() }
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const warn = phase === 'running' ? warning(fb) : null
  const nextZone = ZONES.find((z) => !doneState[z])
  const thumbByPose = Object.fromEntries(captures.map((c) => [c.pose, c.thumbUrl]))
  // Only digital zoom needs the preview transform; native zoom already magnifies
  // the camera feed, so scaling it again would double-zoom.
  const previewScale = cam.zoomCaps && !cam.zoomCaps.native ? cam.zoom : 1
  const showCamera = ['starting', 'running', 'submitting', 'done'].includes(phase)

  return (
    <div className="enroll-stage">
      <div className="enroll-top">
        <div className="enroll-title">
          {phase === 'done' ? 'Enrolled' : 'Face enrollment'}
          {name && <> · <small>{name}</small></>}
        </div>
        <button className="enroll-close" onClick={close} aria-label="Close"><MatIcon name="close" /></button>
      </div>

      {!cam.secure ? (
        <div className="enroll-banner">
          Live enrollment needs HTTPS or localhost to access the camera.
        </div>
      ) : phase === 'error' ? (
        <>
          <div className="enroll-cam-wrap">
            <div className="enroll-cam" />
            <div className="enroll-cam-overlay">
              <span className="enroll-check" style={{ background: 'rgba(232,85,43,.22)', color: '#ffb59e' }}>
                <MatIcon name="error" />
              </span>
            </div>
            <Ring curDeg={90} done={doneState} active={false} />
          </div>
          <div className="enroll-banner">{err || 'Enrollment could not be verified.'}</div>
          <div className="enroll-actions">
            <button className="enroll-btn enroll-btn-ghost" onClick={close}>Close</button>
            <button className="enroll-btn enroll-btn-primary" onClick={start}>
              <MatIcon name="refresh" style={{ fontSize: 19 }} /> Try again
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="enroll-cam-wrap">
            <div className="enroll-cam">
              {showCamera && (
                <video ref={cam.videoRef} autoPlay muted playsInline
                  style={{ transform: `scaleX(-1) scale(${previewScale})`, transformOrigin: 'center' }} />
              )}
            </div>

            {(phase === 'starting' || phase === 'submitting') && (
              <div className="enroll-cam-overlay">
                <span className="enroll-spinner" />
                {phase === 'starting' ? 'Starting camera…' : 'Verifying & saving…'}
              </div>
            )}
            {phase === 'done' && (
              <div className="enroll-cam-overlay">
                <span className="enroll-check"><MatIcon name="check" /></span>
              </div>
            )}

            <Ring curDeg={curDeg} done={doneState} active={phase === 'running'} />
          </div>

          {phase === 'running' && cam.zoomCaps && <ZoomControl cam={cam} />}

          <div className="enroll-instruction">{instruction(phase, fb, doneState, warn)}</div>

          {phase === 'running' && warn && (
            <div className="enroll-pill"><MatIcon name={warn.icon} />{warn.text}</div>
          )}

          <div className="enroll-steps">
            {ZONES.map((z) => (
              <StepChip key={z} zone={z} done={doneState[z]}
                active={phase === 'running' && z === nextZone}
                thumbUrl={thumbByPose[z]} />
            ))}
          </div>

          {(phase === 'starting' || phase === 'running') && (
            <div className="enroll-actions">
              <button className="enroll-btn enroll-btn-ghost" onClick={close}>Cancel</button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
