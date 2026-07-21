import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Row, Col, Card, Table, Badge } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import { api, todayStr } from '../api'

// Signal code -> label + badge variant. 'at_risk' is the headline flag.
const SIGNALS = {
  at_risk: { label: 'At risk', bg: 'danger' },
  attendance_low: { label: 'Low attendance', bg: 'warning' },
  attendance_declining: { label: 'Attendance declining', bg: 'warning' },
  missing_assignments: { label: 'Missing assignments', bg: 'warning' },
  quiz_avg_below_60: { label: 'Low quiz average', bg: 'warning' },
}

function SignalBadges({ signals }) {
  if (!signals?.length) return <Badge bg="success">On track</Badge>
  const ordered = [...signals].sort((a) => (a === 'at_risk' ? -1 : 0))
  return (
    <div className="d-flex flex-wrap gap-1">
      {ordered.map((s) => {
        const m = SIGNALS[s] || { label: s, bg: 'secondary' }
        return <Badge key={s} bg={m.bg} text={m.bg === 'warning' ? 'dark' : undefined}>{m.label}</Badge>
      })}
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

export default function Insights() {
  const [classes, setClasses] = useState([])
  const [cls, setCls] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const nav = useNavigate()

  // Populate the class picker from today's summary (same source the dashboard uses).
  useEffect(() => {
    api.summary(todayStr())
      .then((s) => {
        const names = (s.by_class || []).map((c) => c.cls).filter(Boolean)
        setClasses(names)
        if (names.length) setCls((c) => c || names[0])
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!cls) return
    setLoading(true)
    api.cohort(cls)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [cls])

  const pct = (v) => (v == null ? '—' : `${v}%`)

  return (
    <>
      <PageHeader heading="Insights" subHeading="At-risk signals across a class — attendance + assignments">
        <select className="form-select" style={{ maxWidth: 260 }}
          value={cls} onChange={(e) => setCls(e.target.value)}>
          {classes.length === 0 && <option value="">No classes</option>}
          {classes.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </PageHeader>

      <Row className="g-3 mb-1">
        <Col md={6} xl={3}><Stat label="Students" value={data ? data.total : '—'} sub={cls} /></Col>
        <Col md={6} xl={3}><Stat label="Flagged" value={data ? data.flagged : '—'}
          color="var(--warning)" sub={data ? `${data.total - data.flagged} on track` : ''} /></Col>
      </Row>

      <Card className="mt-2">
        <Card.Header className="fw-bold text-primary">
          Students {data ? `· ${data.total}` : ''} <span className="text-secondary fw-normal fs-2">(most at-risk first)</span>
        </Card.Header>
        <Card.Body className="p-0">
          <Table responsive className="camu-table mb-0">
            <thead>
              <tr><th>Student</th><th>Attendance</th><th>Quiz avg</th><th>Missing</th><th>Signals</th></tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan="5" className="text-center text-secondary p-4">Loading…</td></tr>}
              {!loading && data?.students?.length ? data.students.map((r) => (
                <tr key={r.sid} className="table-list_body" style={{ cursor: 'pointer' }}
                  onClick={() => nav(`/students/${r.sid}`)}>
                  <td className="fs-3 p-3">
                    <div className="fw-semibold text-primary">{r.name}</div>
                    <div className="text-secondary fs-2">{r.sid}</div>
                  </td>
                  <td className="fs-3 p-3">{pct(r.rate)}</td>
                  <td className="fs-3 p-3">{pct(r.quizAvg)}</td>
                  <td className="fs-3 p-3">{r.missing || 0}</td>
                  <td className="fs-3 p-3"><SignalBadges signals={r.signals} /></td>
                </tr>
              )) : (!loading && (
                <tr><td colSpan="5" className="text-center text-secondary p-4">No students in this class</td></tr>
              ))}
            </tbody>
          </Table>
        </Card.Body>
      </Card>
    </>
  )
}
