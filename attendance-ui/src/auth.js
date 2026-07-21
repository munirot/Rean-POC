// Simple client session for the demo (stored in localStorage on the user's machine).
const KEY = 'rean.session'

export function getSession() {
  try { return JSON.parse(localStorage.getItem(KEY)) } catch { return null }
}
export function setSession(s) { localStorage.setItem(KEY, JSON.stringify(s)) }
export function clearSession() { localStorage.removeItem(KEY) }
export function isLoggedIn() { return !!getSession()?.token }

export function getInstitution() {
  try { return JSON.parse(localStorage.getItem('rean.institution')) } catch { return null }
}
export function setInstitution(inst) { localStorage.setItem('rean.institution', JSON.stringify(inst)) }
