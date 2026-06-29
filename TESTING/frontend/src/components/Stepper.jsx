import { Check } from '../icons.jsx'

const STEPS = ['Dispute', 'File Claim', 'Response', 'AI Resolution']

export default function Stepper({ current }) {
  return (
    <div className="stepper">
      {STEPS.map((label, i) => (
        <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div className={`node ${i === current ? 'active' : ''} ${i < current ? 'done' : ''}`}>
            <span className="dot">{i < current ? <Check width={13} height={13} /> : i + 1}</span>
            <span>{label}</span>
          </div>
          {i < STEPS.length - 1 && <span className="sep" />}
        </div>
      ))}
    </div>
  )
}
