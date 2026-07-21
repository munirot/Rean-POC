import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Form, InputGroup } from 'react-bootstrap'
import AuthLayout from '../components/AuthLayout'
import Button from '../components/Button'
import MatIcon from '../components/MatIcon'
import { api } from '../api'
import { getInstitution, setSession } from '../auth'

export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()
  const inst = getInstitution()

  async function submit(e) {
    e.preventDefault()
    setErr(''); setBusy(true)
    try {
      const u = await api.login(username.trim(), password)
      setSession(u)
      nav('/')
    } catch (e2) {
      setErr(e2.message.includes('401') ? 'Invalid username or password' : e2.message)
    } finally { setBusy(false) }
  }

  return (
    <AuthLayout>
      <form onSubmit={submit}>
        <div className="auth-logo"><span className="badge-lg" /></div>
        <div className="text-center fw-semibold text-primary mt-2">
          {inst?.InNa || 'Your Institution'}
        </div>
        <div className="text-center fs-2 mb-2">
          <Link to="/select">Not from {inst?.InNa || 'this institution'}?</Link>
        </div>
        <h5 className="auth-title">Login to Rean</h5>
        <hr className="auth-hr" />

        <Form.Label className="fw-semibold text-primary">User name</Form.Label>
        <Form.Control value={username} onChange={(e) => setUsername(e.target.value)}
          placeholder="Email or Login ID" autoFocus className="mb-3" />

        <Form.Label className="fw-semibold text-primary">Password</Form.Label>
        <InputGroup>
          <Form.Control type={show ? 'text' : 'password'} value={password}
            onChange={(e) => setPassword(e.target.value)} placeholder="Password" />
          <InputGroup.Text role="button" onClick={() => setShow((s) => !s)}>
            <MatIcon name={show ? 'visibility_off' : 'visibility'} />
          </InputGroup.Text>
        </InputGroup>

        {err && <div className="text-danger mt-2 fs-2">{err}</div>}

        <Button type="submit" className="btn-royal mt-4" disabled={busy}>
          {busy ? 'Signing in…' : 'Login'}
        </Button>

        <div className="auth-hint mt-3">
          Demo logins (password <b>Rean@123</b>): a student <code>LG-2301</code>, a teacher
          <code> LG-S001</code>, or admin <code>LG-ADMIN</code>. You can also use the email.
        </div>
        <div className="auth-foot">© 2026 Rean · University Technologies. All rights reserved.</div>
      </form>
    </AuthLayout>
  )
}
