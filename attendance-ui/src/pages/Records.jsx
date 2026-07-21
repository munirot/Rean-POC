import { useEffect, useMemo, useState } from 'react'
import { Card, Table, Form, Row, Col, Badge } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api, todayStr } from '../api'
import { useToast } from '../components/Layout'
import { getSession } from '../auth'

export default function Records() {
  const session0 = getSession()
  const isStudent = session0?.type === 'student'
  const mySid = session0?.sid
  const [date, setDate] = useState('')            // default: all dates (data is historical)
  const [cls, setCls] = useState('')
  const [session, setSession] = useState('')
  const [rows, setRows] = useState([])
  const [classes, setClasses] = useState([])
  const [toast, setToast] = useToast()

  const load = () => api.attendance({ date, cls, session, sid: isStudent ? mySid : undefined })
    .then(setRows).catch(() => setRows([]))
  useEffect(() => { load() }, [date, cls, session])
  useEffect(() => { if (!isStudent) api.students().then((s) =>
    setClasses([...new Set(s.map((x) => x.cls).filter(Boolean))].sort())).catch(() => {}) }, [isStudent])

  const sessions = useMemo(() => [...new Set(rows.map((r) => r.session).filter(Boolean))].sort(), [rows])
  async function del(id) { await api.deleteAttendance(id); setToast('Record removed'); load() }

  function exportCsv() {
    const head = ['sid', 'name', 'class', 'date', 'session', 'time', 'source', 'similarity']
    const lines = [head.join(',')].concat(rows.map((r) => [
      r.sid, `"${r.name}"`, r.cls || '', r.date, r.session || '',
      r.ts ? new Date(r.ts).toLocaleTimeString() : '', r.source || '', r.similarity ?? '',
    ].join(',')))
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }))
    a.download = `attendance_${date}.csv`; a.click()
  }

  return (
    <>
      <PageHeader heading="Attendance Records" subHeading={`${rows.length} record(s)`}>
        <Button variant="secondary" icon="download" onClick={exportCsv} disabled={!rows.length}>Export CSV</Button>
      </PageHeader>

      <Card>
        <Card.Body>
          <Row className="g-2 mb-3 align-items-end">
            <Col xs={6} sm={3}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Date</Form.Label>
              <Form.Control size="sm" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Col>
            {!isStudent && (
              <Col xs={6} sm={3}>
                <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Class</Form.Label>
                <Form.Select size="sm" value={cls} onChange={(e) => setCls(e.target.value)}>
                  <option value="">All</option>
                  {classes.map((c) => <option key={c} value={c}>{c}</option>)}
                </Form.Select>
              </Col>
            )}
            <Col xs={6} sm={3}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Session</Form.Label>
              <Form.Select size="sm" value={session} onChange={(e) => setSession(e.target.value)}>
                <option value="">All</option>
                {sessions.map((s) => <option key={s} value={s}>{s}</option>)}
              </Form.Select>
            </Col>
          </Row>

          <Table responsive hover className="camu-table align-middle mb-0">
            <thead><tr>
              <th>Student</th><th>ID</th><th>Class</th><th>Date</th><th>Session</th>
              <th>Status</th><th>Time</th><th>Source</th><th>Match</th><th></th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="table-list_body">
                  <td className="fs-3 p-3 fw-semibold text-primary">{r.name}</td>
                  <td className="fs-3 p-3 text-secondary">{r.sid}</td>
                  <td className="fs-3 p-3">{r.cls}</td>
                  <td className="fs-3 p-3">{r.date}</td>
                  <td className="fs-3 p-3">{r.session}</td>
                  <td className="fs-3 p-3">
                    <Badge bg={r.status === 'P' ? 'success' : r.status === 'L' ? 'warning' : r.status === 'A' ? 'danger' : 'light'}
                      text={r.status === 'L' || !r.status ? 'dark' : undefined}>
                      {r.status === 'P' ? 'Present' : r.status === 'L' ? 'Late' : r.status === 'A' ? 'Absent' : (r.status || '—')}
                    </Badge>
                  </td>
                  <td className="fs-3 p-3">{r.ts ? new Date(r.ts).toLocaleTimeString() : '—'}</td>
                  <td className="fs-3 p-3"><Badge bg="light" text="dark">{r.source}</Badge></td>
                  <td className="fs-3 p-3">{r.similarity != null ? Math.round(r.similarity * 100) + '%' : '—'}</td>
                  <td className="fs-3 p-3"><Button size="sm" variant="danger" icon="cancel" onClick={() => del(r.id)}>Undo</Button></td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan="10" className="text-center text-secondary p-4">No records for these filters</td></tr>}
            </tbody>
          </Table>
        </Card.Body>
      </Card>
      {toast}
    </>
  )
}
