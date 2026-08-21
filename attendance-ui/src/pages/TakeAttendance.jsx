import { useEffect, useMemo, useRef, useState } from 'react'
import { Row, Col, Card, Form, Badge, ButtonGroup, Table } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import FaceStage from '../components/FaceStage'
import MatIcon from '../components/MatIcon'
import { api } from '../api'
import { fmtTime, todayStr } from '../utils/time'
import { useCamera } from '../hooks/useCamera'
import { useFaceDetection } from '../hooks/useFaceDetection'
import { useToast } from '../components/Layout'
import { getSession } from '../auth'

// Auto-mark confirmation gate. Recognition runs per 500ms frame, and a single
// mislabeled frame used to mark the wrong student immediately. Instead, the same
// student must be recognized in at least CONFIRM_MIN_HITS separate cycles within
// CONFIRM_WINDOW_MS before we auto-mark — genuine presence still confirms in ~1s,
// but a lone stray frame no longer marks anyone.
const CONFIRM_MIN_HITS = 2
const CONFIRM_WINDOW_MS = 2500

// Live recognition cadence + crop. We only send a frame when the in-browser
// detector sees a face, and we send just the padded region around the face(s)
// rather than the whole frame — idle scenes cost the server nothing, and an
// occupied scene uploads/decodes far less. Padding keeps enough context around
// the face for the server's detector, alignment, and liveness cues.
const RECOGNIZE_INTERVAL_MS = 350
const CROP_MARGIN = 0.4

export default function TakeAttendance() {
  const session0 = getSession()
  const isStudent = session0?.type === 'student'
  const mySid = session0?.sid
  const [source, setSource] = useState('face') // 'face' | 'manual'
  const [session, setSession] = useState('Morning')
  // null until /api/health reports the server's MATCH_THRESHOLD. While it is
  // null we send no threshold at all, so the server's configured value applies;
  // the slider only overrides it once an operator actually drags it.
  const [threshold, setThreshold] = useState(null)
  const [autoMark, setAutoMark] = useState(true)
  const [marked, setMarked] = useState([])
  const [toast, setToast] = useToast()

  // Manual source: roster to pick from.
  const [students, setStudents] = useState(null)
  const [q, setQ] = useState('')
  const [cls, setCls] = useState('')

  const cam = useCamera()
  const det = useFaceDetection()            // in-browser real-time face boxes
  const labelsRef = useRef([])              // backend identity, normalized, for the overlay
  const busy = useRef(false)
  const loopRef = useRef(null)
  const runningRef = useRef(false)   // live-detection loop flag (avoids stale cam.active)
  const markedSids = useRef(new Set())
  const votesRef = useRef(new Map())   // sid -> recent hit timestamps (N-of-M gate)
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
    votesRef.current.clear()   // votes are per-session; don't carry across a switch
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

  async function doMark(sid, { source: src = 'face', similarity } = {}) {
    if (markedSids.current.has(sid)) return
    markedSids.current.add(sid)
    try {
      const r = await api.mark({ sid, session: sessionRef.current, source: src, similarity })
      if (r.record) setMarked((m) => (m.some((x) => x.sid === sid) ? m : [{ ...r.record, when: new Date() }, ...m]))
    } catch (e) { markedSids.current.delete(sid); setToast(e.message) }
  }
  async function recognizeBlob(blob, region) {
    // Omit the threshold unless it was overridden, so MATCH_THRESHOLD applies.
    const res = await api.recognize(blob, threshTouched.current ? threshRef.current : null)
    // A request already in flight when the camera is stopped resolves ~1-2s later;
    // dropping it here stops a stale box/label from flashing back after stop.
    if (!runningRef.current) return res
    // The server's bbox is in the coordinates of the CROP we sent. Map it back into
    // full-frame normalized coords so the overlay can attach the identity to the
    // real-time local box by centre. For a full-frame send (region covers the whole
    // video) this reduces to a plain normalize, i.e. the previous behaviour.
    const iw = res.image_w || region.w, ih = res.image_h || region.h
    const sx = region.w / iw, sy = region.h / ih          // sent-crop px -> video px
    const vw = region.videoW, vh = region.videoH
    const now = performance.now()
    labelsRef.current = res.faces.map((f) => {
      const fx = region.x + f.bbox.x * sx, fy = region.y + f.bbox.y * sy
      const fw = f.bbox.w * sx, fh = f.bbox.h * sy
      return {
        nx: fx / vw, ny: fy / vh, nw: fw / vw, nh: fh / vh,
        ncx: (fx + fw / 2) / vw, ncy: (fy + fh / 2) / vh,
        recognized: f.recognized, name: f.name, accuracy: f.accuracy, sid: f.sid, ts: now,
      }
    })
    if (autoRef.current) {
      const votes = votesRef.current
      for (const f of res.faces) {
        if (!f.recognized || !f.sid) continue
        // students self-check-in: only mark their own face
        if (isStudent && f.sid !== mySid) continue
        // Record this cycle's confident recognition as a vote, dropping any that
        // fell outside the window; auto-mark only once the same face has cleared
        // in CONFIRM_MIN_HITS separate recent cycles.
        const hits = (votes.get(f.sid) || []).filter((t) => now - t < CONFIRM_WINDOW_MS)
        hits.push(now)
        votes.set(f.sid, hits)
        if (hits.length >= CONFIRM_MIN_HITS) {
          // Student self-check-in: once identity is confirmed, require a live
          // head-turn before marking. Everyone else (staff kiosk) marks directly.
          if (isStudent && challengeReq.current && !challengeDoneRef.current) {
            if (!challengeRef.current) startChallenge()
          } else {
            doMark(f.sid, { source: 'face', similarity: f.similarity })
          }
        }
      }
      // Prune faces not seen recently so the vote map can't grow unbounded.
      for (const [sid, hits] of votes) {
        const keep = hits.filter((t) => now - t < CONFIRM_WINDOW_MS)
        if (keep.length) votes.set(sid, keep); else votes.delete(sid)
      }
    }
    return res
  }
  // The region to recognize this cycle, in video pixels. Returns null to SKIP the
  // call entirely (no face in view). Uses the in-browser detector's boxes to crop
  // to just the face(s); if that detector is unavailable (CDN blocked / erroring)
  // we fall back to the full frame so recognition still works.
  function recognitionRegion(v) {
    const vw = v.videoWidth, vh = v.videoHeight
    const boxes = det.boxesRef.current || []
    if (!boxes.length) {
      return det.status === 'error'
        ? { x: 0, y: 0, w: vw, h: vh, videoW: vw, videoH: vh, full: true }
        : null   // idle, no face — send nothing
    }
    let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity
    for (const b of boxes) {
      x1 = Math.min(x1, b.x); y1 = Math.min(y1, b.y)
      x2 = Math.max(x2, b.x + b.w); y2 = Math.max(y2, b.y + b.h)
    }
    const padX = (x2 - x1) * CROP_MARGIN, padY = (y2 - y1) * CROP_MARGIN
    x1 = Math.max(0, x1 - padX); y1 = Math.max(0, y1 - padY)
    x2 = Math.min(vw, x2 + padX); y2 = Math.min(vh, y2 + padY)
    return { x: x1, y: y1, w: x2 - x1, h: y2 - y1, videoW: vw, videoH: vh, full: false }
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
    } else {
      setChallengeUI({ dir: ch.dir, text: `Turn your head ${ch.dir}` })
    }
  }

  function startLive() {
    runningRef.current = true
    votesRef.current.clear()
    challengeRef.current = null; challengeDoneRef.current = false; setChallengeUI(null)
    let last = 0
    // The box tracks the face locally (in-browser), so recognition only needs to
    // run often enough for fresh identity + prompt auto-marking, not for smoothness.
    const step = async (t) => {
      if (!runningRef.current) return   // ref, not stale cam.active
      const v = cam.videoRef.current
      if (t - last > RECOGNIZE_INTERVAL_MS && !busy.current && v?.readyState >= 2 && v.videoWidth) {
        last = t
        busy.current = true
        try {
          if (challengeRef.current) {
            await challengeFrame()          // verifying a live head-turn
          } else {
            const region = recognitionRegion(v)
            if (region === null) {
              // No one in view — drop stale labels so a name doesn't linger.
              if (labelsRef.current.length) labelsRef.current = []
            } else {
              const b = await cam.grabRegion(region, region.full ? 640 : 480)
              if (b) await recognizeBlob(b, region)
            }
          }
        } catch {} finally { busy.current = false }
      }
      loopRef.current = requestAnimationFrame(step)
    }
    loopRef.current = requestAnimationFrame(step)
  }
  async function onStart() {
    const ok = await cam.start(); if (!ok) return setToast(cam.error)
    det.start(cam.videoRef)          // begin real-time in-browser detection (loads on first use)
    startLive()
  }
  function onStop() {
    runningRef.current = false; cancelAnimationFrame(loopRef.current)
    det.stop(); labelsRef.current = []; votesRef.current.clear()
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
                {!isStudent && <Col xs={12} className="mb-1">
                  <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Source</Form.Label>
                  <div>
                    <ButtonGroup size="sm">
                      <Button icon="camera" variant={source === 'face' ? 'primary' : 'outline-primary'}
                        onClick={() => switchSource('face')}>Face</Button>
                      <Button icon="check" variant={source === 'manual' ? 'primary' : 'outline-primary'}
                        onClick={() => switchSource('manual')}>Manual</Button>
                    </ButtonGroup>
                  </div>
                </Col>}
                <Col xs={5} sm={4}>
                  <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Session</Form.Label>
                  <Form.Control size="sm" value={session} onChange={(e) => setSession(e.target.value)} />
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

              {source === 'face' ? <>
                <FaceStage videoRef={cam.active ? cam.videoRef : null}
                  boxesRef={det.boxesRef} labelsRef={labelsRef}
                  placeholder="Start the camera to recognize students and mark attendance automatically." />

                {challengeUI && (
                  <div className="alert alert-primary d-flex align-items-center gap-2 mt-2 mb-0 py-2">
                    <MatIcon name={challengeUI.dir === 'left' ? 'arrow_back' : 'arrow_forward'} />
                    <span className="fw-semibold">Liveness check: {challengeUI.text}</span>
                  </div>
                )}

                {cam.active && (
                  <div className="fs-2 text-secondary mt-2">
                    {det.status === 'ready' ? '● Camera on — recognizing students'
                      : det.status === 'loading' ? '○ Getting the camera ready…'
                      : det.status === 'error' ? '● Camera on — recognizing students'
                      : ''}
                  </div>
                )}

                <div className="d-flex flex-wrap gap-2 mt-3">
                  {!cam.active
                    ? <Button variant="primary" icon="camera" onClick={onStart}>Start camera</Button>
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
