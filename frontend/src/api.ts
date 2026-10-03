// Thin API client for the DigiNyaya backend. In dev, relative paths go
// through the Vite proxy (/api -> :8000). In production the frontend and
// backend are separate deploys on different domains, so VITE_API_BASE points
// straight at the backend's origin (see .env.production / Render env vars).
import { getAccessToken, setAccessToken } from './auth/tokenStore'
import { authApi } from './auth/authApi'

const BASE = `${import.meta.env.VITE_API_BASE || ''}/api`

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

function authHeaders(): Record<string, string> {
  const token = getAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// Access tokens are short-lived (60 min). A case-filing session (details ->
// evidence -> review -> submit) can easily outlast that, so any call here can
// hit a 401 mid-flow. Refresh once via the httpOnly cookie and retry rather
// than surfacing the 401 -- mirrors AuthContext's mount-time refresh, and
// dedupes concurrent 401s the same way (refresh tokens rotate on every use,
// so two overlapping /auth/refresh calls would revoke the session, see
// AuthContext.jsx).
let refreshPromise: Promise<string | null> | null = null

function refreshAccessToken(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = authApi
      .refresh()
      .then(({ access_token }: { access_token: string }) => {
        setAccessToken(access_token)
        return access_token
      })
      .catch((err: unknown) => {
        setAccessToken(null)
        throw err
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

// Runs `makeOptions()` -> fetch; on a 401, refreshes the access token once
// and retries with freshly-rebuilt options (so the new token is picked up).
// If refresh itself fails (no valid session), the original 401 response is
// returned unchanged.
async function fetchWithRefresh(url: string, makeOptions: () => RequestInit): Promise<Response> {
  const res = await fetch(url, makeOptions())
  if (res.status !== 401) return res
  try {
    await refreshAccessToken()
  } catch {
    return res
  }
  return fetch(url, makeOptions())
}

async function jsonFetch(path: string, options: RequestInit = {}): Promise<any> {
  const res = await fetchWithRefresh(BASE + path, () => ({
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    ...options,
  }))
  if (!res.ok) {
    let msg = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body.detail) msg = body.detail
    } catch {
      // Response body wasn't JSON -- fall back to the generic message above.
    }
    throw new ApiError(msg, res.status)
  }
  return res.json()
}

async function uploadFetch(path: string, files: FileList | File[]): Promise<any> {
  const body = new FormData()
  for (const file of files) body.append('files', file)
  const res = await fetchWithRefresh(BASE + path, () => ({ method: 'POST', headers: authHeaders(), body }))
  if (!res.ok) {
    let msg = `Request failed (${res.status})`
    try {
      const data = await res.json()
      if (data.detail) msg = data.detail
    } catch {
      // Response body wasn't JSON -- fall back to the generic message above.
    }
    throw new ApiError(msg, res.status)
  }
  return res.json()
}

async function blobFetch(path: string): Promise<Blob> {
  const res = await fetchWithRefresh(BASE + path, () => ({ headers: { ...authHeaders() } }))
  if (!res.ok) {
    let msg = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body.detail) msg = body.detail
    } catch {
      // Response body wasn't JSON -- fall back to the generic message above.
    }
    throw new ApiError(msg, res.status)
  }
  return res.blob()
}

export const api = {
  aiStatus: () => jsonFetch('/ai-status'),
  languages: () => jsonFetch('/languages'),
  disputeTypes: () => jsonFetch('/dispute-types'),
  sampleClaim: (disputeType?: string) => jsonFetch(`/sample-claim${disputeType ? `?dispute_type=${encodeURIComponent(disputeType)}` : ''}`),
  precedents: () => jsonFetch('/precedents'),
  myCases: () => jsonFetch('/cases'),
  createCase: (claim: Record<string, unknown>) => jsonFetch('/cases', { method: 'POST', body: JSON.stringify(claim) }),
  classifyDisputeType: (description: string, selectedType?: string) =>
    jsonFetch('/classify-dispute-type', {
      method: 'POST',
      body: JSON.stringify({ description, selected_type: selectedType }),
    }),
  getCase: (id: string, lang?: string) => jsonFetch(`/cases/${id}${lang ? `?lang=${encodeURIComponent(lang)}` : ''}`),
  submitCase: (id: string, confirmedAccurate: boolean) =>
    jsonFetch(`/cases/${id}/submit`, { method: 'POST', body: JSON.stringify({ confirmed_accurate: confirmedAccurate }) }),
  uploadDocuments: (id: string, files: FileList | File[]) => uploadFetch(`/cases/${id}/documents`, files),
  listDocuments: (id: string) => jsonFetch(`/cases/${id}/documents`),
  preliminaryReview: (id: string) => jsonFetch(`/cases/${id}/preliminary-review`, { method: 'POST' }),
  respond: (id: string, submission: Record<string, unknown>) =>
    jsonFetch(`/cases/${id}/respond`, { method: 'POST', body: JSON.stringify(submission) }),
  skipResponse: (id: string) => jsonFetch(`/cases/${id}/skip-response`, { method: 'POST' }),
  runPipeline: (id: string) => jsonFetch(`/cases/${id}/run`, { method: 'POST' }),
  mediationDecision: (id: string, accept: boolean) =>
    jsonFetch(`/cases/${id}/mediation`, { method: 'POST', body: JSON.stringify({ accept }) }),
  mediationAudio: (id: string, lang?: string) => blobFetch(`/cases/${id}/mediation/audio${lang ? `?lang=${encodeURIComponent(lang)}` : ''}`),
  resolutionAudio: (id: string, lang?: string) => blobFetch(`/cases/${id}/resolution/audio${lang ? `?lang=${encodeURIComponent(lang)}` : ''}`),
  requestReview: (id: string) => jsonFetch(`/cases/${id}/request-review`, { method: 'POST' }),
  reviewQueue: () => jsonFetch('/reviews/queue'),
  evalMetrics: () => jsonFetch('/reviews/eval-metrics'),
  opsMetrics: () => jsonFetch('/reviews/ops-metrics'),
  reviewDetail: (id: string) => jsonFetch(`/reviews/${id}`),
  submitReviewDecision: (id: string, decision: Record<string, unknown>) =>
    jsonFetch(`/reviews/${id}/decision`, { method: 'POST', body: JSON.stringify(decision) }),
}

export interface SSEEvent {
  type: string
  [key: string]: unknown
}

interface StreamSSEHandlers {
  onEvent?: (data: SSEEvent) => void
  onDone?: () => void
  onError?: (err: Error) => void
}

// Stream Server-Sent Events. Calls onEvent for every parsed event object,
// onDone when the stream closes, onError on failure. Sends the auth header so
// ownership is enforced. Returns an abort function.
export function streamSSE(path: string, { onEvent, onDone, onError }: StreamSSEHandlers): () => void {
  const controller = new AbortController()
  fetchWithRefresh(BASE + path, () => ({ signal: controller.signal, headers: { ...authHeaders() } }))
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
    .catch((err: Error) => {
      if (err.name !== 'AbortError') onError && onError(err)
    })
  return () => controller.abort()
}
