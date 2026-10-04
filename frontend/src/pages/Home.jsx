import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext.jsx'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import Hero from '../components/home/Hero.jsx'
import StickyNav from '../components/home/StickyNav.jsx'
import TrustStrip from '../components/home/TrustStrip.jsx'
import Problem from '../components/home/Problem.jsx'
import About from '../components/home/About.jsx'
import TierRouter from '../components/home/TierRouter.jsx'
import AgentSlider from '../components/home/AgentSlider.jsx'
import ConfidenceGate from '../components/home/ConfidenceGate.jsx'
import Lifecycle from '../components/home/Lifecycle.jsx'
import Outcomes from '../components/home/Outcomes.jsx'
import { Legal, Founder } from '../components/home/LegalFounder.jsx'
import { Close, HomeFooter } from '../components/home/Close.jsx'
import './home.css'

export default function Home() {
  const { user } = useAuth()
  const { t } = useLanguage()
  const navigate = useNavigate()

  function goFileDispute() {
    if (user) navigate('/disputes')
    else navigate('/login', { state: { from: { pathname: '/disputes' } } })
  }

  const account = user
    ? { label: t('landing.myCases'), onClick: () => navigate('/my-cases') }
    : { label: t('landing.signIn'), onClick: () => navigate('/login') }

  return (
    <div className="dn-home">
      <StickyNav onFile={goFileDispute} />
      <Hero onFile={goFileDispute} account={account} />
      <TrustStrip />
      <Problem />
      <About />
      <TierRouter />
      <AgentSlider />
      <ConfidenceGate />
      <Lifecycle />
      <Outcomes />
      <Legal />
      <Founder />
      <Close onFile={goFileDispute} />
      <HomeFooter />
    </div>
  )
}
