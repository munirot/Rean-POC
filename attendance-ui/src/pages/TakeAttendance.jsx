import { useEffect, useMemo, useRef, useState } from 'react'
import { Row, Col, Card, Form, Badge, ButtonGroup, Table, Alert } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import FaceStage from '../components/FaceStage'
import ZoomControl from '../components/ZoomControl'
import MatIcon from '../components/MatIcon'
import { api } from '../api'
import { fmtTime, todayStr } from '../utils/time'
import { useCamera } from '../hooks/useCamera'
import { useFaceDetection } from '../hooks/useFaceDetection'
import { useToast } from '../components/Layout'
import { getSession } from '../auth'

// Snapshot recognition. Rather than streaming frames continuously, we detect a
// face locally at a LOW rate and, once it's steady and close enough, FREEZE the
// frame and send exactly ONE frame to the server. This cuts local CPU (a few
// detections/sec, not 30fps) and server load (one recognition per person, not
// ~8/sec of streaming), and reads as a clear "snapshot" to the user.
//
//   scan  → a big-enough face steady for STABLE_HITS low-rate checks
//   shoot → pause the video (freeze), send one /api/recognize
//   lock  → a recognized face stays plotted (tracked locally) with NO further
//           recognition while it remains in view — we already know who it is
//   rearm → once the face leaves, forget it and be ready for the next person
// (an unrecognized face isn't locked — it's retried after UNKNOWN_RETRY_MS.)
const STABLE_HITS = 2            // steady low-rate detections before we shoot
const MIN_FACE_FRAC = 0.16       // face box width / frame width worth recognizing
const UNKNOWN_RETRY_MS = 1500    // after an unknown result, wait this long before re-trying the same face
// Group mode runs a tiled full-frame pass in ~100ms on GPU, so we can refresh fast
// enough that far faces (which only the server sees) track smoothly, not just the
// near faces the local detector handles. The heartbeat is the slower idle probe.
const GROUP_REFRESH_MS = 200     // group: re-recognize this often while faces are present
const GROUP_HEARTBEAT_MS = 700   // group: idle sweep to catch new/far arrivals
const SCAN_POLL_MS = 200         // how often the scan loop wakes
const CROP_MARGIN = 0.4          // padding around the face when we crop the shot

// Scan-status banner styling by state.
const SCAN_VARIANT = { aim: 'alert-secondary', checking: 'alert-primary', ok: 'alert-success', unknown: 'alert-warning' }
const SCAN_ICON = { aim: 'center_focus_weak', checking: 'hourglass_top', ok: 'check_circle', unknown: 'help' }

// "08:20" + 10 -> "08:30". Used only to show when the late window shuts.
function addMinutes(hhmm, mins) {
  const [h, m] = String(hhmm || '').split(':').map(Number)
  if (Number.isNaN(h) || Number.isNaN(m)) return hhmm
  const t = (h * 60 + m + (Number(mins) || 0)) % 1440
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`
}

export default function TakeAttendance() {
  const session0 = getSession()
  const isStudent = session0?.type === 'student'
  const mySid = session0?.sid
  const [source, setSource] = useState('face') // 'face' | 'manual'
  // Face capture mode: 'individual' = one person per snapshot (kiosk / self check-in);
  // 'group' = recognize everyone in frame each pass and mark all (staff only).
  const [scanMode, setScanMode] = useState('individual')
  const [session, setSession] = useState('Morning')
  // null until /api/health reports the server's MATCH_THRESHOLD. While it is
  // null we send no threshold at all, so the server's configured value applies;
  // the slider only overrides it once an operator actually drags it.
  const [threshold, setThreshold] = useState(null)
  const [autoMark, setAutoMark] = useState(true)
  const [marked, setMarked] = useState([])
  const [toast, setToast] = useToast()
  // Capture policy for this class: which periods exist, which is live right now,
  // and whether individual scanning is permitted at all. The server enforces all
  // of this on POST /api/attendance — this is purely so the UI prompts honestly
  // instead of letting someone scan into a rejection.
  const [policy, setPolicy] = useState(null)
  const pickedPeriod = useRef(false)

  // Manual source: roster to pick from.
  const [students, setStudents] = useState(null)
  const [q, setQ] = useState('')
  const [cls, setCls] = useState('')

  const cam = useCamera()
  // short-range in-browser detector (near faces, tracked locally at ~10fps; FaceStage
  // interpolates to 60fps). Far faces in Group mode are found by the server pass.
  const det = useFaceDetection({ intervalMs: 100 })
  const labelsRef = useRef([])              // backend identity, normalized, for the overlay
  const busy = useRef(false)
  const loopRef = useRef(null)
  const runningRef = useRef(false)   // live-detection loop flag (avoids stale cam.active)
  const markedSids = useRef(new Set())
  // Snapshot state machine: 'scan' → 'shoot' (frozen, recognizing) → back to 'scan',
  // where a recognized face is LOCKED and just re-plotted (no re-recognition) until
  // it leaves the frame.
  const phaseRef = useRef('scan')
  const stableRef = useRef(0)          // consecutive steady detections while scanning
  const rearmRef = useRef(true)        // ready to take a shot? (false once locked / cooling down)
  const lockRef = useRef(null)         // identity stuck to the on-screen face: {sid,name,accuracy,recognized}
  const retryAtRef = useRef(0)         // earliest time to retry after an unknown result
  const pendingIdentityRef = useRef(null)   // identity awaiting a student's liveness turn
  // Group mode: recognize everyone in frame on a cadence, marking newly-seen students.
  const scanModeRef = useRef('individual')
  const lastPassRef = useRef(0)        // when the last group recognition ran
  const lastFoundRef = useRef(0)       // faces the last group pass found (keeps fast cadence for far faces)
  const scanKeyRef = useRef('')        // dedupes scan-status re-renders
  const [scanUI, setScanUI] = useState(null)  // {kind:'aim'|'checking'|'ok'|'unknown', text}
  // Active liveness challenge for unsupervised student self-check-in. challengeRef
  // holds {dir, sawCenter} while a turn is being verified; challengeDoneRef marks
  // it passed this session; challengeReq is whether the server requires it.
  const challengeRef = useRef(null)
  const challengeDoneRef = useRef(false)
  const challengeReq = useRef(false)
  const [challengeUI, setChallengeUI] = useState(null)   // {dir, text} while prompting
  const sessionRef = useRef(session)
  const threshRef = useRef(threshold)
  const threshTouched = useRef(false)   // has the operator overridden the server default?
  const autoRef = useRef(autoMark)
  // Seed "already marked" from the server so the page reflects existing
  // attendance (prior check-ins, face scans, or edits made in Records) instead
  // of only what was marked in this page session.
  useEffect(() => {
    sessionRef.current = session
    challengeRef.current = null; challengeDoneRef.current = false; setChallengeUI(null)
    api.roster({ date: todayStr(), session }).then((r) => {
      const present = (r.students || []).filter((x) => x.checkedIn)
      markedSids.current = new Set(present.map((x) => x.sid))
      setMarked(present.map((x) => ({
        sid: x.sid, name: x.name, cls: x.cls, session,
        status: x.status, time: x.time, when: x.time ? new Date(x.time) : new Date(),
      })))
    }).catch(() => { markedSids.current = new Set(); setMarked([]) })
  }, [session])
  useEffect(() => { threshRef.current = threshold }, [threshold])
  // Show the server's own cutoff on the slider so the number an operator reads is
  // the one actually in force. A drag that beats this response wins (touched).
  useEffect(() => {
    api.health()
      .then((h) => {
        if (!threshTouched.current && h?.match_threshold != null)
          setThreshold(h.match_threshold)
        // Students self-checking in must pass a live head-turn unless the server
        // disables it; staff kiosks (supervised) never challenge.
        challengeReq.current = isStudent && h?.self_checkin_challenge !== false
      })
      .catch(() => { challengeReq.current = isStudent })   // offline: default on for students
  }, [])
  useEffect(() => { autoRef.current = autoMark }, [autoMark])
  // Switching individual⇄group mid-scan: reset per-mode state so it starts clean.
  useEffect(() => {
    scanModeRef.current = scanMode
    lockRef.current = null; rearmRef.current = true; stableRef.current = 0; retryAtRef.current = 0
    lastPassRef.current = 0; lastFoundRef.current = 0; phaseRef.current = 'scan'
    labelsRef.current = []; challengeRef.current = null; setChallengeUI(null); ui(null)
  }, [scanMode])

  // Poll the capture policy: the window state changes with the clock (open ->
  // grace -> closed), so a page left open must not keep claiming it's open.
  useEffect(() => {
    let alive = true
    const tick = () => api.attendancePolicy(session)
      .then((p) => alive && setPolicy(p))
      .catch(() => { /* offline: leave the last known state */ })
    tick()
    const id = setInterval(tick, 30000)
    return () => { alive = false; clearInterval(id) }
  }, [session])

  // Once periods are configured, snap the session to a real one — the free-text
  // default ("Morning") may not match any configured period, and an unmatched
  // label is refused server-side.
  useEffect(() => {
    if (pickedPeriod.current || !policy?.periods?.length) return
    pickedPeriod.current = true
    const names = policy.periods.map((p) => p.name)
    if (names.includes(session)) return
    setSession(policy.period?.name || names[0])
  }, [policy, session])

  // Load the roster the first time manual marking is opened.
  useEffect(() => {
    if (source === 'manual' && students === null)
      api.students().then(setStudents).catch(() => setStudents([]))
  }, [source, students])

  const classes = useMemo(
    () => [...new Set((students || []).map((s) => s.cls).filter(Boolean))].sort(), [students])
  const filtered = useMemo(() => (students || []).filter((s) => {
    const hit = (s.name + ' ' + s.sid).toLowerCase().includes(q.toLowerCase())
    return hit && (!cls || s.cls === cls)
  }), [students, q, cls])

  // --- what capture is permitted right now -------------------------------
  const periods = policy?.periods || []
  const enforce = !!policy?.enforceWindow
  const winState = policy?.state                    // before|open|grace|closed
  const individualAllowed = policy?.individualAllowed !== false
  // No periods configured -> nothing to enforce (matches the server).
  const windowOpen = !enforce || !periods.length || winState === 'open' || winState === 'grace'
  // Automated capture (face scan / self check-in) obeys mode + window. Staff
  // hand-marking is human correction and is exempt server-side, so the Manual
  // tab stays usable even when the window has closed.
  const faceAllowed = individualAllowed && windowOpen
  const unknownSession = enforce && periods.length > 0 && !policy?.period

  // If the window shuts while the camera is running, stop rather than let every
  // scan bounce off a 409.
  useEffect(() => {
    if (cam.active && !faceAllowed) {
      onStop()
      setToast(individualAllowed ? 'Attendance closed for this period.'
        : 'Individual check-in is turned off for this class.')
    }
  }, [faceAllowed])   // eslint-disable-line react-hooks/exhaustive-deps

  async function doMark(sid, { source: src = 'face', similarity } = {}) {
    if (markedSids.current.has(sid)) return
    markedSids.current.add(sid)
    try {
      const r = await api.mark({ sid, session: sessionRef.current, source: src, similarity })
      if (r.record) setMarked((m) => (m.some((x) => x.sid === sid) ? m : [{ ...r.record, when: new Date() }, ...m]))
    } catch (e) { markedSids.current.delete(sid); setToast(e.message) }
  }
  // Set the scan-status banner, de-duped so we don't re-render every loop tick.
  function ui(kind, text) {
    const key = kind ? `${kind}|${text}` : ''
    if (scanKeyRef.current === key) return
    scanKeyRef.current = key
    setScanUI(kind ? { kind, text } : null)
  }

  // Map the server's crop-relative face boxes into full-frame normalized coords so
  // FaceStage can draw the identity on the (frozen) frame.
  function mapLabels(res, region) {
    const iw = res.image_w || region.w, ih = res.image_h || region.h
    const sx = region.w / iw, sy = region.h / ih          // sent-crop px -> video px
    const vw = region.videoW, vh = region.videoH
    const now = performance.now()
    labelsRef.current = (res.faces || []).map((f) => {
      const fx = region.x + f.bbox.x * sx, fy = region.y + f.bbox.y * sy
      const fw = f.bbox.w * sx, fh = f.bbox.h * sy
      return {
        nx: fx / vw, ny: fy / vh, nw: fw / vw, nh: fh / vh,
        ncx: (fx + fw / 2) / vw, ncy: (fy + fh / 2) / vh,
        recognized: f.recognized, name: f.name, accuracy: f.accuracy, sid: f.sid, ts: now,
      }
    })
  }

  // Largest local detection box, or null when no face is in view.
  function biggestBox() {
    const boxes = det.boxesRef.current || []
    if (!boxes.length) return null
    return boxes.reduce((a, b) => (b.w * b.h > a.w * a.h ? b : a))
  }

  // A padded crop region around ONE box (individual mode recognizes just the nearest
  // face, so the crop isn't diluted by other people spread across the frame).
  function regionForBox(box, v) {
    const vw = v.videoWidth, vh = v.videoHeight
    const padX = box.w * CROP_MARGIN, padY = box.h * CROP_MARGIN
    const x1 = Math.max(0, box.x - padX), y1 = Math.max(0, box.y - padY)
    const x2 = Math.min(vw, box.x + box.w + padX), y2 = Math.min(vh, box.y + box.h + padY)
    return { x: x1, y: y1, w: x2 - x1, h: y2 - y1, videoW: vw, videoH: vh, full: false }
  }

  // The recognized face to act on: a student only ever their own; a staff kiosk
  // takes the most confident recognized face (falling back to the largest).
  function pickFace(res) {
    const faces = res.faces || []
    if (isStudent) return faces.find((f) => f.recognized && f.sid === mySid) || null
    const rec = faces.filter((f) => f.recognized && f.sid)
    if (rec.length) return rec.reduce((a, b) => (b.accuracy > a.accuracy ? b : a))
    return faces[0] || null
  }

  function resumeScan() { phaseRef.current = 'scan'; stableRef.current = 0; try { cam.videoRef.current?.play() } catch { /* noop */ } }

  // Keep the known identity drawn on the face while it stays in view, tracking the
  // live detection box — no server round-trip. This is the "just plot" path.
  function plotLock(box, v) {
    const lock = lockRef.current
    if (!lock) return
    const W = v.videoWidth, H = v.videoHeight
    labelsRef.current = [{
      nx: box.x / W, ny: box.y / H, nw: box.w / W, nh: box.h / H,
      ncx: (box.x + box.w / 2) / W, ncy: (box.y + box.h / 2) / H,
      recognized: lock.recognized, name: lock.name, accuracy: lock.accuracy, ts: performance.now(),
    }]
  }

  // Freeze the current frame and send exactly ONE frame for recognition — of the
  // NEAREST face only.
  async function doSnapshot(v) {
    phaseRef.current = 'shoot'
    ui('checking', 'Hold still — checking…')
    try { v.pause() } catch { /* noop */ }           // freeze the displayed frame
    const box = biggestBox()
    const region = box ? regionForBox(box, v)
      : { x: 0, y: 0, w: v.videoWidth, h: v.videoHeight, videoW: v.videoWidth, videoH: v.videoHeight, full: true }
    const blob = await cam.grabRegion(region, region.full ? 640 : 480)
    if (!blob) { rearmRef.current = true; resumeScan(); ui(null); return }
    // Omit the threshold unless overridden, so the server's MATCH_THRESHOLD applies.
    const res = await api.recognize(blob, threshTouched.current ? threshRef.current : null)
    if (!runningRef.current) return
    try { v.play() } catch { /* noop */ }            // unfreeze — from here we track locally
    mapLabels(res, region)
    const face = pickFace(res)
    phaseRef.current = 'scan'; rearmRef.current = false
    if (face && face.recognized && face.sid) {
      if (isStudent && challengeReq.current && !challengeDoneRef.current) {
        // Liveness needs live motion, so run the head-turn challenge before locking.
        pendingIdentityRef.current = { sid: face.sid, name: face.name, accuracy: face.accuracy, recognized: true }
        ui('ok', `${face.name} — confirm you're live`)
        startChallenge()
        return
      }
      if (autoRef.current) await doMark(face.sid, { source: 'face', similarity: face.similarity })
      // Lock the identity to this face; the loop just re-plots it until they leave.
      lockRef.current = { sid: face.sid, name: face.name, accuracy: face.accuracy, recognized: true }
      ui('ok', autoRef.current ? `${face.name} · marked ✓` : `${face.name} · ${face.accuracy}%`)
    } else {
      const sawFace = (res.faces || []).length > 0
      // No identity to lock — retry the same face after a short cooldown.
      lockRef.current = null; retryAtRef.current = performance.now() + UNKNOWN_RETRY_MS
      ui('unknown', sawFace ? (isStudent ? "That's not a match — try again" : 'Not recognized — try again')
        : 'No face — step closer')
    }
  }

  // Group mode: recognize EVERY face in frame (one call covers them all — the region
  // is the union of all detected boxes), plot each with its name, and mark every
  // recognized student we haven't marked yet. No freeze, no single lock — it re-runs
  // on a cadence and whenever the number of faces changes, so a whole group can walk
  // past the camera and each person is marked once.
  async function doGroupPass(v) {
    lastPassRef.current = performance.now()
    // Send the WHOLE frame at high resolution and let the server's SCRFD do TILED
    // detection — so far / back-row faces the short-range in-browser detector can't
    // see are still found. (The frontend BlazeFace is near-range; the server isn't.)
    const region = { x: 0, y: 0, w: v.videoWidth, h: v.videoHeight, videoW: v.videoWidth, videoH: v.videoHeight, full: true }
    const blob = await cam.grabRegion(region, 1280)
    if (!blob) return
    const res = await api.recognize(blob, threshTouched.current ? threshRef.current : null, { tiles: '2x2' })
    if (!runningRef.current) return
    mapLabels(res, region)                            // draw a box + name on every face
    const faces = res.faces || []
    lastFoundRef.current = faces.length               // keep fast cadence while faces are present
    const recognized = faces.filter((f) => f.recognized && f.sid)
    let marked = 0
    for (const f of recognized) {
      if (markedSids.current.has(f.sid)) continue
      if (autoRef.current) { await doMark(f.sid, { source: 'face', similarity: f.similarity }); marked++ }
    }
    if (!faces.length) ui(null)
    else ui(recognized.length ? 'ok' : 'aim',
      recognized.length
        ? `${recognized.length} recognized${marked ? ` · +${marked} marked ✓` : ''} · ${faces.length} in view`
        : `${faces.length} face(s) — no match yet`)
  }

  // Begin the active challenge: pick a random turn direction and prompt for a
  // neutral (center) frame first, so the pass requires a real center -> turn
  // motion that a flat photo or screen can't reproduce.
  function startChallenge() {
    const dir = Math.random() < 0.5 ? 'left' : 'right'
    challengeRef.current = { dir, sawCenter: false }
    labelsRef.current = []           // clear recognition labels during the challenge
    setChallengeUI({ dir, text: 'Look straight at the camera' })
  }

  // One challenge frame: ask the pose endpoint for head pose + liveness and drive
  // the center -> turn state machine. On success, mark the student present.
  async function challengeFrame() {
    const blob = await cam.grabBlob(640)
    if (!blob) return
    const res = await api.analyzePose(blob)
    const ch = challengeRef.current
    if (!runningRef.current || !ch) return
    if (!res.face_found) { setChallengeUI({ dir: ch.dir, text: 'Keep your face in the frame' }); return }
    if (res.live === false) { setChallengeUI({ dir: ch.dir, text: 'Hold still — checking liveness' }); return }
    if (!ch.sawCenter) {
      if (res.pose === 'center') {
        challengeRef.current = { ...ch, sawCenter: true }
        setChallengeUI({ dir: ch.dir, text: `Now slowly turn your head ${ch.dir}` })
      } else {
        setChallengeUI({ dir: ch.dir, text: 'Look straight at the camera first' })
      }
      return
    }
    if (res.pose === ch.dir) {                 // the requested live turn happened
      challengeRef.current = null
      challengeDoneRef.current = true
      setChallengeUI(null)
      doMark(mySid, { source: 'face' })
      setToast('Liveness confirmed — checked in ✓')
      // Lock the (now verified) identity so it keeps plotting until they leave.
      lockRef.current = pendingIdentityRef.current || { sid: mySid, recognized: true }
      pendingIdentityRef.current = null
      ui('ok', `${lockRef.current.name ? lockRef.current.name + ' · ' : ''}checked in ✓`)
      rearmRef.current = false
    } else {
      setChallengeUI({ dir: ch.dir, text: `Turn your head ${ch.dir}` })
    }
  }

  function startLive() {
    runningRef.current = true
    phaseRef.current = 'scan'; stableRef.current = 0; rearmRef.current = true
    lockRef.current = null; retryAtRef.current = 0; pendingIdentityRef.current = null
    lastPassRef.current = 0; lastFoundRef.current = 0
    challengeRef.current = null; challengeDoneRef.current = false; setChallengeUI(null); ui(null)
    let last = 0
    const step = async (t) => {
      if (!runningRef.current) return   // ref, not stale cam.active
      const v = cam.videoRef.current
      const ready = v?.readyState >= 2 && v.videoWidth
      if (ready && t - last > SCAN_POLL_MS && !busy.current) {
        last = t; busy.current = true
        try {
          if (challengeRef.current) {
            await challengeFrame()                 // verifying a live head-turn
          } else if (scanModeRef.current === 'group') {
            // Group: re-recognize the whole frame on a cadence. When the in-browser
            // detector sees someone we refresh quickly; even when it sees nobody we
            // still sweep on a slower heartbeat, because far faces it can't see may
            // be there for the server's tiled detector to find.
            // Refresh fast while faces are present (near ones the local detector sees,
            // OR far ones the last server pass found); fall back to the idle heartbeat.
            const active = (det.boxesRef.current || []).length > 0 || lastFoundRef.current > 0
            const due = performance.now() - lastPassRef.current > (active ? GROUP_REFRESH_MS : GROUP_HEARTBEAT_MS)
            if (due) await doGroupPass(v)
          } else if (phaseRef.current === 'scan') {
            const box = biggestBox()
            // boxes are in processed-frame coords, so this fraction already reflects zoom
            const frac = box ? box.w / v.videoWidth : 0
            if (!box || frac < MIN_FACE_FRAC) {
              // Face left the frame — forget its identity and rearm for the next person.
              stableRef.current = 0; rearmRef.current = true; lockRef.current = null
              if (labelsRef.current.length) labelsRef.current = []
              ui(null)
            } else if (lockRef.current) {
              // Same face still here — we already know it, so just keep plotting it.
              plotLock(box, v)
            } else {
              // Unknown-result cooldown expired? allow another attempt on this face.
              if (!rearmRef.current && performance.now() >= retryAtRef.current) { rearmRef.current = true; stableRef.current = 0 }
              stableRef.current += 1
              if (rearmRef.current && stableRef.current >= STABLE_HITS) await doSnapshot(v)
              else if (rearmRef.current) ui('aim', 'Face detected — hold still')
            }
          }
        } catch (e) { console.error('[attendance] scan step failed', e); rearmRef.current = true; resumeScan() }
        finally { busy.current = false }
      }
      loopRef.current = requestAnimationFrame(step)
    }
    loopRef.current = requestAnimationFrame(step)
  }
  async function onStart() {
    const ok = await cam.start(); if (!ok) return setToast(cam.error)
    // Detect on the PROCESSED frame (zoom crop) so a distant/zoomed face is large
    // enough for the local detector — not just bigger on screen.
    det.start(cam.videoRef, cam.sourceRect)
    startLive()
  }
  function onStop() {
    runningRef.current = false; cancelAnimationFrame(loopRef.current)
    det.stop(); labelsRef.current = []
    phaseRef.current = 'scan'; stableRef.current = 0; rearmRef.current = true
    lockRef.current = null; retryAtRef.current = 0; pendingIdentityRef.current = null
    lastPassRef.current = 0; lastFoundRef.current = 0; ui(null)
    challengeRef.current = null; setChallengeUI(null); cam.stop()
  }
  useEffect(() => () => { runningRef.current = false; cancelAnimationFrame(loopRef.current); det.stop() }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  function switchSource(next) {
    if (next === source) return
    if (next === 'manual' && cam.active) onStop() // free the camera when leaving face mode
    setSource(next)
  }

  return (
    <>
      <PageHeader heading={isStudent ? 'Self Check-in' : 'Take Attendance'}
        subHeading={isStudent ? 'Check in with your face for this session'
          : 'Recognize faces and mark students present'} />
      <Row className="g-3">
        <Col lg={7}>
          <Card>
            <Card.Body>
              <Row className="g-2 mb-3 align-items-end">
                {!isStudent && <Col xs={12} className="mb-1 d-flex flex-wrap gap-3 align-items-end">
                  <div>
                    <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Source</Form.Label>
                    <div>
                      <ButtonGroup size="sm">
                        <Button icon="camera" variant={source === 'face' ? 'primary' : 'outline-primary'}
                          onClick={() => switchSource('face')}>Face</Button>
                        <Button icon="check" variant={source === 'manual' ? 'primary' : 'outline-primary'}
                          onClick={() => switchSource('manual')}>Manual</Button>
                      </ButtonGroup>
                    </div>
                  </div>
                  {source === 'face' && <div>
                    <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Mode</Form.Label>
                    <div>
                      <ButtonGroup size="sm">
                        <Button icon="person" variant={scanMode === 'individual' ? 'primary' : 'outline-primary'}
                          onClick={() => setScanMode('individual')}>Individual</Button>
                        <Button icon="groups" variant={scanMode === 'group' ? 'primary' : 'outline-primary'}
                          onClick={() => setScanMode('group')}>Group</Button>
                      </ButtonGroup>
                    </div>
                  </div>}
                </Col>}
                <Col xs={5} sm={4}>
                  <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Session</Form.Label>
                  {/* A picker once the admin has defined capture periods; free text
                      otherwise, so an institute that hasn't configured any still works. */}
                  {periods.length ? (
                    <Form.Select size="sm" value={session}
                      onChange={(e) => setSession(e.target.value)}>
                      {periods.map((p) => (
                        <option key={p.code} value={p.name}>
                          {p.name} · {p.start}–{p.end}
                        </option>
                      ))}
                    </Form.Select>
                  ) : (
                    <Form.Control size="sm" value={session}
                      onChange={(e) => setSession(e.target.value)} />
                  )}
                </Col>
                {source === 'face' && <>
                  <Col xs={7} sm={5}>
                    <Form.Label className="fs-2 text-secondary fw-semibold mb-1">
                      Strictness {(threshold ?? 0.35).toFixed(2)}
                      {!threshTouched.current && <span className="ms-1 opacity-50">(server)</span>}
                    </Form.Label>
                    <Form.Range min="0.2" max="0.7" step="0.01" value={threshold ?? 0.35}
                      onChange={(e) => {
                        threshTouched.current = true
                        setThreshold(parseFloat(e.target.value))
                      }} />
                  </Col>
                  <Col sm={3} className="d-flex align-items-center">
                    <Form.Check type="switch" label="Auto-mark" checked={autoMark}
                      onChange={(e) => setAutoMark(e.target.checked)} />
                  </Col>
                </>}
              </Row>

              <CaptureBanner policy={policy} periods={periods} enforce={enforce}
                winState={winState} individualAllowed={individualAllowed}
                unknownSession={unknownSession} isStudent={isStudent}
                session={session} />

              {source === 'face' ? <>
                <div style={{ position: 'relative' }}>
                  <FaceStage videoRef={cam.active ? cam.videoRef : null} sourceRect={cam.sourceRect}
                    boxesRef={det.boxesRef} labelsRef={labelsRef} useServerBoxes={scanMode === 'group'}
                    singleBox={scanMode === 'individual'}
                    placeholder="Start the camera to recognize students and mark attendance automatically." />
                  {cam.active && cam.zoomCaps && <ZoomControl cam={cam} className="cam-zoom" />}
                </div>

                {challengeUI ? (
                  <div className="alert alert-primary d-flex align-items-center gap-2 mt-2 mb-0 py-2">
                    <MatIcon name={challengeUI.dir === 'left' ? 'arrow_back' : 'arrow_forward'} />
                    <span className="fw-semibold">Liveness check: {challengeUI.text}</span>
                  </div>
                ) : scanUI ? (
                  <div className={`alert d-flex align-items-center gap-2 mt-2 mb-0 py-2 ${SCAN_VARIANT[scanUI.kind] || 'alert-secondary'}`}>
                    <MatIcon name={SCAN_ICON[scanUI.kind] || 'center_focus_weak'} />
                    <span className="fw-semibold">{scanUI.text}</span>
                  </div>
                ) : null}

                {cam.active && !challengeUI && !scanUI && (
                  <div className="fs-2 text-secondary mt-2">
                    {det.status === 'loading' ? '○ Getting the camera ready…'
                      : '● Camera on — look at the camera to check in'}
                  </div>
                )}

                <div className="d-flex flex-wrap gap-2 mt-3">
                  {!cam.active
                    ? <Button variant="primary" icon="camera" disabled={!faceAllowed}
                        onClick={onStart}>Start camera</Button>
                    : <>
                        <Button variant="secondary" icon="flip" onClick={cam.flip}>Flip</Button>
                        <Button variant="secondary" icon="stop" onClick={onStop}>Stop</Button>
                      </>}
                </div>
                {!cam.secure && <p className="text-danger fs-2 mt-2">
                  Live camera needs HTTPS or localhost. Attendance can't be taken here without a working camera.
                </p>}
              </> : (
                <ManualPicker students={students} filtered={filtered} classes={classes}
                  q={q} setQ={setQ} cls={cls} setCls={setCls}
                  isMarked={(sid) => markedSids.current.has(sid)}
                  onMark={(sid) => doMark(sid, { source: 'manual' })} />
              )}
            </Card.Body>
          </Card>
        </Col>

        <Col lg={5}>
          <Card>
            <Card.Header className="d-flex align-items-center">
              <span className="fw-bold text-primary flex-grow-1">Present · {session}</span>
              <Badge bg="success">{marked.length}</Badge>
            </Card.Header>
            <Card.Body>
              {marked.length ? marked.map((m) => (
                <div key={m.sid} className="present-item">
                  <Badge bg="success">✓</Badge>
                  <div className="flex-grow-1">
                    <div className="fw-semibold text-primary">{m.name}</div>
                    <div className="text-secondary fs-2">{m.sid} · {m.cls}</div>
                  </div>
                  <div className="text-secondary fs-2">{fmtTime(m.when || m.time || new Date())}</div>
                </div>
              )) : <div className="text-center text-secondary p-4">No one marked yet. Point the camera at enrolled students.</div>}
            </Card.Body>
          </Card>
        </Col>
      </Row>
      {toast}
    </>
  )
}

// Tells the user plainly what capture is possible right now, so nobody scans into
// a rejection. Mirrors what the server enforces on POST /api/attendance; it never
// decides anything on its own.
function CaptureBanner({ policy, periods, enforce, winState, individualAllowed,
                         unknownSession, isStudent, session }) {
  if (!policy) return null
  const p = policy.period
  const askTeacher = isStudent
    ? ' Ask your teacher to mark you.'
    : ' You can still mark students from the Manual tab.'

  if (!individualAllowed) {
    return (
      <Alert variant="warning" className="fs-3 py-2">
        This class takes attendance by <strong>class camera</strong>; individual
        check-in is turned off.{askTeacher}
      </Alert>
    )
  }
  if (!enforce || !periods.length) return null
  if (unknownSession) {
    return (
      <Alert variant="warning" className="fs-3 py-2">
        “{session}” isn’t one of the configured attendance periods, so marking
        would be refused. Pick a period above.
      </Alert>
    )
  }
  if (winState === 'before') {
    return (
      <Alert variant="secondary" className="fs-3 py-2">
        Attendance for <strong>{p.name}</strong> opens at <strong>{p.start}</strong>.
      </Alert>
    )
  }
  if (winState === 'open') {
    return (
      <Alert variant="success" className="fs-3 py-2">
        <strong>{p.name}</strong> is open until <strong>{p.end}</strong> — marks count
        as Present.
      </Alert>
    )
  }
  if (winState === 'grace') {
    return (
      <Alert variant="warning" className="fs-3 py-2">
        Late window — marks count as <strong>Late</strong> until{' '}
        <strong>{addMinutes(p.end, p.graceMinutes)}</strong>.
      </Alert>
    )
  }
  return (
    <Alert variant="danger" className="fs-3 py-2">
      Attendance is closed for <strong>{p?.name || session}</strong>.{askTeacher}
    </Alert>
  )
}

// Roster picker for manual attendance: search, filter by class, tap to mark present.
function ManualPicker({ students, filtered, classes, q, setQ, cls, setCls, isMarked, onMark }) {
  if (students === null)
    return <div className="text-center text-secondary p-4">Loading students…</div>
  return (
    <>
      <Row className="g-2 mb-2 align-items-end">
        <Col sm={6}>
          <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Search</Form.Label>
          <Form.Control size="sm" placeholder="Name or ID…" value={q} onChange={(e) => setQ(e.target.value)} />
        </Col>
        <Col sm={4}>
          <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Class</Form.Label>
          <Form.Select size="sm" value={cls} onChange={(e) => setCls(e.target.value)}>
            <option value="">All classes</option>
            {classes.map((c) => <option key={c} value={c}>{c}</option>)}
          </Form.Select>
        </Col>
        <Col className="text-end text-secondary fs-2">{filtered.length} shown</Col>
      </Row>
      <div style={{ maxHeight: 420, overflowY: 'auto' }}>
        <Table hover className="camu-table align-middle mb-0">
          <tbody>
            {filtered.map((s) => {
              const done = isMarked(s.sid)
              return (
                <tr key={s.sid}>
                  <td className="p-2" style={{ width: 48 }}>
                    <span className="avatar">{s.thumb
                      ? <img src={s.thumb} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : '—'}</span>
                  </td>
                  <td className="fs-3 p-2">
                    <div className="fw-semibold text-primary">{s.name}</div>
                    <div className="text-secondary fs-2">{s.sid} · {s.cls}</div>
                  </td>
                  <td className="p-2 text-end">
                    {done
                      ? <Badge bg="success">✓ Present</Badge>
                      : <Button size="sm" variant="outline-primary" icon="check"
                          onClick={() => onMark(s.sid)}>Mark</Button>}
                  </td>
                </tr>
              )
            })}
            {!filtered.length && <tr><td colSpan={3}
              className="text-center text-secondary p-4">No students match.</td></tr>}
          </tbody>
        </Table>
      </div>
    </>
  )
}
