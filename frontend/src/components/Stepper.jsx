import { useLanguage } from '../i18n/LanguageContext.jsx'

export default function Stepper({ current }) {
  const { t } = useLanguage()
  const STEPS = [t('stepper.dispute'), t('stepper.fileClaim'), t('stepper.response'), t('stepper.resolution')]
  return (
    <div className="stepper">
      {STEPS.map((label, i) => (
        <div key={label} style={{ display: 'flex', alignItems: 'center' }}>
          <div className={`step-node ${i === current ? 'active' : ''} ${i < current ? 'done' : ''}`}>
            <span className="step-dot">{i < current ? '✓' : i + 1}</span>
            <span>{label}</span>
          </div>
          {i < STEPS.length - 1 && <span className="step-sep" />}
        </div>
      ))}
    </div>
  )
}
