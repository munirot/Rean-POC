import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Badge, Modal } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'
import GuidedEnroll from '../components/GuidedEnroll'
import { useToast } from '../components/Layout'

function Field({ k, v }) {
  return (
    <div className="field-row">
      <span className="k">{k}</span><span className="sep">:</span>
      <span>{v}</span>
    </div>
  )
}

// Signal code -> human label + badge variant. 'at_risk' is the headline flag.
const SIGNALS = {
  at_risk: { label: 'At risk', bg: 'danger' },
  attendance_low: { label: 'Low attendance', bg: 'warning' },
  attendance_declining: { label: 'Attendance declining', bg: 'warning' },
  missing_assignments: { label: 'Missing assignments', bg: 'warning' },
  quiz_avg_below_60: { label: 'Low quiz average', bg: 'warning' },
}

function SignalBadges({ signals }) {
  if (!signals?.length) return <Badge bg="success">On track</Badge>
  // headline flag first, then the rest
  const ordered = [...signals].sort((a) => (a === 'at_risk' ? -1 : 0))
  return (
    <div className="d-flex flex-wrap gap-2">
      {ordered.map((s) => {
        const m = SIGNALS[s] || { label: s, bg: 'secondary' }
        return <Badge key={s} bg={m.bg} text={m.bg === 'warning' ? 'dark' : undefined}>{m.label}</Badge>
      })}
    </div>
  )
}

function Metric({ label, value, sub }) {
  return (
    <div className="me-4 mb-2">
      <div className="fs-2 text-secondary">{label}</div>
      <div className="fw-bold text-primary" style={{ fontSize: '1.4rem' }}>{value}</div>
      {sub && <div className="fs-2 text-secondary">{sub}</div>}
    </div>
  )
}

// Teacher-facing improvement suggestions — loaded on demand, never auto-applied.
// Same deterministic source the chat's get_plan tool uses, so the advice here and
// the advice in "Ask AI" are always identical. This card needs no language model,
// which is why it stays even though chat can answer the same question.
function ImprovementPlan({ sid, name }) {
  const [plan, setPlan] = useState(null)
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()

  async function generate() {
    setBusy(true)
    try { setPlan(await api.studentPlan(sid)) } catch { setPlan(null) } finally { setBusy(false) }
  }

  // Hand the student off to chat for follow-up questions. Passed via router
  // state rather than a query string so the id stays out of the URL.
  const discuss = () => nav('/chat', { state: { sid, name } })

  return (
    <Card className="mb-4">
      <Card.Header className="d-flex justify-content-between align-items-center">
        <span>Improvement suggestions</span>
        <div className="d-flex gap-2">
          <Button variant="secondary" icon="forum" onClick={discuss}>Discuss in chat</Button>
          <Button variant="secondary" icon="lightbulb" disabled={busy} onClick={generate}>
            {busy ? 'Generating…' : plan ? 'Refresh' : 'Generate'}
          </Button>
        </div>
      </Card.Header>
      <Card.Body>
        {!plan && !busy && (
          <p className="text-secondary mb-0">
            Generate suggestions based on this student's attendance and assignment signals.
          </p>
        )}
        {plan && (
          <>
            <p className="fw-semibold text-primary">{plan.summary}</p>
            {plan.suggestions.length === 0 ? (
              <p className="text-secondary mb-0">No concerns flagged — nothing to suggest right now.</p>
            ) : (
              plan.suggestions.map((s, i) => (
                <div key={i} className="mb-3">
                  <div className="fw-semibold">
                    <Badge bg="secondary" className="me-2">{s.area}</Badge>{s.observation}
                  </div>
                  <ul className="mb-0 mt-1">
                    {s.actions.map((a, k) => <li key={k} className="text-secondary">{a}</li>)}
                  </ul>
                </div>
              ))
            )}
            <div className="text-secondary fs-2 mt-2">{plan.disclaimer}</div>
          </>
        )}
      </Card.Body>
    </Card>
  )
}

function StudentSuccess({ prof }) {
  if (!prof) return null
  const { attendance: a, academics: ac, signals } = prof
  const pct = (v) => (v == null ? '—' : `${v}%`)
  const num = (v) => (v == null ? '—' : v)
  const cats = Object.entries(ac.byCategory || {})
  return (
    <Card className="mb-4">
      <Card.Header className="d-flex justify-content-between align-items-center">
        <span>Student success</span>
        <SignalBadges signals={signals} />
      </Card.Header>
      <Card.Body>
        <div className="d-flex flex-wrap">
          <Metric label="Attendance" value={pct(a.rate)}
            sub={`${a.present} present · ${a.late} late · ${a.absent} absent`} />
          <Metric label="Attendance trend"
            value={a.recentRate == null ? '—' : pct(a.recentRate)}
            sub={a.priorRate == null ? 'not enough data' : `was ${pct(a.priorRate)}`} />
          <Metric label="Quiz average" value={pct(ac.quizAvg)} />
          <Metric label="Missing" value={num(ac.missing)} sub={`${ac.submitted} submitted`} />
        </div>
        {cats.length > 0 && (
          <table className="table table-sm mt-2 mb-0" style={{ maxWidth: 480 }}>
            <thead>
              <tr className="text-secondary fs-2">
                <th>Category</th><th>Avg</th><th>Graded</th><th>Missing</th>
              </tr>
            </thead>
            <tbody>
              {cats.map(([cat, c]) => (
                <tr key={cat}>
                  <td>{cat}</td>
                  <td>{c.avg == null ? '—' : `${c.avg}%`}</td>
                  <td>{c.graded}/{c.count}</td>
                  <td>{c.missing || 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card.Body>
    </Card>
  )
}

export default function StudentDetail() {
  const { sid } = useParams()
  const nav = useNavigate()
  const [s, setS] = useState(null)
  const [prof, setProf] = useState(null)
  const [busy, setBusy] = useState(false)
  const [enrolling, setEnrolling] = useState(false)
  const [toast, setToast] = useToast()

  const load = () => api.student(sid).then(setS)
  useEffect(() => { load() }, [sid])
  useEffect(() => {
    setProf(null)
    api.studentProfileFull(sid).then(setProf).catch(() => setProf(null))
  }, [sid])

  async function onEnrolled(r) {
    setToast(r.message || `Enrolled ✓ (${r.angles?.length || 0} angles)`)
    setEnrolling(false)
    await load()
  }
  async function remove() {
    setBusy(true)
    try { await api.unenroll(sid); setToast('Profile removed'); await load() }
    finally { setBusy(false) }
  }

  if (!s) return <div className="text-secondary p-4">Loading…</div>

  return (
    <>
      <PageHeader heading={s.name} subHeading={`${s.sid} · ${s.cls}`} back={() => nav('/students')} />

      {/* banner */}
      <div className="profile-banner mb-4">
        <div className="d-flex flex-wrap gap-4">
          <span className="avatar lg square">
            {s.thumb ? <img src={s.thumb} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : 'no photo'}
          </span>
          <div className="flex-grow-1">
            <h4 className="fw-bold text-primary mb-3">{s.name}</h4>
            <Field k="Face profile" v={s.enrolled
              ? <Badge bg="success">Enrolled</Badge>
              : <Badge bg="warning" text="dark">Not enrolled</Badge>} />
            <Field k="Student ID" v={s.sid} />
            <Field k="Class" v={s.cls} />
            <Field k="Enrollment status" v={s.expected ? 'Profile expected' : 'New student'} />
            <Field k="Embedding model" v={s.embVer || '—'} />
            <Field k="Detection quality" v={s.quality != null ? s.quality : '—'} />
            <Field k="Enrolled angles" v={s.angles != null ? s.angles : '—'} />
          </div>
        </div>
      </div>

      {/* student-success profile */}
      <StudentSuccess prof={prof} />

      {/* teacher-facing improvement suggestions */}
      <ImprovementPlan sid={s.sid} name={s.name} />

      {/* face profile card — guided, motion-based enrollment */}
      <Card>
        <Card.Header>Face profile</Card.Header>
        <Card.Body>
          <p className="text-secondary">
            Look at the camera and slowly turn your head left, center, and right to complete enrollment.
          </p>

          <div className="d-flex flex-wrap gap-2">
            <Button variant="primary" icon="videocam" disabled={busy} onClick={() => setEnrolling(true)}>
              {s.enrolled ? 'Re-enroll (guided)' : 'Start guided enrollment'}
            </Button>
            {s.enrolled && <Button variant="danger" icon="delete" disabled={busy} onClick={remove}>Remove profile</Button>}
          </div>
        </Card.Body>
      </Card>

      {/* Guided enrollment runs in a focused dark modal (FaceID-style). The panel
          renders its own top bar + close, so no default Modal.Header. backdrop
          "static" avoids closing mid-capture by a stray outside click. */}
      <Modal show={enrolling} onHide={() => setEnrolling(false)} centered backdrop="static"
        contentClassName="enroll-modal">
        <Modal.Body className="p-0">
          {enrolling && (
            <GuidedEnroll sid={sid} name={s.name} autoStart
              onDone={onEnrolled} onCancel={() => setEnrolling(false)} />
          )}
        </Modal.Body>
      </Modal>
      {toast}
    </>
  )
}
