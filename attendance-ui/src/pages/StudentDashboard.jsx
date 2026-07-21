import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Row, Col, Card, Badge, Table } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'
import { getSession } from '../auth'

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
  const [err, setErr] = useState('')
  const nav = useNavigate()

  useEffect(() => {
    if (!sid) { setErr('This login has no linked student profile.'); return }
    api.studentProfile(sid).then(setData).catch((e) => setErr(e.message))
    api.studentStats(sid).then(setStats).catch(() => {})
    api.attendance({ sid }).then((r) => setRecent(r.slice(0, 10))).catch(() => {})
  }, [sid])

  if (err) return (<><PageHeader heading="My Dashboard" /><Card body className="text-danger">{err}</Card></>)
  if (!data) return <div className="text-secondary p-4">Loading…</div>
  const p = data.profile || {}

  return (
    <>
      <PageHeader heading="My Dashboard" subHeading={`${data.name} · ${data.sid}`}>
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

      <Card className="mt-2">
        <Card.Header className="fw-bold text-primary">My recent attendance</Card.Header>
        <Card.Body className="p-0">
          <Table responsive className="camu-table mb-0">
            <thead><tr><th>Date</th><th>Session</th><th>Subject</th><th>Status</th><th>Source</th></tr></thead>
            <tbody>
              {recent.length ? recent.map((r) => (
                <tr key={r.id} className="table-list_body">
                  <td className="fs-3 p-3">{r.date}</td>
                  <td className="fs-3 p-3">{r.session}</td>
                  <td className="fs-3 p-3">{r.SubNa || '—'}</td>
                  <td className="fs-3 p-3">
                    <Badge bg={r.status === 'P' ? 'success' : r.status === 'L' ? 'warning' : 'danger'} text={r.status === 'L' ? 'dark' : undefined}>
                      {r.status === 'P' ? 'Present' : r.status === 'L' ? 'Late' : 'Absent'}
                    </Badge>
                  </td>
                  <td className="fs-3 p-3"><Badge bg="light" text="dark">{r.source}</Badge></td>
                </tr>
              )) : <tr><td colSpan="5" className="text-center text-secondary p-4">No attendance yet</td></tr>}
            </tbody>
          </Table>
        </Card.Body>
      </Card>
    </>
  )
}
