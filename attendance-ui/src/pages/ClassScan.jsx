import { useEffect, useRef, useState } from 'react'
import { Row, Col, Card, Form, Badge, Table, Alert } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'
import { todayStr } from '../utils/time'
import { useToast } from '../components/Layout'

// Teacher review for a whole-class camera sitting. The camera only ever ADDS
// "present" — students it never saw are listed for the teacher, never marked
// absent automatically. So this screen is not a dashboard, it is the place the
// remaining judgement gets made.
const POLL_MS = 5000
const LS_KEY = 'rean.classSession'

function Bucket({ title, tone, hint, rows, children }) {
  return (
    <Card className="mb-3">
      <Card.Header className="fw-bold text-primary d-flex align-items-center gap-2">
        {title}
        <Badge bg={tone}>{rows.length}</Badge>
        {hint && <span className="text-secondary fs-2 fw-normal ms-auto">{hint}</span>}
      </Card.Header>
      <Card.Body className="p-0">
        {rows.length ? children : (
          <div className="text-center text-secondary p-4">None</div>
        )}
      </Card.Body>
    </Card>
  )
}

export default function ClassScan() {
  const [courses, setCourses] = useState([])
  const [camEnabled, setCamEnabled] = useState(null)
  const [confirmHits, setConfirmHits] = useState(3)
  const [form, setForm] = useState({ CrID: '', SecID: '', session: 'Morning', camera: '' })
  const [periods, setPeriods] = useState([])
  const [view, setView] = useState(null)     // {session, confirmed, ambiguous, notDetected}
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [toast, setToast] = useToast()
  const pollRef = useRef(null)

  useEffect(() => {
    api.myCourses().then((r) => {
      setCourses(r.courses || [])
      setCamEnabled(!!r.classCameraEnabled)
      if (r.confirmHits) setConfirmHits(r.confirmHits)
    }).catch((e) => { setErr(e.message); setCamEnabled(false) })
    api.attendancePolicy().then((p) => setPeriods(p.periods || [])).catch(() => {})
    // Re-attach to a session left open by a reload — opening is idempotent, but
    // the teacher shouldn't have to remember which sitting was running.
    const saved = localStorage.getItem(LS_KEY)
    if (saved) refresh(saved).catch(() => localStorage.removeItem(LS_KEY))
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  // Poll while the sitting is open so the buckets track the camera live.
  useEffect(() => {
    clearInterval(pollRef.current)
    if (view?.session?.state === 'open') {
      pollRef.current = setInterval(() => refresh(view.session.id).catch(() => {}), POLL_MS)
    }
    return () => clearInterval(pollRef.current)
  }, [view?.session?.id, view?.session?.state])   // eslint-disable-line react-hooks/exhaustive-deps

  async function refresh(id) {
    const v = await api.classSession(id)
    setView(v)
    return v
  }

  async function open() {
    if (!form.CrID) { setErr('Pick a class first.'); return }
    setErr(''); setBusy(true)
    try {
      const r = await api.openClassSession({
        CrID: form.CrID, SecID: form.SecID || null,
        date: todayStr(), session: form.session,
        camera: form.camera.trim() || null,
      })
      localStorage.setItem(LS_KEY, r.session.id)
      await refresh(r.session.id)
      setToast('Session open — the camera can start sending frames.')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  async function close() {
    setBusy(true)
    try {
      const v = await api.closeClassSession(view.session.id)
      setView(v)
      setToast(`Closed — ${v.stats.marked} student(s) marked present by camera.`)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  // Exception resolution reuses the ordinary staff edit path, so a camera-era
  // correction is indistinguishable from any other and lands in the same audit.
  async function resolve(row, status) {
    try {
      await api.setAttendance({
        sid: row.sid, date: view.session.date,
        session: view.session.session, status,
      })
      setToast(`${row.name} marked ${status === 'P' ? 'present' : status === 'L' ? 'late' : 'absent'}`)
      await refresh(view.session.id)
    } catch (e) { setToast('Could not update: ' + e.message) }
  }

  function reset() {
    clearInterval(pollRef.current)
    localStorage.removeItem(LS_KEY)
    setView(null)
  }

  const sections = courses.find((c) => c.CrID === form.CrID)?.sections || []
  const s = view?.session
  const open_ = s?.state === 'open'

  if (camEnabled === false) {
    return (
      <>
        <PageHeader heading="Class scan"
          subHeading="Mark a whole seated class from the room camera" />
        <Alert variant="secondary" className="fs-3">
          <strong>Whole-class camera capture is turned off.</strong> An administrator
          enables it in Settings once a camera is installed and the coverage check has
          been run for the room. Until then, use Take Attendance.
        </Alert>
      </>
    )
  }

  return (
    <>
      <PageHeader heading="Class scan"
        subHeading="The camera confirms who it clearly sees — you decide the rest">
        {s && <Button variant="secondary" icon="refresh"
          onClick={() => refresh(s.id)}>Refresh</Button>}
        {open_ && <Button variant="primary" icon="done_all" disabled={busy}
          onClick={close}>{busy ? 'Closing…' : 'Close & mark'}</Button>}
        {s && !open_ && <Button variant="secondary" icon="add" onClick={reset}>New session</Button>}
      </PageHeader>

      {err && <Alert variant="danger" className="fs-3">{err}</Alert>}

      {!s && (
        <Card className="mb-3">
          <Card.Header className="fw-bold text-primary">Start a sitting</Card.Header>
          <Card.Body>
            <Row className="g-2 align-items-end">
              <Col md={4}>
                <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Class</Form.Label>
                <Form.Select size="sm" value={form.CrID}
                  onChange={(e) => setForm({ ...form, CrID: e.target.value, SecID: '' })}>
                  <option value="">Choose a class…</option>
                  {courses.map((c) => (
                    <option key={c.CrID} value={c.CrID}>{c.name} ({c.students})</option>
                  ))}
                </Form.Select>
              </Col>
              <Col md={3}>
                <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Section</Form.Label>
                <Form.Select size="sm" value={form.SecID} disabled={!form.CrID}
                  onChange={(e) => setForm({ ...form, SecID: e.target.value })}>
                  <option value="">All sections</option>
                  {sections.map((x) => (
                    <option key={x.SecID} value={x.SecID}>{x.name} ({x.students})</option>
                  ))}
                </Form.Select>
              </Col>
              <Col md={2}>
                <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Period</Form.Label>
                {periods.length ? (
                  <Form.Select size="sm" value={form.session}
                    onChange={(e) => setForm({ ...form, session: e.target.value })}>
                    {periods.map((p) => <option key={p.code} value={p.name}>{p.name}</option>)}
                  </Form.Select>
                ) : (
                  <Form.Control size="sm" value={form.session}
                    onChange={(e) => setForm({ ...form, session: e.target.value })} />
                )}
              </Col>
              <Col md={2}>
                <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Camera</Form.Label>
                <Form.Control size="sm" placeholder="ROOM-A" value={form.camera}
                  onChange={(e) => setForm({ ...form, camera: e.target.value })} />
              </Col>
              <Col md={1}>
                <Button variant="primary" icon="videocam" disabled={busy} onClick={open}>
                  {busy ? '…' : 'Open'}
                </Button>
              </Col>
            </Row>
            <div className="text-secondary fs-2 mt-2">
              A student is auto-marked once the camera has clearly recognised them in{' '}
              <strong>{confirmHits}</strong> separate frames. Nobody is ever marked
              absent by the camera.
            </div>
          </Card.Body>
        </Card>
      )}

      {s && (
        <>
          <Card className="mb-3">
            <Card.Body className="d-flex flex-wrap gap-4 align-items-center">
              <div>
                <div className="text-secondary fs-2 fw-semibold text-uppercase">Sitting</div>
                <div className="fw-semibold text-primary">
                  {s.date} · {s.session}{s.camera ? ` · ${s.camera}` : ''}
                </div>
              </div>
              <div>
                <div className="text-secondary fs-2 fw-semibold text-uppercase">State</div>
                {open_ ? <Badge bg="success">Open · listening</Badge>
                  : <Badge bg="secondary">Closed</Badge>}
              </div>
              <div>
                <div className="text-secondary fs-2 fw-semibold text-uppercase">Frames</div>
                <div className="fw-semibold">{s.frames}</div>
              </div>
              <div>
                <div className="text-secondary fs-2 fw-semibold text-uppercase">Confirm at</div>
                <div className="fw-semibold">{view.confirmHits} hits</div>
              </div>
              {open_ && s.frames === 0 && (
                <div className="text-secondary fs-2">
                  Waiting for the camera to send its first frame…
                </div>
              )}
            </Card.Body>
          </Card>

          <Bucket title="Confirmed present" tone="success" rows={view.confirmed}
            hint={open_ ? 'will be marked on close' : 'marked by camera'}>
            <Table responsive className="camu-table mb-0">
              <thead><tr><th>Student</th><th>Frames seen</th><th>Best match</th></tr></thead>
              <tbody>
                {view.confirmed.map((r) => (
                  <tr key={r.sid} className="table-list_body">
                    <td className="fs-3 p-3">
                      <div className="fw-semibold">{r.name}</div>
                      <div className="text-secondary">{r.sid}</div>
                    </td>
                    <td className="fs-3 p-3">{r.hits}</td>
                    <td className="fs-3 p-3">
                      {r.bestSim != null ? `${Math.round(r.bestSim * 100)}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Bucket>

          <Bucket title="Ambiguous" tone="warning" rows={view.ambiguous}
            hint="seen, but not enough to trust — you decide">
            <ExceptionTable rows={view.ambiguous} onResolve={resolve} showHits />
          </Bucket>

          <Bucket title="Not detected" tone="light" rows={view.notDetected}
            hint="the camera says nothing about these — not an absence">
            <ExceptionTable rows={view.notDetected} onResolve={resolve} />
          </Bucket>
        </>
      )}

      {toast}
    </>
  )
}

// One-tap resolution for the students the camera could not settle. Deliberately
// offers Absent as an explicit teacher action — the camera never chooses it.
function ExceptionTable({ rows, onResolve, showHits }) {
  return (
    <Table responsive className="camu-table mb-0">
      <thead>
        <tr>
          <th>Student</th>{showHits && <th>Frames seen</th>}
          <th className="text-end">Mark</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.sid} className="table-list_body">
            <td className="fs-3 p-3">
              <div className="d-flex align-items-center gap-2">
                {r.thumb && <span className="avatar"><img src={r.thumb} alt=""
                  style={{ width: '100%', height: '100%', objectFit: 'cover' }} /></span>}
                <div>
                  <div className="fw-semibold">{r.name}</div>
                  <div className="text-secondary">{r.sid}</div>
                </div>
              </div>
            </td>
            {showHits && (
              <td className="fs-3 p-3">
                {r.hits > 0 && <div>{r.hits} clear</div>}
                {r.nearHits > 0 && (
                  <div className="text-secondary">
                    {r.nearHits} too close to a classmate to be sure
                  </div>
                )}
              </td>
            )}
            <td className="fs-3 p-3 text-end">
              <div className="d-flex gap-2 justify-content-end">
                <Button variant="primary" icon="check"
                  onClick={() => onResolve(r, 'P')}>Present</Button>
                <Button variant="secondary" icon="schedule"
                  onClick={() => onResolve(r, 'L')}>Late</Button>
                <Button variant="secondary" icon="close"
                  onClick={() => onResolve(r, 'A')}>Absent</Button>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}
