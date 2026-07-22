import { useState, useRef, useEffect } from 'react'
import { Card, Badge } from 'react-bootstrap'
import PageHeader from '../components/PageHeader'
import Button from '../components/Button'
import { api } from '../api'

const SUGGESTIONS = [
  'Who is at risk in Diplomacy & Negotiation?',
  'How is Dara Sok doing?',
  'How many students were absent today?',
  'Which students are missing assignments?',
]

// Collapsible "show the data" panel so staff can verify the numbers.
function Evidence({ data }) {
  const [open, setOpen] = useState(false)
  if (!data) return null
  return (
    <div className="mt-2">
      <span className="text-secondary fs-2" style={{ cursor: 'pointer' }}
        onClick={() => setOpen((v) => !v)}>
        {open ? '▾ hide data' : '▸ show data'}
      </span>
      {open && (
        <pre className="fs-2 mt-1 p-2" style={{
          background: 'var(--bs-tertiary-bg, #f5f5f5)', borderRadius: 6,
          maxHeight: 260, overflow: 'auto', whiteSpace: 'pre-wrap',
        }}>{JSON.stringify(data, null, 2)}</pre>
      )}
    </div>
  )
}

function Bubble({ m }) {
  const mine = m.role === 'user'
  return (
    <div className={`d-flex mb-3 ${mine ? 'justify-content-end' : ''}`}>
      <div style={{ maxWidth: '80%' }}>
        <Card body className={mine ? 'bg-primary text-white' : ''}
          style={mine ? { background: 'var(--primary, #0d6efd)' } : undefined}>
          <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
          {m.error && <Badge bg="warning" text="dark" className="mt-2">{m.error}</Badge>}
          {!mine && <Evidence data={m.data} />}
        </Card>
      </div>
    </div>
  )
}

export default function Chat() {
  const [msgs, setMsgs] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs, busy])

  async function send(text) {
    const q = (text ?? input).trim()
    if (!q || busy) return
    setInput('')
    setMsgs((m) => [...m, { role: 'user', content: q }])
    setBusy(true)
    try {
      const r = await api.chat(q)
      setMsgs((m) => [...m, { role: 'assistant', content: r.answer, data: r.data, error: r.error }])
    } catch (e) {
      setMsgs((m) => [...m, { role: 'assistant', content: `Request failed: ${e.message}`, error: 'error' }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader heading="Ask about students"
        subHeading="Grounded in attendance + assignments — answers cite real records" />

      {msgs.length === 0 && (
        <div className="mb-3 d-flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <Button key={s} variant="secondary" onClick={() => send(s)}>{s}</Button>
          ))}
        </div>
      )}

      <div className="mb-3">
        {msgs.map((m, i) => <Bubble key={i} m={m} />)}
        {busy && <div className="text-secondary fs-2 mb-3">Thinking…</div>}
        <div ref={endRef} />
      </div>

      <Card body>
        <div className="d-flex gap-2">
          <input className="form-control" placeholder="Ask about a student or class…"
            value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') send() }} disabled={busy} />
          <Button variant="primary" icon="send" disabled={busy || !input.trim()} onClick={() => send()}>
            Send
          </Button>
        </div>
        <div className="text-secondary fs-2 mt-2">
          Answers are limited to students you have access to. Suggestions are for you to review — not auto-applied.
        </div>
      </Card>
    </>
  )
}
