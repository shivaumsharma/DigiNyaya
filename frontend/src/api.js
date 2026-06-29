// Thin API client for the DigiNyaya backend. Uses the Vite proxy (/api -> :8077).

const BASE = '/api'
const SESSION_KEY = 'diginyaya_session'

function authToken() {
  try {
    const raw = localStorage.getItem(SESSION_KEY)
    return raw ? JSON.parse(raw)?.token : null
  } catch {
    return null
  }
}

function authHeaders() {
  const token = authToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function jsonFetch(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    ...options,
  })
  if (!res.ok) {
    let msg = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body.detail) msg = body.detail
    } catch (_) {}
    const err = new Error(msg)
    err.status = res.status
    throw err
  }
  return res.json()
}

export const api = {
  login: (name, aadhaar_last4) =>
    jsonFetch('/login', { method: 'POST', body: JSON.stringify({ name, aadhaar_last4 }) }),
  aiStatus: () => jsonFetch('/ai-status'),
  disputeTypes: () => jsonFetch('/dispute-types'),
  sampleClaim: () => jsonFetch('/sample-claim'),
  precedents: () => jsonFetch('/precedents'),
  createCase: (claim) => jsonFetch('/cases', { method: 'POST', body: JSON.stringify(claim) }),
  getCase: (id) => jsonFetch(`/cases/${id}`),
  respond: (id, submission) =>
    jsonFetch(`/cases/${id}/respond`, { method: 'POST', body: JSON.stringify(submission) }),
  skipResponse: (id) => jsonFetch(`/cases/${id}/skip-response`, { method: 'POST' }),
  runPipeline: (id) => jsonFetch(`/cases/${id}/run`, { method: 'POST' }),
  mediationDecision: (id, accept) =>
    jsonFetch(`/cases/${id}/mediation`, { method: 'POST', body: JSON.stringify({ accept }) }),
}

// Stream Server-Sent Events. Calls onEvent for every parsed event object,
// onDone when the stream closes, onError on failure. Sends the auth header so
// ownership is enforced. Returns an abort function.
export function streamSSE(path, { onEvent, onDone, onError }) {
  const controller = new AbortController()
  fetch(BASE + path, { signal: controller.signal, headers: { ...authHeaders() } })
    .then(async (res) => {
      if (!res.ok || !res.body) throw new Error(`Stream failed (${res.status})`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const frames = buffer.split('\n\n')
        buffer = frames.pop() || ''
        for (const frame of frames) {
          const line = frame.split('\n').find((l) => l.startsWith('data:'))
          if (!line) continue
          const raw = line.slice(5).trim()
          if (!raw || raw === '{}') continue
          try {
            const data = JSON.parse(raw)
            if (data && data.type) onEvent && onEvent(data)
          } catch (_) {}
        }
      }
      onDone && onDone()
    })
    .catch((err) => {
      if (err.name !== 'AbortError') onError && onError(err)
    })
  return () => controller.abort()
}
