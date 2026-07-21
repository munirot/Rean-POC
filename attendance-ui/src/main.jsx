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
import SelectInstitution from './pages/SelectInstitution'
import Login from './pages/Login'
import { isLoggedIn } from './auth'
import './styles/theme.scss'

function RequireAuth({ children }) {
  return isLoggedIn() ? children : <Navigate to="/select" replace />
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/select" element={<SelectInstitution />} />
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth><Layout /></RequireAuth>}>
          <Route index element={<Dashboard />} />
          <Route path="students" element={<Students />} />
          <Route path="students/:sid" element={<StudentDetail />} />
          <Route path="attendance" element={<TakeAttendance />} />
          <Route path="records" element={<Records />} />
          <Route path="insights" element={<Insights />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
)
