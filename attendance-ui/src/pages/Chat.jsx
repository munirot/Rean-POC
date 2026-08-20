import { useState, useRef, useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { Row, Col, Card, Badge } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'
import { fmtDate } from '../utils/time'
import { getSession } from '../auth'

// Staff/admin ask about their students; a student asks about their own record.
// The backend scopes every query to the caller, so the student view is safe.
const SUGGESTIONS = [
  'How many students were absent today?',
  'Who is at risk in Diplomacy & Negotiation?',
  'How is Dara Sok doing?',
  'Which students are missing assignments?',
]

const MY_SUGGESTIONS = [
  'How is my attendance this month?',
  'How many classes have I missed?',
  'Am I missing any assignments?',
  'How can I improve?',
]

// Shown instead when arriving from a student page, so the first click is useful.
const studentSuggestions = (name) => [
  `How can I help ${name} improve?`,
  `How is ${name} doing?`,
  `Is ${name} missing any assignments?`,
]

const newId = () =>
  (window.crypto?.randomUUID?.() || `c${Date.now()}${Math.random().toString(16).slice(2)}`)

function Bubble({ m }) {
  const mine = m.role === 'user'
  return (
    <div className={`d-flex mb-3 ${mine ? 'justify-content-end' : ''}`}>
      <div style={{ maxWidth: '80%' }}>
        <Card body className={mine ? 'text-white' : ''}
          style={mine ? { background: 'var(--primary, #0d6efd)' } : undefined}>
          <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
          {m.error && <Badge bg="warning" text="dark" className="mt-2">{m.error}</Badge>}
        </Card>
      </div>
    </div>
  )
}

export default function Chat() {
  const isStudent = getSession()?.type === 'student'
  const [convos, setConvos] = useState([])
  const [convId, setConvId] = useState(newId())
  const [msgs, setMsgs] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)

  // Student handed over from their detail page ("Discuss in chat"). Sent as
  // ChatRequest.sid so the server can resolve "he/she/they" to this student.
  const handoff = useLocation().state
  const [ctx, setCtx] = useState(handoff?.sid ? handoff : null)

  const loadConvos = () => api.chatConversations().then(setConvos).catch(() => {})

  // On mount: load the conversation list and open the most recent (if any).
  // Arriving with a student in tow starts a fresh thread about them instead.
  useEffect(() => {
    api.chatConversations().then((list) => {
      setConvos(list)
      if (!handoff?.sid && list.length) selectConv(list[0].conversationId)
    }).catch(() => {})
  }, [])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs, busy])

  function newChat() {
    setConvId(newId())
    setMsgs([])
    setInput('')
    setCtx(null)
  }

  async function selectConv(id) {
    setConvId(id)
    setCtx(null)      // an older thread has its own subject; drop the handoff
    try {
      const h = await api.chatHistory(id)
      setMsgs(h.map((m) => ({ role: m.role, content: m.content })))
    } catch { setMsgs([]) }
  }

  async function clearOne() {
    try { await api.clearChat(convId) } catch { /* ignore */ }
    await loadConvos()
    newChat()
  }

  async function send(text) {
    const q = (text ?? input).trim()
    if (!q || busy) return
    const isNew = !convos.some((c) => c.conversationId === convId)
    setInput('')
    setMsgs((m) => [...m, { role: 'user', content: q }])
    setBusy(true)
    try {
      const r = await api.chat(q, convId, ctx?.sid)
      if (r.conversationId) setConvId(r.conversationId)
      setMsgs((m) => [...m, { role: 'assistant', content: r.answer, error: r.error }])
      if (isNew) loadConvos()   // surface the new thread in the sidebar
    } catch (e) {
      setMsgs((m) => [...m, { role: 'assistant', content: `Request failed: ${e.message}`, error: 'error' }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        heading={isStudent ? 'Ask about my record' : 'Ask about students'}
        subHeading={isStudent
          ? 'Grounded in your own attendance + assignments — answers cite your real records'
          : 'Grounded in attendance + assignments — answers cite real records'}>
        <Button variant="primary" icon="add" onClick={newChat}>New chat</Button>
      </PageHeader>

      <Row className="g-3">
        {/* conversation sidebar */}
        <Col md={3}>
          <Card>
            <Card.Header className="fw-bold text-primary fs-3">Conversations</Card.Header>
            <Card.Body className="p-2" style={{ maxHeight: 460, overflow: 'auto' }}>
              {convos.length === 0 && <div className="text-secondary fs-2 p-2">No saved chats yet</div>}
              {convos.map((c) => (
                <div key={c.conversationId}
                  onClick={() => selectConv(c.conversationId)} 
                  className={`p-2 mb-1 border-b-1 ${c.conversationId === convId ? 'chat_active' : ''}`}
                  style={{ cursor: 'pointer', borderRadius: 6 }}>
                  <div className="text-primary fs-3 text-truncate">{c.title}</div>
                  <div className="text-secondary fs-2">
                    {c.count} msg{c.count === 1 ? '' : 's'}
                    {c.updatedAt ? ` · ${fmtDate(c.updatedAt)}` : ''}
                  </div>
                </div>
              ))}
            </Card.Body>
          </Card>
        </Col>

        {/* chat thread */}
        <Col md={9} className="d-flex flex-column"
          style={{ height: 'calc(100vh - 180px)' }}>
          {/* scrollable message area */}
          <div className="flex-grow-1 overflow-auto pe-1">
            {ctx && (
              <div className="d-flex align-items-center gap-2 mb-3">
                <Badge bg="primary">About {ctx.name || ctx.sid}</Badge>
                <span className="text-secondary fs-2" role="button"
                  onClick={() => setCtx(null)}>clear</span>
              </div>
            )}

            {msgs.length === 0 && (
              <div className="mb-3 d-flex flex-wrap gap-2">
                {(ctx?.name ? studentSuggestions(ctx.name) : isStudent ? MY_SUGGESTIONS : SUGGESTIONS).map((s) => (
                  <Button key={s} variant="secondary" onClick={() => send(s)}>{s}</Button>
                ))}
              </div>
            )}

            {msgs.map((m, i) => <Bubble key={i} m={m} />)}
            {busy && <div className="text-secondary fs-2 mb-3">Thinking…</div>}
            <div ref={endRef} />
          </div>

          <Card body className="shadow-sm mt-3 flex-shrink-0">
            <div className="d-flex gap-2">
              <input className="form-control"
                placeholder={isStudent ? 'Ask about your attendance or assignments…' : 'Ask about a student or class…'}
                value={input} onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') send() }} disabled={busy} />
              <Button variant="primary" icon="send" disabled={busy || !input.trim()} onClick={() => send()}>
                Send
              </Button>
              {msgs.length > 0 && (
                <Button variant="secondary" icon="delete" onClick={clearOne}>Clear</Button>
              )}
            </div>
            <div className="text-secondary fs-2 mt-2">
              {isStudent
                ? 'Answers are limited to your own record. This is supportive guidance — talk to your teacher any time.'
                : 'Answers are limited to students you have access to. Suggestions are for you to review — not auto-applied.'}
            </div>
          </Card>
        </Col>
      </Row>
    </>
  )
}
