// Thin client for the Python face-service. In dev, Vite proxies /api -> :8000.
import { getSession } from './auth'

const BASE = import.meta.env.VITE_API_BASE || ''

async function j(url, opts = {}) {
  // Attach the session token so scoped endpoints can authorize the caller.
  const token = getSession()?.token
  const headers = { ...(opts.headers || {}) }
  if (token) headers.Authorization = `Bearer ${token}`
  const r = await fetch(BASE + url, { ...opts, headers })
  if (!r.ok) {
    let msg = `HTTP ${r.status}`
    try { const e = await r.json(); msg = e.detail || e.message || msg } catch {}
    throw new Error(msg)
  }
  return r.status === 204 ? null : r.json()
}

export const api = {
  health: () => j('/api/health'),

  // auth / institutes
  institutes: (q) => j('/api/institutes' + (q ? `?q=${encodeURIComponent(q)}` : '')),
  login: (username, password) => j('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }),

  // students
  students: () => j('/api/students'),
  student: async (sid) => (await j('/api/students')).find((s) => s.sid === sid) || null,
  studentProfile: (sid) => j(`/api/students/${sid}/profile`),
  studentStats: (sid) => j(`/api/students/${sid}/stats`),
  studentProfileFull: (sid) => j(`/api/students/${sid}/profile/full`),
  studentPlan: (sid) => j(`/api/students/${sid}/plan`),
  cohort: (cls) => j(`/api/analytics/cohort?cls=${encodeURIComponent(cls)}`),
  chat: (message, conversationId, sid) => j('/api/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversationId, sid }),
  }),
  chatConversations: () => j('/api/chat/conversations'),
  chatHistory: (conversationId) => j('/api/chat/history' +
    (conversationId ? `?conversationId=${encodeURIComponent(conversationId)}` : '')),
  clearChat: (conversationId) => j('/api/chat/history' +
    (conversationId ? `?conversationId=${encodeURIComponent(conversationId)}` : ''),
    { method: 'DELETE' }),
  // Guided enrollment: analyze one frame's head pose + liveness for live feedback.
  analyzePose: (blob) => {
    const fd = new FormData()
    fd.append('file', blob, 'frame.jpg')
    return j('/api/face/pose', { method: 'POST', body: fd })
  },
  // Multi-angle enrollment: submit the captured frames + their pose labels.
  // `captures` = [{ blob, pose }] in order (e.g. center, left, right).
  enroll: (sid, captures) => {
    const fd = new FormData()
    for (const c of captures) {
      fd.append('files', c.blob, `${c.pose}.jpg`)
      fd.append('poses', c.pose)
    }
    return j(`/api/students/${sid}/enroll`, { method: 'POST', body: fd })
  },
  unenroll: (sid) => j(`/api/students/${sid}/enroll`, { method: 'DELETE' }),
  resetProfiles: () => j('/api/students/reset', { method: 'POST' }),

  // recognition
  recognize: (blob, threshold) => {
    const fd = new FormData()
    fd.append('file', blob, 'frame.jpg')
    if (threshold != null) fd.append('threshold', threshold)
    return j('/api/recognize', { method: 'POST', body: fd })
  },

  // attendance
  mark: (body) => j('/api/attendance', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  attendance: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v))
    ).toString()
    return j('/api/attendance' + (q ? `?${q}` : ''))
  },
  deleteAttendance: (id) => j(`/api/attendance/${id}`, { method: 'DELETE' }),
  setAttendance: (body) => j('/api/attendance', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  summary: (date) => j('/api/attendance/summary' + (date ? `?date=${date}` : '')),
  roster: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v))
    ).toString()
    return j('/api/attendance/roster' + (q ? `?${q}` : ''))
  },

  // attendance policy / capture periods
  attendancePolicy: (session) =>
    j('/api/attendance/policy' + (session ? `?session=${encodeURIComponent(session)}` : '')),
  adminPeriods: () => j('/api/admin/attendance-periods'),
  saveAdminPeriods: (periods) => j('/api/admin/attendance-periods', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ periods }),
  }),

  // attendance disputes ("I was present")
  raiseDispute: (body) => j('/api/attendance/disputes', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  disputes: (state) => j('/api/attendance/disputes' + (state ? `?state=${state}` : '')),
  resolveDispute: (id, body) => j(`/api/attendance/disputes/${id}/resolve`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
}
