import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { api } from '../api'
import MatIcon from './MatIcon'
import { getSession, getInstitution, clearSession } from '../auth'

// Role-aware menu (staff/admin see the full set; students a reduced set).
const ALL = [
  { to: '/', label: 'Dashboard', end: true, roles: ['admin', 'staff', 'student'] },
  { to: '/students', label: 'Students', roles: ['admin', 'staff'] },
  { to: '/attendance', label: 'Take Attendance', roles: ['admin', 'staff', 'student'] },
  { to: '/records', label: 'Attendance Records', roles: ['admin', 'staff', 'student'] },
  { to: '/disputes', label: 'Disputes', roles: ['admin', 'staff'] },
  { to: '/leave', label: 'Leave requests', roles: ['admin', 'staff'] },
  { to: '/insights', label: 'Insights', roles: ['admin', 'staff'] },
  { to: '/chat', label: 'Ask AI', roles: ['admin', 'staff', 'student'] },
  // Settings is reached from the gear in the right-hand rail, not this list.
]

export default function Layout() {
  const [health, setHealth] = useState(null)
  const [err, setErr] = useState(false)
  const loc = useLocation()
  const nav = useNavigate()
  const session = getSession()
  const inst = getInstitution()
  const role = session?.type || 'student'
  const menu = ALL.filter((m) => m.roles.includes(role))

  useEffect(() => {
    let alive = true
    const tick = () => api.health()
      .then((h) => alive && (setHealth(h), setErr(false)))
      .catch(() => alive && setErr(true))
    tick(); const id = setInterval(tick, 8000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const isActive = (m) => (m.end ? loc.pathname === '/' : loc.pathname.startsWith(m.to))
  const logout = () => { clearSession(); nav('/select') }

  return (
    <div className="camu-main_container">
      <nav className="left-nav_container">
        <div className="inst-logo_container">
          <span className="logo-badge" />
          <div className="inst-logo_view">
            <div>Rean<small>{inst?.InNa || 'Face Attendance'}</small></div>
          </div>
        </div>
        <div className="nav-search"><input placeholder="Search" disabled /></div>
        <ul className="side-nav_container">
          {menu.map((m) => (
            <li key={m.to} className={isActive(m) ? 'menu_active' : ''}>
              <div className="sub-list_option" onClick={() => nav(m.to)}>{m.label}</div>
            </li>
          ))}
        </ul>
        <div className="side-foot">
          <div className="fw-semibold text-primary">{session?.name}</div>
          <div className="text-capitalize mb-1">{role}{session?.sid ? ` · ${session.sid}` : ''}</div>
          {err && <div className="down">● backend offline</div>}
          {!err && health && (
            <div className={health.mongo === 'up' ? 'up' : 'down'}>
              ● {health.mongo === 'up' ? `${health.enrolled}/${health.students} enrolled` : 'db down'}
            </div>
          )}
        </div>
      </nav>

      <main className="main-container_block"><Outlet /></main>

      <div className="right-navbar_container">
        <div className="nav-more_option">
          <div className="right-nav_list">
            <div className="side-nav_list" title="Profile"><MatIcon name="account_circle" /></div>
            {/* Institution configuration — only an admin has anything to open here. */}
            {role === 'admin' && (
              <div className={`side-nav_list${loc.pathname.startsWith('/settings') ? ' menu_active' : ''}`}
                title="Settings" role="button" onClick={() => nav('/settings')}>
                <MatIcon name="settings" />
              </div>
            )}
            <div className="side-nav_list" title="Logout" role="button" onClick={logout}>
              <MatIcon name="logout" /></div>
          </div>
        </div>
      </div>

      <footer className="app-footer">
        <span>Copyright © 2026 Rean. All rights reserved.</span>
        <span className="prepared">Prepared by <span className="logo-badge" /> Rean</span>
      </footer>
    </div>
  )
}

export function useToast() {
  const [msg, setMsg] = useState('')
  useEffect(() => {
    if (!msg) return
    const id = setTimeout(() => setMsg(''), 2600)
    return () => clearTimeout(id)
  }, [msg])
  const node = msg ? <div className="toast-msg">{msg}</div> : null
  return [node, setMsg]
}
