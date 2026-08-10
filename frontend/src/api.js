// Thin API client for the DigiNyaya backend. In dev, relative paths go
// through the Vite proxy (/api -> :8000). In production the frontend and
// backend are separate deploys on different domains, so VITE_API_BASE points
// straight at the backend's origin (see .env.production / Render env vars).
import { getAccessToken } from './auth/tokenStore.js'

const BASE = `${import.meta.env.VITE_API_BASE || ''}/api`

function authHeaders() {
  const token = getAccessToken()
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
    } catch {
      // Response body wasn't JSON -- fall back to the generic message above.
    }
    const err = new Error(msg)
    err.status = res.status
    throw err
  }
  return res.json()
}

async function uploadFetch(path, files) {
  const body = new FormData()
  for (const file of files) body.append('files', file)
  const res = await fetch(BASE + path, { method: 'POST', headers: authHeaders(), body })
  if (!res.ok) {
    let msg = `Request failed (${res.status})`
    try {
      const data = await res.json()
      if (data.detail) msg = data.detail
    } catch {
      // Response body wasn't JSON -- fall back to the generic message above.
    }
    const err = new Error(msg)
    err.status = res.status
    throw err
  }
  return res.json()
}

export const api = {
  aiStatus: () => jsonFetch('/ai-status'),
  languages: () => jsonFetch('/languages'),
  disputeTypes: () => jsonFetch('/dispute-types'),
  sampleClaim: () => jsonFetch('/sample-claim'),
  precedents: () => jsonFetch('/precedents'),
  myCases: () => jsonFetch('/cases'),
  createCase: (claim) => jsonFetch('/cases', { method: 'POST', body: JSON.stringify(claim) }),
  getCase: (id, lang) => jsonFetch(`/cases/${id}${lang ? `?lang=${encodeURIComponent(lang)}` : ''}`),
  submitCase: (id) => jsonFetch(`/cases/${id}/submit`, { method: 'POST' }),
  uploadDocuments: (id, files) => uploadFetch(`/cases/${id}/documents`, files),
  listDocuments: (id) => jsonFetch(`/cases/${id}/documents`),
  preliminaryReview: (id) => jsonFetch(`/cases/${id}/preliminary-review`, { method: 'POST' }),
  respond: (id, submission) =>
    jsonFetch(`/cases/${id}/respond`, { method: 'POST', body: JSON.stringify(submission) }),
  skipResponse: (id) => jsonFetch(`/cases/${id}/skip-response`, { method: 'POST' }),
  runPipeline: (id) => jsonFetch(`/cases/${id}/run`, { method: 'POST' }),
  mediationDecision: (id, accept) =>
    jsonFetch(`/cases/${id}/mediation`, { method: 'POST', body: JSON.stringify({ accept }) }),
  requestReview: (id) => jsonFetch(`/cases/${id}/request-review`, { method: 'POST' }),
  reviewQueue: () => jsonFetch('/reviews/queue'),
  reviewDetail: (id) => jsonFetch(`/reviews/${id}`),
  submitReviewDecision: (id, decision) =>
    jsonFetch(`/reviews/${id}/decision`, { method: 'POST', body: JSON.stringify(decision) }),
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
          } catch {
            // Malformed SSE frame -- skip it rather than crash the stream.
          }
        }
      }
      onDone && onDone()
    })
    .catch((err) => {
      if (err.name !== 'AbortError') onError && onError(err)
    })
  return () => controller.abort()
}
