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
  enroll: (sid, fileOrBlob, name = 'photo.jpg') => {
    const fd = new FormData()
    fd.append('file', fileOrBlob, name)
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
  summary: (date) => j('/api/attendance/summary' + (date ? `?date=${date}` : '')),
  roster: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v))
    ).toString()
    return j('/api/attendance/roster' + (q ? `?${q}` : ''))
  },
}

export const todayStr = () => new Date().toLocaleDateString('en-CA')
