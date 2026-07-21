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

export default function StudentDetail() {
  const { sid } = useParams()
  const nav = useNavigate()
  const [s, setS] = useState(null)
  const [busy, setBusy] = useState(false)
  const [camOn, setCamOn] = useState(false)
  const [toast, setToast] = useToast()
  const fileRef = useRef(null)
  const cam = useCamera()

  const load = () => api.student(sid).then(setS)
  useEffect(() => { load() }, [sid])

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
