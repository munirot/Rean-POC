import { useEffect, useState } from 'react'
import { Card, Table, Badge, Form } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'
import { useToast } from '../components/Layout'

// Staff/admin queue for student "I was present" challenges. Approving corrects
// the attendance row to Present; rejecting leaves it unchanged. The backend
// scopes this list to the caller's students, so a teacher only sees their own.
const RECORDED = { A: 'Absent', L: 'Late', P: 'Present' }

function StateBadge({ state }) {
  if (state === 'approved') return <Badge bg="success">Approved · corrected</Badge>
  if (state === 'rejected') return <Badge bg="secondary">Rejected</Badge>
  return <Badge bg="info" text="dark">Open</Badge>
}

export default function Disputes() {
  const [items, setItems] = useState([])
  const [onlyOpen, setOnlyOpen] = useState(true)
  const [busyId, setBusyId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useToast()

  const load = () => {
    setLoading(true)
    api.disputes(onlyOpen ? 'open' : undefined)
      .then(setItems).catch((e) => setToast('Could not load disputes: ' + e.message))
      .finally(() => setLoading(false))
  }
  useEffect(load, [onlyOpen])

  async function resolve(d, action) {
    setBusyId(d.id)
    try {
      await api.resolveDispute(d.id, { action })
      setToast(action === 'approve' ? `Corrected ${d.name} to Present` : `Dispute from ${d.name} rejected`)
      load()
    } catch (e) {
      setToast('Could not resolve: ' + e.message)
    } finally { setBusyId(null) }
  }

  const openCount = items.filter((d) => d.state === 'open').length

  return (
    <>
      <PageHeader heading="Attendance disputes"
        subHeading="Students flagging a mark as wrong — approve to correct it to Present">
        <Form.Check type="switch" id="only-open" label="Open only"
          checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} />
      </PageHeader>

      <Card>
        <Card.Header className="fw-bold text-primary d-flex align-items-center gap-2">
          Queue
          {openCount > 0 && <Badge bg="info" text="dark">{openCount} open</Badge>}
        </Card.Header>
        <Card.Body className="p-0">
          <Table responsive className="camu-table mb-0">
            <thead>
              <tr>
                <th>Student</th><th>Date</th><th>Session</th><th>Subject</th>
                <th>Marked</th><th>Reason</th><th>State</th><th className="text-end">Action</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan="8" className="text-center text-secondary p-4">Loading…</td></tr>
              ) : items.length ? items.map((d) => (
                <tr key={d.id} className="table-list_body">
                  <td className="fs-3 p-3">
                    <div className="fw-semibold">{d.name}</div>
                    <div className="text-secondary">{d.sid}{d.cls ? ` · ${d.cls}` : ''}</div>
                  </td>
                  <td className="fs-3 p-3">{d.date}</td>
                  <td className="fs-3 p-3">{d.session}</td>
                  <td className="fs-3 p-3">{d.subNa || '—'}</td>
                  <td className="fs-3 p-3">
                    <Badge bg={d.recordedStatus === 'L' ? 'warning' : 'danger'}
                      text={d.recordedStatus === 'L' ? 'dark' : undefined}>
                      {RECORDED[d.recordedStatus] || d.recordedStatus}
                    </Badge>
                  </td>
                  <td className="fs-3 p-3" style={{ maxWidth: 260 }}>
                    {d.reason || <span className="text-secondary">—</span>}
                  </td>
                  <td className="fs-3 p-3"><StateBadge state={d.state} /></td>
                  <td className="fs-3 p-3 text-end">
                    {d.state === 'open' ? (
                      <div className="d-flex gap-2 justify-content-end">
                        <Button variant="primary" icon="check" disabled={busyId === d.id}
                          onClick={() => resolve(d, 'approve')}>Approve</Button>
                        <Button variant="secondary" icon="close" disabled={busyId === d.id}
                          onClick={() => resolve(d, 'reject')}>Reject</Button>
                      </div>
                    ) : <span className="text-secondary">Resolved</span>}
                  </td>
                </tr>
              )) : (
                <tr><td colSpan="8" className="text-center text-secondary p-4">
                  {onlyOpen ? 'No open disputes — you\'re all caught up.' : 'No disputes yet.'}
                </td></tr>
              )}
            </tbody>
          </Table>
        </Card.Body>
      </Card>

      {toast}
    </>
  )
}
