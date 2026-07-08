import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import App from './App.jsx'
import { SessionProvider } from './session.jsx'
import Landing from './pages/Landing.jsx'
import Disputes from './pages/Disputes.jsx'
import NewCase from './pages/NewCase.jsx'
import Respondent from './pages/Respondent.jsx'
import Resolve from './pages/Resolve.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<App />}>
            <Route path="/" element={<Landing />} />
            <Route path="/disputes" element={<Disputes />} />
            <Route path="/file/:type" element={<NewCase />} />
            <Route path="/case/:id/respond" element={<Respondent />} />
            <Route path="/case/:id" element={<Resolve />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  </StrictMode>,
)
