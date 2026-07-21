import { useEffect, useRef, useState } from 'react'
import { Row, Col, Card, Form, Badge } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import FaceStage from '../components/FaceStage'
import { api } from '../api'
import { useCamera } from '../hooks/useCamera'
import { useToast } from '../components/Layout'
import { getSession } from '../auth'

export default function TakeAttendance() {
  const session0 = getSession()
  const isStudent = session0?.type === 'student'
  const mySid = session0?.sid
  const [session, setSession] = useState('Morning')
  const [threshold, setThreshold] = useState(0.35)
  const [autoMark, setAutoMark] = useState(true)
  const [faces, setFaces] = useState([])
  const [dims, setDims] = useState({ w: 640, h: 480 })
  const [marked, setMarked] = useState([])
  const [imgSrc, setImgSrc] = useState(null)
  const [toast, setToast] = useToast()
  const fileRef = useRef(null)

  const cam = useCamera()
  const busy = useRef(false)
  const loopRef = useRef(null)
  const markedSids = useRef(new Set())
  const sessionRef = useRef(session)
  const threshRef = useRef(threshold)
  const autoRef = useRef(autoMark)
  useEffect(() => { sessionRef.current = session; markedSids.current = new Set() }, [session])
  useEffect(() => { threshRef.current = threshold }, [threshold])
  useEffect(() => { autoRef.current = autoMark }, [autoMark])

  async function doMark(sid, similarity) {
    if (markedSids.current.has(sid)) return
    markedSids.current.add(sid)
    try {
      const r = await api.mark({ sid, session: sessionRef.current, source: 'kiosk', similarity })
      if (r.record) setMarked((m) => (m.some((x) => x.sid === sid) ? m : [{ ...r.record, when: new Date() }, ...m]))
    } catch { markedSids.current.delete(sid) }
  }
  async function recognizeBlob(blob) {
    const res = await api.recognize(blob, threshRef.current)
    setFaces(res.faces); setDims({ w: res.image_w, h: res.image_h })
    if (autoRef.current) for (const f of res.faces) {
      if (!f.recognized || !f.sid) continue
      // students self-check-in: only mark their own face
      if (isStudent && f.sid !== mySid) continue
      doMark(f.sid, f.similarity)
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
  async function onStart() { setImgSrc(null); const ok = await cam.start(); if (!ok) return setToast(cam.error); startLive() }
  function onStop() { cancelAnimationFrame(loopRef.current); cam.stop(); setFaces([]) }
  async function onPhoto(file) {
    onStop(); setImgSrc(URL.createObjectURL(file))
    try { await recognizeBlob(file) } catch (e) { setToast('Recognition failed: ' + e.message) }
  }
  useEffect(() => () => cancelAnimationFrame(loopRef.current), [])

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
                <Col xs={5} sm={4}>
                  <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Session</Form.Label>
                  <Form.Control size="sm" value={session} onChange={(e) => setSession(e.target.value)} />
                </Col>
                <Col xs={7} sm={5}>
                  <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Strictness {threshold.toFixed(2)}</Form.Label>
                  <Form.Range min="0.2" max="0.7" step="0.01" value={threshold}
                    onChange={(e) => setThreshold(parseFloat(e.target.value))} />
                </Col>
                <Col sm={3} className="d-flex align-items-center">
                  <Form.Check type="switch" label="Auto-mark" checked={autoMark}
                    onChange={(e) => setAutoMark(e.target.checked)} />
                </Col>
              </Row>

              <FaceStage videoRef={cam.active ? cam.videoRef : null} imgSrc={imgSrc}
                faces={faces} srcW={dims.w} srcH={dims.h}
                placeholder="Start the camera or upload a class photo to recognize students." />

              <div className="d-flex flex-wrap gap-2 mt-3">
                {!cam.active
                  ? <Button variant="primary" icon="camera" onClick={onStart}>Start camera</Button>
                  : <>
                      <Button variant="secondary" icon="flip" onClick={cam.flip}>Flip</Button>
                      <Button variant="secondary" icon="stop" onClick={onStop}>Stop</Button>
                    </>}
                <Button variant="secondary" icon="photo" onClick={() => fileRef.current.click()}>Upload photo</Button>
                <input ref={fileRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }}
                  onChange={(e) => { const f = e.target.files[0]; if (f) onPhoto(f); e.target.value = '' }} />
              </div>
              {!cam.secure && <p className="text-secondary fs-2 mt-2">Live camera needs HTTPS/localhost — photo upload works anywhere.</p>}
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
                  <div className="text-secondary fs-2">{(m.when || new Date()).toLocaleTimeString()}</div>
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
