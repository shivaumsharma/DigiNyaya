import { Check } from '../icons.jsx'
import { useLanguage } from '../i18n/LanguageContext.jsx'

const STEP_KEYS = ['stepper.dispute', 'stepper.fileClaim', 'stepper.response', 'stepper.resolution']

export default function Stepper({ current }) {
  const { t } = useLanguage()
  return (
    <div className="stepper">
      {STEP_KEYS.map((key, i) => (
        <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div className={`node ${i === current ? 'active' : ''} ${i < current ? 'done' : ''}`}>
            <span className="dot">{i < current ? <Check width={13} height={13} /> : i + 1}</span>
            <span>{t(key)}</span>
          </div>
          {i < STEP_KEYS.length - 1 && <span className="sep" />}
        </div>
      ))}
    </div>
  )
}
