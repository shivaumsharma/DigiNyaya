import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import App from './App.jsx'
import { LanguageProvider } from './i18n/LanguageContext.jsx'
import { AuthProvider } from './auth/AuthContext.jsx'
import ProtectedRoute from './auth/ProtectedRoute.jsx'
import ProtectedReviewerRoute from './auth/ProtectedReviewerRoute.jsx'
import AuthScreen from './auth/AuthScreen.jsx'
import ResetPassword from './auth/ResetPassword.jsx'
import VerifyEmail from './auth/VerifyEmail.jsx'
import Home from './pages/Home.jsx'
import MyCases from './pages/MyCases.jsx'
import Disputes from './pages/Disputes.jsx'
import NewCase from './pages/NewCase.jsx'
import Respondent from './pages/Respondent.jsx'
import Resolve from './pages/Resolve.jsx'
import ReviewerQueue from './pages/ReviewerQueue.jsx'
import ReviewerCaseDetail from './pages/ReviewerCaseDetail.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <LanguageProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<App />}>
              <Route path="/" element={<Home />} />
              <Route path="/login" element={<AuthScreen />} />
              <Route path="/reset-password" element={<ResetPassword />} />
              <Route path="/verify-email" element={<VerifyEmail />} />

              <Route element={<ProtectedRoute />}>
                <Route path="/my-cases" element={<MyCases />} />
                <Route path="/disputes" element={<Disputes />} />
                <Route path="/file/:type" element={<NewCase />} />
                <Route path="/case/:id/respond" element={<Respondent />} />
                <Route path="/case/:id" element={<Resolve />} />
              </Route>

              <Route element={<ProtectedReviewerRoute />}>
                <Route path="/reviewer" element={<ReviewerQueue />} />
                <Route path="/reviewer/:id" element={<ReviewerCaseDetail />} />
              </Route>
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </LanguageProvider>
  </StrictMode>,
)
