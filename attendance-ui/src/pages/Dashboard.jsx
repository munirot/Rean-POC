import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Row, Col, Card, Table, Badge } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api, todayStr } from '../api'
import { getSession } from '../auth'
import StudentDashboard from './StudentDashboard'

function Stat({ label, value, sub, color }) {
  return (
    <Card body>
      <div className="text-secondary fs-3 fw-semibold text-uppercase">{label}</div>
      <div className="stat-value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="text-secondary fs-2">{sub}</div>}
    </Card>
  )
}

export default function Dashboard() {
  // Students see their own dashboard; staff/admin see the aggregate.
  if (getSession()?.type === 'student') return <StudentDashboard />
  return <StaffDashboard />
}

function StaffDashboard() {
  const [sum, setSum] = useState(null)
  const [recent, setRecent] = useState([])
  const nav = useNavigate()
  const today = todayStr()

  useEffect(() => {
    api.summary(today).then(setSum).catch(() => {})
    api.attendance({ date: today }).then((r) => setRecent(r.slice(0, 8))).catch(() => {})
  }, [today])

  return (
    <>
      <PageHeader heading="Dashboard" subHeading={`${today} · today's attendance overview`}>
        <Button variant="primary" icon="how_to_reg" onClick={() => nav('/attendance')}>
          Take attendance
        </Button>
      </PageHeader>

      <Row className="g-3 mb-1">
        <Col md={6} xl={3}><Stat label="Students" value={sum ? sum.total_students : '—'} sub="in roster" /></Col>
        <Col md={6} xl={3}><Stat label="Face-enrolled" value={sum ? sum.enrolled : '—'}
          sub={sum ? `${sum.total_students - sum.enrolled} without a profile` : ''} /></Col>
        <Col md={6} xl={3}><Stat label="Present today" value={sum ? sum.present : '—'} color="var(--success)" /></Col>
        <Col md={6} xl={3}><Stat label="Absent today" value={sum ? sum.absent : '—'} color="var(--warning)" /></Col>
      </Row>

      <Row className="g-3 mt-1">
        <Col lg={6}>
          <Card>
            <Card.Header className="fw-bold text-primary">By class · today</Card.Header>
            <Card.Body className="p-0">
              <Table responsive className="camu-table mb-0">
                <thead><tr><th>Class</th><th>Present</th><th>Total</th><th>Rate</th></tr></thead>
                <tbody>
                  {sum?.by_class?.length ? sum.by_class.map((c) => (
                    <tr key={c.cls} className="table-list_body">
                      <td className="fs-3 p-3">{c.cls}</td>
                      <td className="fs-3 p-3">{c.present}</td>
                      <td className="fs-3 p-3">{c.total}</td>
                      <td className="fs-3 p-3">{c.total ? Math.round((c.present / c.total) * 100) : 0}%</td>
                    </tr>
                  )) : <tr><td colSpan="4" className="text-center text-secondary p-4">No data</td></tr>}
                </tbody>
              </Table>
            </Card.Body>
          </Card>
        </Col>

        <Col lg={6}>
          <Card>
            <Card.Header className="fw-bold text-primary">Recent check-ins</Card.Header>
            <Card.Body>
              {recent.length ? recent.map((r) => (
                <div key={r.id} className="present-item">
                  <Badge bg="success">✓</Badge>
                  <div className="flex-grow-1">
                    <div className="fw-semibold text-primary">{r.name}</div>
                    <div className="text-secondary fs-2">{r.cls} · {r.session}</div>
                  </div>
                  <div className="text-secondary fs-2">{r.ts ? new Date(r.ts).toLocaleTimeString() : ''}</div>
                </div>
              )) : <div className="text-center text-secondary p-4">No check-ins yet today</div>}
            </Card.Body>
          </Card>
        </Col>
      </Row>
    </>
  )
}
