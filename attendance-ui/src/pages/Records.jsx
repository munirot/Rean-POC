import { useEffect, useMemo, useState } from 'react'
import { Card, Table, Form, Row, Col, Badge, Modal } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'
import { todayStr, fmtTime } from '../utils/time'
import { useToast } from '../components/Layout'
import { getSession } from '../auth'

// Confirmation dialog for changing a student's attendance status.
const STATUS_LABEL = { present: 'Present', late: 'Late', absent: 'Absent',
                       excused: 'Excused', none: 'Not marked' }
// 'excused' is intentionally NOT offered here: it is granted by approving a leave
// request, not set by hand, so the audit trail always points back to a request.
const UI_TO_CODE = { present: 'P', late: 'L', absent: 'A' }

function EditAttendanceModal({ row, date, defaultSession, onClose, onSaved, setToast }) {
  const [status, setStatus] = useState('present')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (row) setStatus(['present', 'late', 'absent'].includes(row.status) ? row.status : 'present')
  }, [row])
  if (!row) return null
  const session = row.session || defaultSession || 'Morning'

  async function confirm() {
    setBusy(true)
    try {
      await api.setAttendance({ sid: row.sid, date, session, status: UI_TO_CODE[status] })
      setToast('Attendance updated')
      onSaved()
    } catch (e) {
      setToast('Update failed: ' + e.message)
    } finally { setBusy(false) }
  }

  return (
    <Modal show onHide={busy ? undefined : onClose} centered>
      <Modal.Header closeButton><Modal.Title>Edit attendance</Modal.Title></Modal.Header>
      <Modal.Body>
        <div className="mb-3">
          <div className="fw-semibold text-primary">{row.name}</div>
          <div className="text-secondary fs-2">{row.sid} · {date} · {session}</div>
          <div className="text-secondary fs-2 mt-1">Current: {STATUS_LABEL[row.status] || '—'}</div>
        </div>
        <Form.Label className="fs-2 text-secondary fw-semibold mb-1">New status</Form.Label>
        <Form.Select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="present">Present</option>
          <option value="late">Late</option>
          <option value="absent">Absent</option>
        </Form.Select>
        <p className="text-secondary fs-2 mt-3 mb-0">
          Change {row.name}'s status to <strong>{STATUS_LABEL[status]}</strong>? This updates the record.
        </p>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" icon="check" onClick={confirm} disabled={busy}>
          {busy ? 'Saving…' : 'Confirm change'}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}

export default function Records() {
  const session0 = getSession()
  return session0?.type === 'student' ? <MyRecords sid={session0.sid} /> : <RosterRecords />
}

// ---- Staff/admin: today's roster — who has checked in, who hasn't ----------
function RosterRecords() {
  const [date, setDate] = useState(todayStr())
  const [cls, setCls] = useState('')
  const [session, setSession] = useState('')
  const [data, setData] = useState(null)
  const [classes, setClasses] = useState([])
  const [only, setOnly] = useState('all')            // all | in | out
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState(null)       // row being edited (modal)
  const [toast, setToast] = useToast()

  const load = () => api.roster({ date, cls, session }).then(setData).catch(() => setData(null))
  useEffect(() => { load() }, [date, cls, session])
  useEffect(() => { api.students().then((s) =>
    setClasses([...new Set(s.map((x) => x.cls).filter(Boolean))].sort())).catch(() => {}) }, [])

  const rows = useMemo(() => {
    let r = data?.students || []
    if (only === 'in') r = r.filter((x) => x.checkedIn)
    if (only === 'out') r = r.filter((x) => !x.checkedIn)
    if (q) r = r.filter((x) => (x.name + ' ' + x.sid).toLowerCase().includes(q.toLowerCase()))
    return r
  }, [data, only, q])

  function exportCsv() {
    const head = ['sid', 'name', 'class', 'date', 'checkedIn', 'status', 'time', 'source']
    const lines = [head.join(',')].concat((data?.students || []).map((r) => [
      r.sid, `"${r.name}"`, r.cls || '', date, r.checkedIn ? 'yes' : 'no',
      r.status, fmtTime(r.time), r.source || '',
    ].join(',')))
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }))
    a.download = `attendance_roster_${date}.csv`; a.click()
  }

  const statusBadge = (r) => {
    if (r.status === 'present') return <Badge bg="success">Present</Badge>
    if (r.status === 'late') return <Badge bg="warning" text="dark">Late</Badge>
    if (r.status === 'absent') return <Badge bg="danger">Absent</Badge>
    // Approved leave — neutral, not red: it doesn't count against the student.
    if (r.status === 'excused') return <Badge bg="info" text="dark">Excused</Badge>
    return <Badge bg="light" text="dark">Not yet</Badge>
  }

  return (
    <>
      <PageHeader heading="Attendance Records"
        subHeading={data ? `${date} · ${data.checked_in} of ${data.total} checked in` : date}>
        <Button variant="secondary" icon="download" onClick={exportCsv} disabled={!data}>Export CSV</Button>
      </PageHeader>

      <Row className="g-3 mb-1">
        <Col sm={4}><Card body><div className="text-secondary fs-3 fw-semibold text-uppercase">Checked in</div>
          <div className="stat-value" style={{ color: 'var(--success)' }}>{data ? data.checked_in : '—'}</div></Card></Col>
        <Col sm={4}><Card body><div className="text-secondary fs-3 fw-semibold text-uppercase">Not yet</div>
          <div className="stat-value" style={{ color: 'var(--warning)' }}>{data ? data.not_yet : '—'}</div></Card></Col>
        <Col sm={4}><Card body><div className="text-secondary fs-3 fw-semibold text-uppercase">Total students</div>
          <div className="stat-value">{data ? data.total : '—'}</div></Card></Col>
      </Row>

      <Card className="mt-2">
        <Card.Body>
          <Row className="g-2 mb-3 align-items-end">
            <Col xs={6} sm={3}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Date</Form.Label>
              <Form.Control size="sm" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Col>
            <Col xs={6} sm={3}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Class</Form.Label>
              <Form.Select size="sm" value={cls} onChange={(e) => setCls(e.target.value)}>
                <option value="">All</option>
                {classes.map((c) => <option key={c} value={c}>{c}</option>)}
              </Form.Select>
            </Col>
            <Col xs={6} sm={3}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Show</Form.Label>
              <Form.Select size="sm" value={only} onChange={(e) => setOnly(e.target.value)}>
                <option value="all">All</option>
                <option value="in">Checked in</option>
                <option value="out">Not yet</option>
              </Form.Select>
            </Col>
            <Col xs={6} sm={3}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Search</Form.Label>
              <Form.Control size="sm" placeholder="Name or ID" value={q} onChange={(e) => setQ(e.target.value)} />
            </Col>
          </Row>

          <Table responsive hover className="camu-table align-middle mb-0">
            <thead><tr>
              <th>Student</th><th>ID</th><th>Class</th><th>Status</th><th>Time</th><th>Source</th><th></th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.sid} className="table-list_body">
                  <td className="fs-3 p-3 fw-semibold text-primary">{r.name}</td>
                  <td className="fs-3 p-3 text-secondary">{r.sid}</td>
                  <td className="fs-3 p-3">{r.cls}</td>
                  <td className="fs-3 p-3">{statusBadge(r)}</td>
                  <td className="fs-3 p-3">{fmtTime(r.time) || '—'}</td>
                  <td className="fs-3 p-3">{r.source ? <Badge bg="light" text="dark">{r.source}</Badge> : '—'}</td>
                  <td className="fs-3 p-3 text-end">
                    <Button size="sm" variant="outline-primary" icon="edit"
                      onClick={() => setEditing(r)}>Edit</Button>
                  </td>
                </tr>
              ))}
              {data && !rows.length && <tr><td colSpan="7" className="text-center text-secondary p-4">No students match</td></tr>}
            </tbody>
          </Table>
        </Card.Body>
      </Card>

      <EditAttendanceModal row={editing} date={date} defaultSession={session}
        onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); load() }} setToast={setToast} />
      {toast}
    </>
  )
}

// ---- Student: their own attendance history --------------------------------
function MyRecords({ sid }) {
  const [rows, setRows] = useState([])
  const [date, setDate] = useState('')
  useEffect(() => { api.attendance({ sid, date }).then(setRows).catch(() => setRows([])) }, [date, sid])

  const badge = (s) => s === 'P' ? <Badge bg="success">Present</Badge>
    : s === 'L' ? <Badge bg="warning" text="dark">Late</Badge>
    : s === 'A' ? <Badge bg="danger">Absent</Badge> : <Badge bg="light" text="dark">{s || '—'}</Badge>

  return (
    <>
      <PageHeader heading="My Attendance" subHeading={`${rows.length} record(s)`} />
      <Card>
        <Card.Body>
          <Row className="g-2 mb-3 align-items-end">
            <Col xs={6} sm={3}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Date</Form.Label>
              <Form.Control size="sm" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Col>
          </Row>
          <Table responsive hover className="camu-table align-middle mb-0">
            <thead><tr><th>Date</th><th>Session</th><th>Subject</th><th>Status</th><th>Time</th><th>Source</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="table-list_body">
                  <td className="fs-3 p-3">{r.date}</td>
                  <td className="fs-3 p-3">{r.session}</td>
                  <td className="fs-3 p-3">{r.subNa || '—'}</td>
                  <td className="fs-3 p-3">{badge(r.status)}</td>
                  <td className="fs-3 p-3">{fmtTime(r.ts) || '—'}</td>
                  <td className="fs-3 p-3">{r.source ? <Badge bg="light" text="dark">{r.source}</Badge> : '—'}</td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan="6" className="text-center text-secondary p-4">No records</td></tr>}
            </tbody>
          </Table>
        </Card.Body>
      </Card>
    </>
  )
}
