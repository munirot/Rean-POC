import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Table, Form, Badge, Row, Col } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import { api } from '../api'

export default function Students() {
  const [list, setList] = useState(null)
  const [q, setQ] = useState('')
  const [cls, setCls] = useState('')
  const nav = useNavigate()

  useEffect(() => { api.students().then(setList).catch(() => setList([])) }, [])

  const classes = useMemo(
    () => [...new Set((list || []).map((s) => s.cls).filter(Boolean))].sort(), [list])
  const filtered = useMemo(() => (list || []).filter((s) => {
    const hit = (s.name + ' ' + s.sid).toLowerCase().includes(q.toLowerCase())
    return hit && (!cls || s.cls === cls)
  }), [list, q, cls])

  const badge = (s) => s.enrolled
    ? <Badge bg="success">Enrolled</Badge>
    : s.expected ? <Badge bg="warning" text="dark">Profile expected</Badge>
    : <Badge bg="light" text="dark">New student</Badge>

  return (
    <>
      <PageHeader heading="Students"
        subHeading={list ? `${list.length} students · ${list.filter((s) => s.enrolled).length} face-enrolled` : 'Loading…'} />

      <Card>
        <Card.Body>
          <Row className="g-2 mb-3 align-items-end">
            <Col sm={5} md={4}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Search</Form.Label>
              <Form.Control size="sm" placeholder="Name or ID…" value={q} onChange={(e) => setQ(e.target.value)} />
            </Col>
            <Col sm={4} md={3}>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Class</Form.Label>
              <Form.Select size="sm" value={cls} onChange={(e) => setCls(e.target.value)}>
                <option value="">All classes</option>
                {classes.map((c) => <option key={c} value={c}>{c}</option>)}
              </Form.Select>
            </Col>
            <Col className="text-end text-secondary fs-2">{filtered.length} shown</Col>
          </Row>

          <Table responsive hover className="camu-table align-middle mb-0">
            <thead><tr><th></th><th>Student</th><th>ID</th><th>Class</th><th>Face profile</th></tr></thead>
            <tbody>
              {filtered.map((s) => (
                <tr key={s.sid} className="table-list_body rowlink" onClick={() => nav('/students/' + s.sid)}>
                  <td className="p-3" style={{ width: 56 }}>
                    <span className="avatar">{s.thumb
                      ? <img src={s.thumb} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : '—'}</span>
                  </td>
                  <td className="fs-3 p-3 fw-semibold text-primary">{s.name}</td>
                  <td className="fs-3 p-3 text-secondary">{s.sid}</td>
                  <td className="fs-3 p-3">{s.cls}</td>
                  <td className="fs-3 p-3">{badge(s)}</td>
                </tr>
              ))}
              {list && !filtered.length && (
                <tr><td colSpan="5" className="text-center text-secondary p-4">No students match</td></tr>)}
            </tbody>
          </Table>
        </Card.Body>
      </Card>
    </>
  )
}
