import { useEffect, useRef, useState } from 'react'
import Button from './Button'
import { useCamera } from '../hooks/useCamera'
import { api } from '../api'

// Apple-FaceID-style guided enrollment. The student's face sits in a circular
// frame; a tick ring around it fills as they slowly move their head left, center,
// and right. Every frame is scored by the backend for head pose + liveness; a real
// left/right turn (which a flat photo or screen can't fake) is required, and each
// captured angle is stored as its own embedding for robust recognition.

const ZONES = ['center', 'left', 'right']       // must all be covered to finish
const PROMPT = 'Slowly move your head left, center, and right to fill the ring'
const HOLD_FRAMES = 2        // consecutive good frames in a zone before we keep it
const POLL_MS = 350          // min gap between frame analyses
const YAW_MAX = 30           // deg mapped to the edge of the arc (cosmetic)

// ---- ring geometry ---------------------------------------------------------
const CX = 130, CY = 130, R_TICK_IN = 104, R_TICK_OUT = 118, R_DOT = 111
const TICKS = 32
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

function hint(fb, err, done) {
  if (err) return err
  if (!fb) return 'Waiting for the camera…'
  if (!fb.face_found) return 'Center your face in the circle'
  if (fb.live === false) return 'Hold still — checking liveness'
  if (!fb.quality_ok) return 'Move a little closer / improve the lighting'
  const remaining = ZONES.filter((z) => !done[z])
  if (!remaining.length) return 'Great — finishing up ✓'
  if (fb.pose === 'center' && done.center) return `Now turn your head ${remaining.includes('left') ? 'left' : 'right'}`
  if (['center', 'left', 'right'].includes(fb.pose)) return 'Hold still ✓'
  return 'Keep turning slowly…'
}

function Ring({ curDeg, done, active }) {
  const dotDeg = curDeg
  const ticks = []
  for (let i = 0; i < TICKS; i++) {
    const deg = (360 / TICKS) * i
    const zone = tickZone(deg)
    const [x1, y1] = polar(R_TICK_IN, deg)
    const [x2, y2] = polar(R_TICK_OUT, deg)
    let color = 'var(--bs-border-color, #d0d5dd)', op = 0.5, w = 3
    if (zone && done[zone]) { color = 'var(--bs-success, #22c55e)'; op = 1; w = 4 }
    else if (zone) {
      // target area not yet done — brighten the tick nearest the live pointer
      const near = active && Math.abs(((deg - dotDeg + 540) % 360) - 180) > 168
      color = near ? 'var(--bs-primary, #0d6efd)' : 'var(--bs-secondary-color, #98a2b3)'
      op = near ? 1 : 0.7
    }
    ticks.push(<line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={color}
      strokeOpacity={op} strokeWidth={w} strokeLinecap="round" />)
  }
  const [dx, dy] = polar(R_DOT, dotDeg)
  return (
    <svg viewBox="0 0 260 260" width="260" height="260" style={{ maxWidth: '100%' }}>
      {ticks}
      {active && <circle cx={dx} cy={dy} r="7" fill="var(--bs-primary, #0d6efd)" />}
    </svg>
  )
}

export default function GuidedEnroll({ sid, onDone, onCancel }) {
  const cam = useCamera()
  const [phase, setPhase] = useState('idle')     // idle|running|submitting|done|error
  const [fb, setFb] = useState(null)
  const [curDeg, setCurDeg] = useState(90)
  const [doneState, setDoneState] = useState({ center: false, left: false, right: false })
  const [captures, setCaptures] = useState([])   // [{pose, thumbUrl}] for display
  const [msg, setMsg] = useState('')
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
    setPhase('submitting'); setMsg('Verifying and saving your face profile…')
    try {
      const r = await api.enroll(sid, all)
      if (r.ok) { setPhase('done'); setMsg(r.message || 'Enrolled ✓'); onDone?.(r) }
      else { setPhase('error'); setErr(r.message || 'Enrollment could not be verified.') }
    } catch (e) { setPhase('error'); setErr('Enroll failed: ' + e.message) }
  }

  async function analyzeOnce() {
    const blob = await cam.grabBlob()
    if (!blob) return
    let res
    try { res = await api.analyzePose(blob) }
    catch (e) {
      // Keep the technical detail in the console; show the user a plain message.
      console.error('[enroll] pose analysis failed:', e)
      setErr("Couldn't connect to the camera service. Please try again.")
      return
    }
    setErr(''); setFb(res)
    if (res.yaw != null) setCurDeg(yawToDeg(res.yaw))

    const zone = res.pose
    const ok = res.face_found && res.quality_ok && res.live !== false &&
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
    setCaptures([]); setDoneState(doneRef.current); setMsg(''); setErr(''); setFb(null); setCurDeg(90)
    const ok = await cam.start()
    if (!ok) { setPhase('error'); setErr(cam.error || 'Camera unavailable'); return }
    setPhase('running'); runningRef.current = true
    runLoop()
  }

  function cancel() { stopLoop(); cam.stop(); setPhase('idle'); onCancel?.() }

  useEffect(() => () => { stopLoop(); cam.stop() }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const showStage = phase === 'running' || phase === 'submitting'
  const doneCount = ZONES.filter((z) => doneState[z]).length

  return (
    <div>
      {!cam.secure && (
        <p className="text-secondary fs-2">Live enrollment needs HTTPS or localhost to access the camera.</p>
      )}

      {showStage && (
        <div className="d-flex flex-column align-items-center text-center mb-2">
          <div style={{ position: 'relative', width: 260, height: 260 }}>
            {/* circular camera */}
            <div style={{
              position: 'absolute', top: 21, left: 21, width: 218, height: 218,
              borderRadius: '50%', overflow: 'hidden', background: '#000',
            }}>
              <video ref={cam.videoRef} autoPlay muted playsInline
                style={{ width: '100%', height: '100%', objectFit: 'cover', transform: 'scaleX(-1)' }} />
            </div>
            {/* tick ring on top */}
            <div style={{ position: 'absolute', inset: 0 }}>
              <Ring curDeg={curDeg} done={doneState} active={phase === 'running'} />
            </div>
          </div>
          <div className="fw-semibold mt-2">{PROMPT}</div>
          <div className={`fs-2 mt-1 ${err ? 'text-danger' : 'text-secondary'}`}>{hint(fb, err, doneState)}</div>
          <div className="fs-2 text-secondary mt-1">{doneCount} / {ZONES.length} angles captured</div>
        </div>
      )}

      {captures.length > 0 && (
        <div className="d-flex justify-content-center gap-2 mb-3">
          {captures.map((c) => (
            <div key={c.pose} className="text-center">
              <img src={c.thumbUrl} alt={c.pose} title={c.pose}
                style={{ width: 52, height: 52, objectFit: 'cover', borderRadius: 10, border: '2px solid var(--bs-success,#22c55e)' }} />
              <div className="fs-2 text-secondary text-capitalize">{c.pose}</div>
            </div>
          ))}
        </div>
      )}

      {msg && phase !== 'error' && (
        <div className={`fs-2 text-center mb-3 ${phase === 'done' ? 'text-success' : 'text-secondary'}`}>{msg}</div>
      )}
      {phase === 'error' && err && <div className="fs-2 text-center text-danger mb-3">{err}</div>}

      <div className="d-flex justify-content-center flex-wrap gap-2">
        {(phase === 'idle' || phase === 'error') && (
          <Button variant="primary" icon="videocam" disabled={!cam.secure} onClick={start}>
            {phase === 'error' ? 'Try again' : 'Start guided enrollment'}
          </Button>
        )}
        {phase === 'running' && <Button variant="secondary" icon="close" onClick={cancel}>Cancel</Button>}
        {phase === 'submitting' && <Button variant="primary" disabled>Saving…</Button>}
      </div>
    </div>
  )
}
