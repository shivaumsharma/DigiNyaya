import { StrictMode } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { AuthProvider, useAuth } from './AuthContext.jsx'
import { authApi } from './authApi.js'
import { getAccessToken } from './tokenStore.js'

vi.mock('./authApi.js', () => ({
  authApi: {
    refresh: vi.fn(),
    me: vi.fn(),
    logout: vi.fn(),
  },
}))

function Probe() {
  const { user, loading, login, logout } = useAuth()
  if (loading) return <span>loading</span>
  return (
    <div>
      <span data-testid="user">{user ? user.full_name : 'anonymous'}</span>
      <button onClick={() => login({ access_token: 'tok-123' })}>do-login</button>
      <button onClick={() => logout()}>do-logout</button>
    </div>
  )
}

describe('AuthContext', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('silently refreshes on mount and populates the user from the resulting token', async () => {
    authApi.refresh.mockResolvedValue({ access_token: 'refreshed-tok' })
    authApi.me.mockResolvedValue({ full_name: 'Grace Hopper' })

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    expect(screen.getByText('loading')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('Grace Hopper'))
    expect(getAccessToken()).toBe('refreshed-tok')
    expect(authApi.me).toHaveBeenCalledWith('refreshed-tok')
  })

  it('clears the session when the silent refresh fails (no valid cookie)', async () => {
    authApi.refresh.mockRejectedValue(new Error('no refresh cookie'))

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('anonymous'))
    expect(getAccessToken()).toBeNull()
    expect(authApi.me).not.toHaveBeenCalled()
  })

  it('calls authApi.refresh only once under React StrictMode double-invocation', async () => {
    // Regression test for the race documented at the top of AuthContext.jsx:
    // StrictMode mounts effects twice in dev, and without the shared
    // refreshPromiseRef, two concurrent /auth/refresh calls would both
    // present the same rotating refresh cookie -- the second is flagged as
    // token replay and revokes the session the first call just established.
    authApi.refresh.mockResolvedValue({ access_token: 'refreshed-tok' })
    authApi.me.mockResolvedValue({ full_name: 'Grace Hopper' })

    render(
      <StrictMode>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </StrictMode>,
    )

    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('Grace Hopper'))
    expect(authApi.refresh).toHaveBeenCalledTimes(1)
  })

  it('login() sets the access token and fetches the profile', async () => {
    authApi.refresh.mockRejectedValue(new Error('no cookie'))
    authApi.me.mockResolvedValue({ full_name: 'Ada Lovelace' })

    const user = (await import('@testing-library/user-event')).default.setup()
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('anonymous'))

    authApi.me.mockResolvedValueOnce({ full_name: 'Ada Lovelace' })
    await user.click(screen.getByText('do-login'))

    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('Ada Lovelace'))
    expect(getAccessToken()).toBe('tok-123')
  })

  it('logout() clears the local session even when the network call fails', async () => {
    authApi.refresh.mockResolvedValue({ access_token: 'refreshed-tok' })
    authApi.me.mockResolvedValue({ full_name: 'Grace Hopper' })
    authApi.logout.mockRejectedValue(new Error('network down'))

    const user = (await import('@testing-library/user-event')).default.setup()
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('Grace Hopper'))

    await user.click(screen.getByText('do-logout'))

    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('anonymous'))
    expect(getAccessToken()).toBeNull()
  })
})
