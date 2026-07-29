// Shared in-memory access-token accessor. AuthContext.jsx (a React context,
// only readable via useAuth() inside components) sets this on
// login/refresh/logout; api.js (a plain module used by non-React case-filing
// API calls) reads it here instead of localStorage -- decoupling the two
// without threading the token through every call site by hand.

let _accessToken = null

export function setAccessToken(token) {
  _accessToken = token
}

export function getAccessToken() {
  return _accessToken
}
