import { useEffect, useRef, useState } from 'react'
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

      <CapturePolicies setToast={setToast} />
      <SessionAudit />

      {toast}
    </>
  )
}

// Pre-rollout check. Turning window enforcement on against legacy data can start
// refusing marks, so show the admin exactly how the session labels already in the
// attendance log line up with the configured periods. Read-only.
function SessionAudit() {
  const [a, setA] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  // The audit scans the attendance log, and its answer only changes when periods
  // change — so fetch it once. The ref also absorbs StrictMode's double effect
  // invocation in dev, which would otherwise fire this twice on every visit.
  const fetched = useRef(false)

  const run = () => {
    setBusy(true)
    api.adminSessionAudit().then((r) => { setA(r); setErr('') })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false))
  }

  useEffect(() => {
    if (fetched.current) return
    fetched.current = true
    run()
  }, [])

  if (err) return <Alert variant="danger" className="fs-3 mt-4">{err}</Alert>
  if (!a) return null

  const noPeriods = a.periodsConfigured === 0
  const clean = !noPeriods && !a.unmatchedRecords && !a.variantGroups

  return (
    <Card className="mt-4">
      <Card.Header className="fw-bold text-primary d-flex align-items-center gap-2">
        Existing data check
        {clean && <Badge bg="success">Ready</Badge>}
        {!!a.unmatchedRecords && <Badge bg="danger">{a.unmatchedRecords} would be refused</Badge>}
        {!!a.variantGroups && <Badge bg="warning" text="dark">{a.variantGroups} label variant(s)</Badge>}
        <span className="ms-auto">
          <Button variant="secondary" icon="refresh" disabled={busy} onClick={run}>
            {busy ? 'Checking…' : 'Re-check'}
          </Button>
        </span>
      </Card.Header>
      <Card.Body>
        <p className="fs-3 text-secondary">
          {a.records.toLocaleString()} attendance record(s) across{' '}
          {a.distinct} session label(s).
        </p>

        {noPeriods && (
          <Alert variant="secondary" className="fs-3">
            No periods configured yet — nothing is enforced, so nothing here can break.
            Define periods above, then re-check before switching enforcement on.
          </Alert>
        )}
        {!!a.unmatchedRecords && (
          <Alert variant="danger" className="fs-3">
            Some existing labels match no configured period. Once enforcement is on,
            an automated mark carrying one of them is refused. Add a period whose
            <strong> name</strong> matches, or rename the period to match the data.
          </Alert>
        )}
        {!!a.variantGroups && (
          <Alert variant="warning" className="fs-3">
            The same label is stored in more than one form. These still resolve
            (matching ignores case and spacing), but attendance de-duplicates on the
            exact label — so one sitting can end up split across several rows.
          </Alert>
        )}

        <Table responsive className="camu-table mb-0 align-middle">
          <thead>
            <tr><th>Session label</th><th>Records</th><th>Maps to</th><th>Also stored as</th></tr>
          </thead>
          <tbody>
            {a.rows.map((r) => (
              <tr key={String(r.session)} className="table-list_body">
                <td className="fs-3 p-3"><code>{JSON.stringify(r.session)}</code></td>
                <td className="fs-3 p-3">{r.count.toLocaleString()}</td>
                <td className="fs-3 p-3">
                  {noPeriods ? <span className="text-secondary">—</span>
                    : r.matched ? <Badge bg="success">{r.period}</Badge>
                      : <Badge bg="danger">no period</Badge>}
                </td>
                <td className="fs-3 p-3">
                  {r.variants.length
                    ? r.variants.map((v) => <code key={v} className="me-2">{JSON.stringify(v)}</code>)
                    : <span className="text-secondary">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card.Body>
    </Card>
  )
}

// Capture mode per scope. Most specific wins: a section policy beats a course
// policy, which beats the institute default. Removing a row makes that scope
// inherit its parent again.
const MODE_LABEL = {
  individual: 'Individual scan',
  class_camera: 'Whole-class camera',
  both: 'Both',
}
const NEW_POLICY = { scope: 'institute', CrID: '', SecID: '', mode: 'individual', allowIndividualFallback: true }

function CapturePolicies({ setToast }) {
  const [rows, setRows] = useState(null)
  const [camEnabled, setCamEnabled] = useState(false)
  const [courses, setCourses] = useState([])
  const [draft, setDraft] = useState(NEW_POLICY)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = () => api.adminPolicies()
    .then((r) => { setRows(r.policies || []); setCamEnabled(!!r.classCameraEnabled) })
    .catch((e) => { setErr(e.message); setRows([]) })
  useEffect(() => { load() }, [])
  useEffect(() => { api.adminCourses().then((r) => setCourses(r.courses || [])).catch(() => {}) }, [])

  // Resolve ids to readable names so the table doesn't show bare CR001 / SC001.
  const course = (id) => courses.find((c) => c.CrID === id)
  const courseName = (id) => (id ? (course(id)?.name || id) : '—')
  const sectionName = (crid, sid) => {
    if (!sid) return '—'
    return course(crid)?.sections?.find((s) => s.SecID === sid)?.name || sid
  }
  const draftSections = course(draft.CrID)?.sections || []

  async function save() {
    setErr(''); setBusy(true)
    try {
      await api.saveAdminPolicy({
        scope: draft.scope,
        CrID: draft.scope === 'institute' ? null : draft.CrID.trim() || null,
        SecID: draft.scope === 'section' ? draft.SecID.trim() || null : null,
        mode: draft.mode,
        allowIndividualFallback: draft.allowIndividualFallback,
      })
      setDraft(NEW_POLICY)
      setToast('Capture mode saved')
      await load()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  async function remove(id) {
    try { await api.deleteAdminPolicy(id); setToast('Override removed'); await load() }
    catch (e) { setErr(e.message) }
  }

  if (rows === null) return null

  return (
    <Card className="mt-4">
      <Card.Header className="fw-bold text-primary">Capture mode</Card.Header>
      <Card.Body>
        {!camEnabled && (
          <Alert variant="secondary" className="fs-3 mb-3">
            <strong>Whole-class camera capture is not built yet.</strong> There is a
            design and a measurement script for it, but no code that reads a
            classroom camera or marks a class from one — so the mode is not
            selectable. <code>CLASS_CAM_ENABLED</code> only unlocks the setting for
            development; it does not add the capability.
          </Alert>
        )}
        {err && <Alert variant="danger" className="fs-3">{err}</Alert>}

        <Table responsive className="camu-table mb-3 align-middle">
          <thead>
            <tr><th>Scope</th><th>Course</th><th>Section</th><th>Mode</th>
              <th>Individual fallback</th><th></th></tr>
          </thead>
          <tbody>
            {rows.length ? rows.map((p) => (
              <tr key={p.id} className="table-list_body">
                <td className="fs-3 p-3 text-capitalize">{p.scope}</td>
                <td className="fs-3 p-3">{courseName(p.CrID)}</td>
                <td className="fs-3 p-3">{sectionName(p.CrID, p.SecID)}</td>
                <td className="fs-3 p-3">
                  <Badge bg={p.mode === 'individual' ? 'secondary' : 'primary'}>
                    {MODE_LABEL[p.mode] || p.mode}
                  </Badge>
                </td>
                <td className="fs-3 p-3">
                  {p.mode === 'class_camera'
                    ? (p.allowIndividualFallback ? 'Allowed' : 'Off')
                    : <span className="text-secondary">n/a</span>}
                </td>
                <td className="fs-3 p-3 text-end">
                  <Button variant="secondary" icon="delete"
                    onClick={() => remove(p.id)}>Remove</Button>
                </td>
              </tr>
            )) : (
              <tr><td colSpan="6" className="text-center text-secondary p-4">
                No overrides — every class uses the default (individual scan).
              </td></tr>
            )}
          </tbody>
        </Table>

        <div className="d-flex flex-wrap gap-2 align-items-end">
          <div>
            <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Scope</Form.Label>
            <Form.Select size="sm" value={draft.scope}
              onChange={(e) => setDraft({ ...draft, scope: e.target.value })}>
              <option value="institute">Institute</option>
              <option value="course">Course</option>
              <option value="section">Section</option>
            </Form.Select>
          </div>
          {draft.scope !== 'institute' && (
            <div>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Course</Form.Label>
              <Form.Select size="sm" value={draft.CrID}
                onChange={(e) => setDraft({ ...draft, CrID: e.target.value, SecID: '' })}>
                <option value="">Choose a course…</option>
                {courses.map((c) => (
                  <option key={c.CrID} value={c.CrID}>{c.name} ({c.students})</option>
                ))}
              </Form.Select>
            </div>
          )}
          {draft.scope === 'section' && (
            <div>
              <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Section</Form.Label>
              <Form.Select size="sm" value={draft.SecID} disabled={!draft.CrID}
                onChange={(e) => setDraft({ ...draft, SecID: e.target.value })}>
                <option value="">{draft.CrID ? 'Choose a section…' : 'Pick a course first'}</option>
                {draftSections.map((s) => (
                  <option key={s.SecID} value={s.SecID}>{s.name} ({s.students})</option>
                ))}
              </Form.Select>
            </div>
          )}
          <div>
            <Form.Label className="fs-2 text-secondary fw-semibold mb-1">Mode</Form.Label>
            <Form.Select size="sm" value={draft.mode}
              onChange={(e) => setDraft({ ...draft, mode: e.target.value })}>
              <option value="individual">Individual scan</option>
              <option value="class_camera" disabled={!camEnabled}>
                Whole-class camera{camEnabled ? '' : ' — not built yet'}
              </option>
              <option value="both" disabled={!camEnabled}>
                Both{camEnabled ? '' : ' — not built yet'}
              </option>
            </Form.Select>
          </div>
          {draft.mode === 'class_camera' && (
            <Form.Check type="switch" label="Allow individual fallback"
              checked={draft.allowIndividualFallback}
              onChange={(e) => setDraft({ ...draft, allowIndividualFallback: e.target.checked })} />
          )}
          <Button variant="primary" icon="add" disabled={busy} onClick={save}>
            {busy ? 'Saving…' : 'Set mode'}
          </Button>
        </div>
        <div className="text-secondary fs-2 mt-2">
          Most specific wins: a section policy overrides its course, which overrides
          the institute. Removing a row makes that scope inherit its parent again.
        </div>
      </Card.Body>
    </Card>
  )
}
