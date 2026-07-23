import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Badge } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'
import { useCamera } from '../hooks/useCamera'
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
function ImprovementPlan({ sid }) {
  const [plan, setPlan] = useState(null)
  const [busy, setBusy] = useState(false)

  async function generate() {
    setBusy(true)
    try { setPlan(await api.studentPlan(sid)) } catch { setPlan(null) } finally { setBusy(false) }
  }

  return (
    <Card className="mb-4">
      <Card.Header className="d-flex justify-content-between align-items-center">
        <span>Improvement suggestions</span>
        <Button variant="secondary" icon="lightbulb" disabled={busy} onClick={generate}>
          {busy ? 'Generating…' : plan ? 'Refresh' : 'Generate'}
        </Button>
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
  const [camOn, setCamOn] = useState(false)
  const [toast, setToast] = useToast()
  const fileRef = useRef(null)
  const cam = useCamera()

  const load = () => api.student(sid).then(setS)
  useEffect(() => { load() }, [sid])
  useEffect(() => {
    setProf(null)
    api.studentProfileFull(sid).then(setProf).catch(() => setProf(null))
  }, [sid])

  async function enrollWith(blobOrFile, name) {
    setBusy(true)
    try {
      const r = await api.enroll(sid, blobOrFile, name)
      setToast(r.ok ? `Enrolled ✓ (quality ${r.quality})` : (r.message || 'No face found'))
      await load()
    } catch (e) { setToast('Enroll failed: ' + e.message) } finally { setBusy(false) }
  }
  async function captureFromCam() {
    if (!cam.active) { const ok = await cam.start(); setCamOn(ok); if (!ok) { setToast(cam.error); return } }
    await new Promise((r) => setTimeout(r, 350))
    const blob = await cam.grabBlob()
    if (blob) await enrollWith(blob, 'capture.jpg')
  }
  async function remove() { await api.unenroll(sid); setToast('Profile removed'); await load() }

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
          </div>
        </div>
      </div>

      {/* student-success profile */}
      <StudentSuccess prof={prof} />

      {/* teacher-facing improvement suggestions */}
      <ImprovementPlan sid={s.sid} />

      {/* face profile card */}
      <Card>
        <Card.Header>Face profile</Card.Header>
        <Card.Body>
          <p className="text-secondary">
            Enroll this student's face by uploading a clear, front-facing photo or capturing from the
            camera. The backend detects the face and stores a 512-d embedding.
          </p>
          {camOn && (
            <div className="mb-3" style={{ maxWidth: 360 }}>
              <div className="stage"><video ref={cam.videoRef} autoPlay muted playsInline /></div>
            </div>
          )}
          <div className="d-flex flex-wrap gap-2">
            <Button variant="primary" icon="upload" disabled={busy} onClick={() => fileRef.current.click()}>Upload photo</Button>
            <Button variant="secondary" icon="camera" disabled={busy} onClick={captureFromCam}>
              {cam.active ? 'Capture frame' : 'Use camera'}
            </Button>
            {cam.active && <Button variant="secondary" icon="stop" onClick={() => { cam.stop(); setCamOn(false) }}>Stop camera</Button>}
            {s.enrolled && <Button variant="danger" icon="delete" disabled={busy} onClick={remove}>Remove profile</Button>}
            <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }}
              onChange={(e) => { const f = e.target.files[0]; if (f) enrollWith(f, f.name); e.target.value = '' }} />
          </div>
          {!cam.secure && <p className="text-secondary fs-2 mt-2">Camera needs HTTPS/localhost — upload works anywhere.</p>}
        </Card.Body>
      </Card>
      {toast}
    </>
  )
}
