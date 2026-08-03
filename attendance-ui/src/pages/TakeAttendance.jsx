import { useEffect, useMemo, useRef, useState } from 'react'
import { Row, Col, Card, Form, Badge, ButtonGroup, Table } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import FaceStage from '../components/FaceStage'
import { api } from '../api'
import { fmtTime, todayStr } from '../utils/time'
import { useCamera } from '../hooks/useCamera'
import { useFaceDetection } from '../hooks/useFaceDetection'
import { useToast } from '../components/Layout'
import { getSession } from '../auth'

export default function TakeAttendance() {
  const session0 = getSession()
  const isStudent = session0?.type === 'student'
  const mySid = session0?.sid
  const [source, setSource] = useState('face') // 'face' | 'manual'
  const [session, setSession] = useState('Morning')
  const [threshold, setThreshold] = useState(0.75)
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
  const sessionRef = useRef(session)
  const threshRef = useRef(threshold)
  const autoRef = useRef(autoMark)
  // Seed "already marked" from the server so the page reflects existing
  // attendance (prior check-ins, face scans, or edits made in Records) instead
  // of only what was marked in this page session.
  useEffect(() => {
    sessionRef.current = session
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
  async function recognizeBlob(blob) {
    const res = await api.recognize(blob, threshRef.current)
    // A request already in flight when the camera is stopped resolves ~1-2s later;
    // dropping it here stops a stale box/label from flashing back after stop.
    if (!runningRef.current) return res
    // Store identity NORMALIZED so the overlay can attach it to the real-time
    // (locally-detected) box regardless of the resolution we sent to the server.
    const iw = res.image_w || 640, ih = res.image_h || 480
    const now = performance.now()
    labelsRef.current = res.faces.map((f) => ({
      nx: f.bbox.x / iw, ny: f.bbox.y / ih, nw: f.bbox.w / iw, nh: f.bbox.h / ih,
      ncx: (f.bbox.x + f.bbox.w / 2) / iw, ncy: (f.bbox.y + f.bbox.h / 2) / ih,
      recognized: f.recognized, name: f.name, accuracy: f.accuracy, sid: f.sid, ts: now,
    }))
    if (autoRef.current) for (const f of res.faces) {
      if (!f.recognized || !f.sid) continue
      // students self-check-in: only mark their own face
      if (isStudent && f.sid !== mySid) continue
      doMark(f.sid, { source: 'face', similarity: f.similarity })
    }
    return res
  }
  function startLive() {
    runningRef.current = true
    let last = 0
    // The box tracks the face locally (in-browser), so recognition only needs to
    // run often enough for fresh identity + prompt auto-marking, not for smoothness.
    const step = async (t) => {
      if (!runningRef.current) return   // ref, not stale cam.active
      if (t - last > 500 && !busy.current && cam.videoRef.current?.readyState >= 2) {
        last = t; busy.current = true
        try { const b = await cam.grabBlob(640); if (b) await recognizeBlob(b) } catch {} finally { busy.current = false }
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
    det.stop(); labelsRef.current = []; cam.stop()
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
                    <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Strictness {threshold.toFixed(2)}</Form.Label>
                    <Form.Range min="0.2" max="0.7" step="0.01" value={threshold}
                      onChange={(e) => setThreshold(parseFloat(e.target.value))} />
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
