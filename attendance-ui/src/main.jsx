import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Students from './pages/Students'
import StudentDetail from './pages/StudentDetail'
import TakeAttendance from './pages/TakeAttendance'
import Records from './pages/Records'
import Insights from './pages/Insights'
import Chat from './pages/Chat'
import SelectInstitution from './pages/SelectInstitution'
import Login from './pages/Login'
import { isLoggedIn, getSession } from './auth'
import './styles/theme.scss'

function RequireAuth({ children }) {
  return isLoggedIn() ? children : <Navigate to="/select" replace />
}

// Route-level role guard. The nav already hides staff-only items from students
// (see Layout), but that doesn't stop a direct URL. Students who deep-link to a
// staff page are sent to their own landing (index) rather than a staff view they
// can't meaningfully use. `roles` lists who may enter.
function RequireRole({ roles, children }) {
  const role = getSession()?.type || 'student'
  return roles.includes(role) ? children : <Navigate to="/" replace />
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/select" element={<SelectInstitution />} />
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth><Layout /></RequireAuth>}>
          {/* Dashboard self-branches: students see StudentDashboard, staff the aggregate. */}
          <Route index element={<Dashboard />} />
          {/* Staff/admin only — students hitting these URLs are redirected to their landing. */}
          <Route path="students" element={<RequireRole roles={['admin', 'staff']}><Students /></RequireRole>} />
          <Route path="students/:sid" element={<RequireRole roles={['admin', 'staff']}><StudentDetail /></RequireRole>} />
          <Route path="insights" element={<RequireRole roles={['admin', 'staff']}><Insights /></RequireRole>} />
          <Route path="chat" element={<RequireRole roles={['admin', 'staff']}><Chat /></RequireRole>} />
          {/* Open to all authenticated roles (backend scopes the data to the caller). */}
          <Route path="attendance" element={<TakeAttendance />} />
          <Route path="records" element={<Records />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
)
