// Thin API client for the new /auth/* + /me endpoints. Separate from
// ../api.js (which targets /api/* case-filing endpoints under the old
// Aadhaar-demo login) since these live at a different path prefix and use a
// different credential scheme: httpOnly refresh cookie + in-memory bearer
// access token, not a localStorage token.

const AUTH_BASE = '/auth'

async function jsonFetch(path, options = {}) {
  const { headers, ...rest } = options
  const res = await fetch(path, {
    // Required so the browser sends/receives the httpOnly refresh cookie.
    credentials: 'include',
    ...rest,
    // Spread after `rest` so a caller-supplied `headers` (e.g. the auth
    // Authorization header) merges with, rather than replaces, the default
    // Content-Type -- `...options` after a computed `headers` object would
    // silently drop Content-Type whenever a caller passes its own headers.
    headers: { 'Content-Type': 'application/json', ...(headers || {}) },
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
  if (res.status === 204) return null
  return res.json()
}

function authHeader(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export const authApi = {
  signupEmail: (payload) =>
    jsonFetch(`${AUTH_BASE}/signup/email`, { method: 'POST', body: JSON.stringify(payload) }),
  signupPhoneStart: (phone) =>
    jsonFetch(`${AUTH_BASE}/signup/phone/start`, { method: 'POST', body: JSON.stringify({ phone }) }),
  signupPhoneVerify: (payload) =>
    jsonFetch(`${AUTH_BASE}/signup/phone/verify`, { method: 'POST', body: JSON.stringify(payload) }),

  loginEmail: (email, password) =>
    jsonFetch(`${AUTH_BASE}/login/email`, { method: 'POST', body: JSON.stringify({ email, password }) }),
  loginPhoneStart: (phone) =>
    jsonFetch(`${AUTH_BASE}/login/phone/start`, { method: 'POST', body: JSON.stringify({ phone }) }),
  loginPhoneVerify: (phone, otp) =>
    jsonFetch(`${AUTH_BASE}/login/phone/verify`, { method: 'POST', body: JSON.stringify({ phone, otp }) }),

  linkPhoneStart: (phone, token) =>
    jsonFetch(`${AUTH_BASE}/link/phone/start`, {
      method: 'POST',
      headers: authHeader(token),
      body: JSON.stringify({ phone }),
    }),
  linkPhoneVerify: (phone, otp, token) =>
    jsonFetch(`${AUTH_BASE}/link/phone/verify`, {
      method: 'POST',
      headers: authHeader(token),
      body: JSON.stringify({ phone, otp }),
    }),

  refresh: () => jsonFetch(`${AUTH_BASE}/refresh`, { method: 'POST' }),
  logout: (token) => jsonFetch(`${AUTH_BASE}/logout`, { method: 'POST', headers: authHeader(token) }),
  me: (token) => jsonFetch('/me', { headers: authHeader(token) }),

  passwordResetRequest: (email) =>
    jsonFetch(`${AUTH_BASE}/password/reset/request`, { method: 'POST', body: JSON.stringify({ email }) }),
  passwordResetConfirm: (token, new_password) =>
    jsonFetch(`${AUTH_BASE}/password/reset/confirm`, {
      method: 'POST',
      body: JSON.stringify({ token, new_password }),
    }),
  verifyEmail: (token) => jsonFetch(`${AUTH_BASE}/verify-email?token=${encodeURIComponent(token)}`),
}
