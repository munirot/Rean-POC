import { useEffect, useState } from 'react'
import { Card, Table, Badge, Form } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'
import { useToast } from '../components/Layout'

// Staff/admin queue for student leave requests. Approving reclassifies that
// student's absences in the range to Excused, which takes them out of the
// attendance-rate denominator; rejecting changes nothing. The backend scopes the
// list to the caller's students, so a teacher only ever sees their own.
function StateBadge({ state }) {
  if (state === 'approved') return <Badge bg="success">Approved</Badge>
  if (state === 'rejected') return <Badge bg="secondary">Rejected</Badge>
  return <Badge bg="info" text="dark">Pending</Badge>
}

export default function Leave() {
  const [items, setItems] = useState([])
  const [onlyPending, setOnlyPending] = useState(true)
  const [busyId, setBusyId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useToast()

  const load = () => {
    setLoading(true)
    api.leave(onlyPending ? 'pending' : undefined)
      .then(setItems).catch((e) => setToast('Could not load requests: ' + e.message))
      .finally(() => setLoading(false))
  }
  useEffect(load, [onlyPending])

  async function resolve(l, action) {
    setBusyId(l.id)
    try {
      const r = await api.resolveLeave(l.id, { action })
      const n = r.leave?.appliedRows || 0
      setToast(action === 'approve'
        ? `Approved — ${n} absence${n === 1 ? '' : 's'} marked excused`
        : `Request from ${l.name} rejected`)
      load()
    } catch (e) {
      setToast('Could not resolve: ' + e.message)
    } finally { setBusyId(null) }
  }

  const pending = items.filter((l) => l.state === 'pending').length

  return (
    <>
      <PageHeader heading="Leave requests"
        subHeading="Approving excuses that student's absences for the dates requested">
        <Form.Check type="switch" id="only-pending" label="Pending only"
          checked={onlyPending} onChange={(e) => setOnlyPending(e.target.checked)} />
      </PageHeader>

      <Card>
        <Card.Header className="fw-bold text-primary d-flex align-items-center gap-2">
          Queue
          {pending > 0 && <Badge bg="info" text="dark">{pending} pending</Badge>}
        </Card.Header>
        <Card.Body className="p-0">
          <Table responsive className="camu-table mb-0">
            <thead>
              <tr>
                <th>Student</th><th>Dates</th><th>Reason</th><th>Document</th>
                <th>State</th><th className="text-end">Action</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan="6" className="text-center text-secondary p-4">Loading…</td></tr>
              ) : items.length ? items.map((l) => (
                <tr key={l.id} className="table-list_body">
                  <td className="fs-3 p-3">
                    <div className="fw-semibold">{l.name}</div>
                    <div className="text-secondary">{l.sid}{l.cls ? ` · ${l.cls}` : ''}</div>
                  </td>
                  <td className="fs-3 p-3">
                    {l.startDate === l.endDate
                      ? l.startDate
                      : <>{l.startDate} → {l.endDate}</>}
                  </td>
                  <td className="fs-3 p-3" style={{ maxWidth: 260 }}>
                    {l.reason || <span className="text-secondary">—</span>}
                  </td>
                  <td className="fs-3 p-3">
                    {l.document || <span className="text-secondary">—</span>}
                  </td>
                  <td className="fs-3 p-3">
                    <StateBadge state={l.state} />
                    {l.state === 'approved' && (
                      <div className="text-secondary fs-2 mt-1">
                        {l.appliedRows} row{l.appliedRows === 1 ? '' : 's'} excused
                      </div>
                    )}
                  </td>
                  <td className="fs-3 p-3 text-end">
                    {l.state === 'pending' ? (
                      <div className="d-flex gap-2 justify-content-end">
                        <Button variant="primary" icon="check" disabled={busyId === l.id}
                          onClick={() => resolve(l, 'approve')}>Approve</Button>
                        <Button variant="secondary" icon="close" disabled={busyId === l.id}
                          onClick={() => resolve(l, 'reject')}>Reject</Button>
                      </div>
                    ) : <span className="text-secondary">Resolved</span>}
                  </td>
                </tr>
              )) : (
                <tr><td colSpan="6" className="text-center text-secondary p-4">
                  {onlyPending ? 'No pending requests — you\'re all caught up.' : 'No leave requests yet.'}
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
