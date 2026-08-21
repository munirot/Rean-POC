import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Row, Col, Card, Badge, Table, Modal, Form } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'
import { getSession } from '../auth'

// Attendance statuses as the student sees them. 'E' (excused) is deliberately
// neutral, not red: an approved absence is not a mark against them, and it is
// excluded from the attendance rate.
const STATUS_STYLE = {
  P: { label: 'Present', bg: 'success' },
  L: { label: 'Late', bg: 'warning', text: 'dark' },
  A: { label: 'Absent', bg: 'danger' },
  E: { label: 'Excused', bg: 'info', text: 'dark' },
}

// How a raised dispute reads back to the student on the row it challenges.
function DisputeBadge({ state }) {
  if (state === 'approved') return <Badge bg="success">Corrected</Badge>
  if (state === 'rejected') return <Badge bg="secondary">Not changed</Badge>
  return <Badge bg="info" text="dark">Disputed · in review</Badge>
}

function Row2({ k, v }) {
  return (
    <div className="field-row">
      <span className="k">{k}</span><span className="sep">:</span><span>{v ?? '—'}</span>
    </div>
  )
}
function Stat({ label, value, sub, color }) {
  return (
    <Card body>
      <div className="text-secondary fs-3 fw-semibold text-uppercase">{label}</div>
      <div className="stat-value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="text-secondary fs-2">{sub}</div>}
    </Card>
  )
}

export default function StudentDashboard() {
  const session = getSession()
  const sid = session?.sid
  const [data, setData] = useState(null)
  const [stats, setStats] = useState(null)
  const [recent, setRecent] = useState([])
  const [plan, setPlan] = useState(null)
  const [disputes, setDisputes] = useState([])   // my disputes (for row status)
  const [disputing, setDisputing] = useState(null) // the row a modal is open for
  const [reason, setReason] = useState('')
  const [leave, setLeave] = useState([])
  const [leaveOpen, setLeaveOpen] = useState(false)
  const [leaveForm, setLeaveForm] = useState({ startDate: '', endDate: '', reason: '', document: '' })
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState('')
  const [err, setErr] = useState('')
  const nav = useNavigate()

  const loadDisputes = () => api.disputes().then(setDisputes).catch(() => {})
  const loadLeave = () => api.leave().then(setLeave).catch(() => {})

  async function submitLeave() {
    setBusy(true)
    try {
      await api.requestLeave({
        startDate: leaveForm.startDate, endDate: leaveForm.endDate || leaveForm.startDate,
        reason: leaveForm.reason.trim() || undefined,
        document: leaveForm.document.trim() || undefined,
      })
      setToast('Leave request sent — your teacher will review it.')
      setLeaveOpen(false)
      setLeaveForm({ startDate: '', endDate: '', reason: '', document: '' })
      await Promise.all([loadLeave(), api.studentStats(sid).then(setStats).catch(() => {})])
    } catch (e) {
      setToast(e.message || 'Could not send the request.')
    } finally { setBusy(false) }
  }

  useEffect(() => {
    if (!sid) { setErr('This login has no linked student profile.'); return }
    api.studentProfile(sid).then(setData).catch((e) => setErr(e.message))
    api.studentStats(sid).then(setStats).catch(() => {})
    api.attendance({ sid }).then((r) => setRecent(r.slice(0, 10))).catch(() => {})
    // Student-facing improvement plan. The backend returns supportive,
    // first-person wording when the caller is a student (see /api/students/{sid}/plan).
    api.studentPlan(sid).then(setPlan).catch(() => {})
    loadDisputes()
    loadLeave()
  }, [sid])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 2800)
    return () => clearTimeout(t)
  }, [toast])

  // Most recent dispute per attendance row, so a row shows its live review state.
  const byRecord = {}
  for (const d of disputes) { if (d.recordId && !byRecord[d.recordId]) byRecord[d.recordId] = d }

  async function submitDispute() {
    if (!disputing) return
    setBusy(true)
    try {
      await api.raiseDispute({ recordId: disputing.id, reason: reason.trim() || undefined })
      setToast('Sent to your teacher for review — nothing changes until they confirm.')
      setDisputing(null); setReason('')
      await loadDisputes()
    } catch (e) {
      setToast(e.message || 'Could not send the dispute.')
    } finally { setBusy(false) }
  }

  if (err) return (<><PageHeader heading="My Dashboard" /><Card body className="text-danger">{err}</Card></>)
  if (!data) return <div className="text-secondary p-4">Loading…</div>
  const p = data.profile || {}

  return (
    <>
      <PageHeader heading="My Dashboard" subHeading={`${data.name} · ${data.sid}`}>
        <Button variant="secondary" icon="event_busy" onClick={() => setLeaveOpen(true)}>Request leave</Button>
        <Button variant="secondary" icon="forum" onClick={() => nav('/chat')}>Ask AI</Button>
        <Button variant="primary" icon="how_to_reg" onClick={() => nav('/attendance')}>Self check-in</Button>
      </PageHeader>

      {/* profile banner (like the real student dashboard) */}
      <div className="profile-banner mb-4">
        <div className="d-flex flex-wrap gap-4">
          <span className="avatar lg square">
            {data.thumb ? <img src={data.thumb} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : 'no photo'}
          </span>
          <div className="flex-grow-1" style={{ minWidth: 280 }}>
            <h4 className="fw-bold text-primary mb-3">{data.name}</h4>
            <Row2 k="Student status" v={<Badge bg="success">Active</Badge>} />
            <Row2 k="Roll No." v={p.RollNo} />
            <Row2 k="Degree" v={p.CurPrNm} />
            <Row2 k="Department" v={p.CurDeptNm} />
            <Row2 k="Semester" v={p.CurSemNm} />
            <Row2 k="Course Name" v={p.CurCrNm} />
            <Row2 k="College" v={p.InName} />
            <Row2 k="Face profile" v={data.enrolled
              ? <Badge bg="success">Enrolled</Badge> : <Badge bg="warning" text="dark">Not enrolled</Badge>} />
          </div>
        </div>
      </div>

      {/* my attendance */}
      <Row className="g-3 mb-1">
        <Col md={6} xl={3}><Stat label="Attendance rate" value={stats ? `${stats.rate}%` : '—'} color="var(--royal)" /></Col>
        <Col md={6} xl={3}><Stat label="Present" value={stats ? stats.present : '—'} color="var(--success)" /></Col>
        <Col md={6} xl={3}><Stat label="Late" value={stats ? stats.late : '—'} color="var(--warning)" /></Col>
        <Col md={6} xl={3}><Stat label="Absent" value={stats ? stats.absent : '—'} color="var(--danger)" /></Col>
      </Row>
      {!!stats?.excused && (
        <div className="text-secondary fs-2 mt-2">
          {stats.excused} excused absence{stats.excused === 1 ? '' : 's'} — approved
          leave, not counted against your attendance rate.
        </div>
      )}

      {/* My progress — at-risk transparency, spoken to the student in supportive
          language. Loads automatically; no "generate" step, unlike the staff view. */}
      {plan && (
        <Card className={`mt-3 ${plan.atRisk ? 'border-warning' : ''}`}>
          <Card.Header className="fw-bold text-primary d-flex align-items-center gap-2">
            My progress
            {plan.onTrack
              ? <Badge bg="success">On track</Badge>
              : plan.atRisk
                ? <Badge bg="warning" text="dark">Needs attention</Badge>
                : <Badge bg="info" text="dark">A few things to watch</Badge>}
          </Card.Header>
          <Card.Body>
            <p className="fw-semibold text-primary">{plan.summary}</p>
            {plan.focus?.length > 0 && (
              <div className="mb-3 d-flex gap-2 flex-wrap">
                {plan.focus.map((f) => <Badge key={f} bg="light" text="dark">{f}</Badge>)}
              </div>
            )}
            {plan.suggestions.map((s, i) => (
              <div key={i} className="mb-3">
                <div className="fw-semibold">
                  <Badge bg="secondary" className="me-2">{s.area}</Badge>{s.observation}
                </div>
                <ul className="mb-0 mt-1">
                  {s.actions.map((a, k) => <li key={k} className="text-secondary">{a}</li>)}
                </ul>
              </div>
            ))}
            <div className="text-secondary fs-2 mt-2">{plan.disclaimer}</div>
          </Card.Body>
        </Card>
      )}

      <Card className="mt-3">
        <Card.Header className="fw-bold text-primary">My recent attendance</Card.Header>
        <Card.Body className="p-0">
          <Table responsive className="camu-table mb-0">
            <thead><tr><th>Date</th><th>Session</th><th>Subject</th><th>Status</th><th>Source</th><th></th></tr></thead>
            <tbody>
              {recent.length ? recent.map((r) => (
                <tr key={r.id} className="table-list_body">
                  <td className="fs-3 p-3">{r.date}</td>
                  <td className="fs-3 p-3">{r.session}</td>
                  <td className="fs-3 p-3">{r.subNa || '—'}</td>
                  <td className="fs-3 p-3">
                    <Badge bg={STATUS_STYLE[r.status]?.bg || 'danger'}
                      text={STATUS_STYLE[r.status]?.text}>
                      {STATUS_STYLE[r.status]?.label || 'Absent'}
                    </Badge>
                  </td>
                  <td className="fs-3 p-3"><Badge bg="light" text="dark">{r.source}</Badge></td>
                  <td className="fs-3 p-3 text-end">
                    {/* Nothing to dispute on a present day, or on an absence the
                        student themselves asked to have excused. */}
                    {r.status === 'P' || r.status === 'E'
                      ? <span className="text-secondary">—</span>
                      : byRecord[r.id]
                        ? <DisputeBadge state={byRecord[r.id].state} />
                        : <Button variant="secondary" icon="flag" onClick={() => { setDisputing(r); setReason('') }}>Dispute</Button>}
                  </td>
                </tr>
              )) : <tr><td colSpan="6" className="text-center text-secondary p-4">No attendance yet</td></tr>}
            </tbody>
          </Table>
        </Card.Body>
      </Card>

      {leave.length > 0 && (
        <Card className="mt-3">
          <Card.Header className="fw-bold text-primary">My leave requests</Card.Header>
          <Card.Body className="p-0">
            <Table responsive className="camu-table mb-0">
              <thead><tr><th>Dates</th><th>Reason</th><th>State</th></tr></thead>
              <tbody>
                {leave.map((l) => (
                  <tr key={l.id} className="table-list_body">
                    <td className="fs-3 p-3">
                      {l.startDate === l.endDate ? l.startDate : <>{l.startDate} → {l.endDate}</>}
                    </td>
                    <td className="fs-3 p-3">{l.reason || <span className="text-secondary">—</span>}</td>
                    <td className="fs-3 p-3">
                      {l.state === 'approved'
                        ? <Badge bg="success">Approved</Badge>
                        : l.state === 'rejected'
                          ? <Badge bg="secondary">Not approved</Badge>
                          : <Badge bg="info" text="dark">Awaiting your teacher</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card.Body>
        </Card>
      )}

      {/* Leave request — creates a pending item for staff; nothing in the
          attendance log changes unless they approve it. */}
      <Modal show={leaveOpen} onHide={() => !busy && setLeaveOpen(false)} centered>
        <Modal.Header closeButton><Modal.Title>Request leave</Modal.Title></Modal.Header>
        <Modal.Body>
          <p className="text-secondary fs-3">
            Ask for an absence to be excused. If your teacher approves it, those days
            stop counting against your attendance rate.
          </p>
          <Row className="g-2 mb-2">
            <Col sm={6}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">First day</Form.Label>
              <Form.Control type="date" size="sm" value={leaveForm.startDate}
                onChange={(e) => setLeaveForm({ ...leaveForm, startDate: e.target.value })} />
            </Col>
            <Col sm={6}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Last day</Form.Label>
              <Form.Control type="date" size="sm" value={leaveForm.endDate}
                min={leaveForm.startDate || undefined}
                onChange={(e) => setLeaveForm({ ...leaveForm, endDate: e.target.value })} />
              <div className="text-secondary fs-2 mt-1">Leave blank for a single day.</div>
            </Col>
          </Row>
          <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Reason</Form.Label>
          <textarea className="form-control mb-2" rows={3} maxLength={1000}
            placeholder="e.g. Medical appointment at the hospital."
            value={leaveForm.reason}
            onChange={(e) => setLeaveForm({ ...leaveForm, reason: e.target.value })} />
          <Form.Label className="fs-2 text-secondary fw-semibold mb-1">
            Supporting document (optional)
          </Form.Label>
          <Form.Control size="sm" maxLength={300} placeholder="e.g. Medical certificate 12/08"
            value={leaveForm.document}
            onChange={(e) => setLeaveForm({ ...leaveForm, document: e.target.value })} />
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={() => setLeaveOpen(false)} disabled={busy}>Cancel</Button>
          <Button variant="primary" icon="send" onClick={submitLeave}
            disabled={busy || !leaveForm.startDate}>
            {busy ? 'Sending…' : 'Send request'}
          </Button>
        </Modal.Footer>
      </Modal>

      {/* Dispute modal — logs an "I was present" review item; never edits the
          log directly. Staff approve/reject from their Disputes queue. */}
      <Modal show={!!disputing} onHide={() => !busy && setDisputing(null)} centered>
        <Modal.Header closeButton><Modal.Title>Dispute attendance</Modal.Title></Modal.Header>
        <Modal.Body>
          {disputing && (
            <p className="mb-2">
              You're flagging <strong>{disputing.date} · {disputing.session}</strong>
              {disputing.subNa ? <> · {disputing.subNa}</> : null} — currently marked{' '}
              <strong>{disputing.status === 'L' ? 'Late' : 'Absent'}</strong>.
            </p>
          )}
          <p className="text-secondary fs-3">
            Tell your teacher what happened (optional). They'll review and correct it if
            needed — nothing changes automatically.
          </p>
          <textarea className="form-control" rows={3} value={reason} maxLength={500}
            placeholder="e.g. I was in class but the camera didn't recognise me."
            onChange={(e) => setReason(e.target.value)} />
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={() => setDisputing(null)} disabled={busy}>Cancel</Button>
          <Button variant="primary" icon="send" onClick={submitDispute} disabled={busy}>
            {busy ? 'Sending…' : 'Send for review'}
          </Button>
        </Modal.Footer>
      </Modal>

      {toast && <div className="toast-msg">{toast}</div>}
    </>
  )
}
