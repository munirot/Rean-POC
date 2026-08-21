import { useEffect, useState } from 'react'
import { Card, Table, Form, Badge, Alert } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'
import { useToast } from '../components/Layout'

// Admin configuration for attendance capture windows. A period is a named
// clock-time window ("Morning 08:00-08:20"); automated marking (face scan and
// student self check-in) is confined to it, with a grace tail that counts as
// Late. Staff corrections are never window-gated, so a mistake here can't stop a
// teacher fixing attendance by hand.
const BLANK = { code: '', name: '', start: '08:00', end: '08:20', graceMinutes: 10 }

const isTime = (v) => /^([01]\d|2[0-3]):[0-5]\d$/.test(v || '')

export default function Settings() {
  const [rows, setRows] = useState(null)
  const [enforce, setEnforce] = useState(null)   // read-only: server env flag
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [toast, setToast] = useToast()

  useEffect(() => {
    api.adminPeriods()
      .then((r) => setRows(r.periods?.length ? r.periods : [{ ...BLANK, code: 'MORNING', name: 'Morning' }]))
      .catch((e) => { setErr(e.message); setRows([]) })
    api.attendancePolicy().then((p) => setEnforce(p.enforceWindow)).catch(() => {})
  }, [])

  const update = (i, key, val) =>
    setRows((r) => r.map((row, k) => (k === i ? { ...row, [key]: val } : row)))
  const addRow = () => setRows((r) => [...r, { ...BLANK }])
  const removeRow = (i) => setRows((r) => r.filter((_, k) => k !== i))

  // Mirror the server's validation so the admin gets feedback before saving.
  function localError() {
    const seen = new Set()
    for (const [i, p] of rows.entries()) {
      const where = p.code || `row ${i + 1}`
      if (!p.code.trim()) return `Row ${i + 1} needs a code`
      if (!p.name.trim()) return `${where} needs a name`
      if (seen.has(p.code.trim().toLowerCase())) return `Duplicate code '${p.code}'`
      seen.add(p.code.trim().toLowerCase())
      if (!isTime(p.start)) return `${where} has an invalid start time (HH:MM)`
      if (!isTime(p.end)) return `${where} has an invalid end time (HH:MM)`
      if (p.end < p.start) return `${where} ends before it starts`
      if (Number(p.graceMinutes) < 0) return `${where} cannot have negative grace`
    }
    return ''
  }

  async function save() {
    const bad = localError()
    if (bad) { setErr(bad); return }
    setErr(''); setBusy(true)
    try {
      const r = await api.saveAdminPeriods(rows.map((p) => ({
        ...p, graceMinutes: Number(p.graceMinutes) || 0,
      })))
      setRows(r.periods)
      setToast('Capture periods saved')
    } catch (e) {
      setErr(e.message)
    } finally { setBusy(false) }
  }

  if (rows === null) return <div className="text-secondary p-4">Loading…</div>

  return (
    <>
      <PageHeader heading="Attendance settings"
        subHeading="Capture periods — when face scan and self check-in may mark attendance">
        <Button variant="secondary" icon="add" onClick={addRow}>Add period</Button>
        <Button variant="primary" icon="save" disabled={busy} onClick={save}>
          {busy ? 'Saving…' : 'Save'}
        </Button>
      </PageHeader>

      {enforce === false && (
        <Alert variant="secondary" className="fs-3">
          Window enforcement is currently <strong>off</strong> on the server
          (<code>ATTENDANCE_ENFORCE_WINDOW=false</code>), so these periods are saved
          but not yet applied. Turn it on to start confining capture to them.
        </Alert>
      )}
      {err && <Alert variant="danger" className="fs-3">{err}</Alert>}

      <Card>
        <Card.Header className="fw-bold text-primary d-flex align-items-center gap-2">
          Capture periods
          <Badge bg="light" text="dark">{rows.length}</Badge>
        </Card.Header>
        <Card.Body className="p-0">
          <Table responsive className="camu-table mb-0 align-middle">
            <thead>
              <tr>
                <th>Code</th><th>Name</th><th>Opens</th><th>Closes</th>
                <th>Grace (min)</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.length ? rows.map((p, i) => (
                <tr key={i} className="table-list_body">
                  <td className="p-2" style={{ maxWidth: 140 }}>
                    <Form.Control size="sm" value={p.code} placeholder="MORNING"
                      onChange={(e) => update(i, 'code', e.target.value)} />
                  </td>
                  <td className="p-2" style={{ maxWidth: 180 }}>
                    <Form.Control size="sm" value={p.name} placeholder="Morning"
                      onChange={(e) => update(i, 'name', e.target.value)} />
                  </td>
                  <td className="p-2" style={{ maxWidth: 120 }}>
                    <Form.Control size="sm" type="time" value={p.start}
                      onChange={(e) => update(i, 'start', e.target.value)} />
                  </td>
                  <td className="p-2" style={{ maxWidth: 120 }}>
                    <Form.Control size="sm" type="time" value={p.end}
                      onChange={(e) => update(i, 'end', e.target.value)} />
                  </td>
                  <td className="p-2" style={{ maxWidth: 110 }}>
                    <Form.Control size="sm" type="number" min="0" value={p.graceMinutes}
                      onChange={(e) => update(i, 'graceMinutes', e.target.value)} />
                  </td>
                  <td className="p-2 text-end">
                    <Button variant="secondary" icon="delete"
                      onClick={() => removeRow(i)}>Remove</Button>
                  </td>
                </tr>
              )) : (
                <tr><td colSpan="6" className="text-center text-secondary p-4">
                  No periods yet — add one to define when attendance may be taken.
                </td></tr>
              )}
            </tbody>
          </Table>
        </Card.Body>
      </Card>

      <div className="text-secondary fs-2 mt-2">
        Marks inside a period count as <strong>Present</strong>; marks in the grace
        tail count as <strong>Late</strong>. Outside both, automated capture is
        refused — a teacher can still mark or correct attendance manually.
      </div>

      {toast}
    </>
  )
}
