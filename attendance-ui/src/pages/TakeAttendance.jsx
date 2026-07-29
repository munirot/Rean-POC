import { useEffect, useMemo, useRef, useState } from 'react'
import { Row, Col, Card, Form, Badge, ButtonGroup, Table } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import FaceStage from '../components/FaceStage'
import { api } from '../api'
import { fmtTime } from '../utils/time'
import { useCamera } from '../hooks/useCamera'
import { useToast } from '../components/Layout'
import { getSession } from '../auth'

export default function TakeAttendance() {
  const session0 = getSession()
  const isStudent = session0?.type === 'student'
  const mySid = session0?.sid
  const [source, setSource] = useState('face') // 'face' | 'manual'
  const [session, setSession] = useState('Morning')
  const [threshold, setThreshold] = useState(0.35)
  const [autoMark, setAutoMark] = useState(true)
  const [faces, setFaces] = useState([])
  const [dims, setDims] = useState({ w: 640, h: 480 })
  const [marked, setMarked] = useState([])
  const [toast, setToast] = useToast()

  // Manual source: roster to pick from.
  const [students, setStudents] = useState(null)
  const [q, setQ] = useState('')
  const [cls, setCls] = useState('')

  const cam = useCamera()
  const busy = useRef(false)
  const loopRef = useRef(null)
  const markedSids = useRef(new Set())
  const sessionRef = useRef(session)
  const threshRef = useRef(threshold)
  const autoRef = useRef(autoMark)
  useEffect(() => { sessionRef.current = session; markedSids.current = new Set(); setMarked([]) }, [session])
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
    setFaces(res.faces); setDims({ w: res.image_w, h: res.image_h })
    if (autoRef.current) for (const f of res.faces) {
      if (!f.recognized || !f.sid) continue
      // students self-check-in: only mark their own face
      if (isStudent && f.sid !== mySid) continue
      doMark(f.sid, { source: 'face', similarity: f.similarity })
    }
    return res
  }
  function startLive() {
    let last = 0
    const step = async (t) => {
      if (!cam.active) return
      if (t - last > 700 && !busy.current && cam.videoRef.current?.readyState >= 2) {
        last = t; busy.current = true
        try { const b = await cam.grabBlob(); if (b) await recognizeBlob(b) } catch {} finally { busy.current = false }
      }
      loopRef.current = requestAnimationFrame(step)
    }
    loopRef.current = requestAnimationFrame(step)
  }
  async function onStart() { const ok = await cam.start(); if (!ok) return setToast(cam.error); startLive() }
  function onStop() { cancelAnimationFrame(loopRef.current); cam.stop(); setFaces([]) }
  useEffect(() => () => cancelAnimationFrame(loopRef.current), [])

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
                  faces={faces} srcW={dims.w} srcH={dims.h}
                  placeholder="Start the camera to recognize students. Live camera only — photo upload is disabled here to prevent spoofing." />

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
