import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext.jsx'

// Same shape as ProtectedRoute.jsx, plus the is_reviewer gate -- a logged-in
// citizen who isn't a reviewer gets bounced to /disputes rather than /login
// (they ARE authenticated, just not authorized for this section).
export default function ProtectedReviewerRoute() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="card card-pad fade-in" style={{ maxWidth: 420, margin: '60px auto', textAlign: 'center' }}>
        Checking your session…
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  if (!user.is_reviewer) {
    return <Navigate to="/disputes" replace />
  }

  return <Outlet />
}
