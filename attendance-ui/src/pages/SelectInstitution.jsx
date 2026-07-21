import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Form, ListGroup } from 'react-bootstrap'
import AuthLayout from '../components/AuthLayout'
import { api } from '../api'
import { setInstitution } from '../auth'

export default function SelectInstitution() {
  const [q, setQ] = useState('')
  const [list, setList] = useState([])
  const [err, setErr] = useState('')
  const nav = useNavigate()

  useEffect(() => {
    if (q.trim().length < 1) { setList([]); return }
    const t = setTimeout(() => {
      api.institutes(q).then(setList).catch((e) => setErr(e.message))
    }, 250)
    return () => clearTimeout(t)
  }, [q])

  function choose(inst) {
    setInstitution(inst)
    nav('/login')
  }

  return (
    <AuthLayout>
      <div className="auth-logo"><span className="badge-lg" /></div>
      <h4 className="auth-title">Select your institution</h4>
      <hr className="auth-hr" />
      <Form.Label className="fw-semibold text-primary">Institution</Form.Label>
      <Form.Control placeholder="Type your institution name" value={q}
        onChange={(e) => setQ(e.target.value)} autoFocus />
      {list.length > 0 && (
        <ListGroup className="mt-2">
          {list.map((i) => (
            <ListGroup.Item action key={i.InId} onClick={() => choose(i)}>
              {i.InNa}{i.City ? ` · ${i.City}` : ''}
            </ListGroup.Item>
          ))}
        </ListGroup>
      )}
      {!list.length && (
        <div className="auth-hint">
          Please type your Institution name to search. You can type the first 3 letters of your
          Institution to see the list.
        </div>
      )}
      {err && <div className="text-danger mt-2 fs-2">{err}</div>}
    </AuthLayout>
  )
}
