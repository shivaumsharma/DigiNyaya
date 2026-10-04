import { useRef } from 'react'
import { useInView } from './hooks.js'

// Fades and lifts its children in the first time they scroll into view.
// (home.css makes .dn-reveal visible at rest when motion is reduced.)
export function Reveal({ as: Tag = 'div', className = '', children, ...rest }) {
  const ref = useRef(null)
  const seen = useInView(ref)
  return (
    <Tag ref={ref} className={`dn-reveal ${seen ? 'in' : ''} ${className}`.trim()} {...rest}>
      {children}
    </Tag>
  )
}

export function ScalesIcon({ size = 24, stroke = 1.4, ...rest }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--dn-acc)"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      <path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z" />
      <path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z" />
      <path d="M7 21h10" />
      <path d="M12 3v18" />
      <path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2" />
    </svg>
  )
}

export function Chevron({ dir = 'right', size = 20 }) {
  const d = { left: 'm15 18-6-6 6-6', right: 'm9 18 6-6-6-6', down: 'm6 9 6 6 6-6' }[dir]
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  )
}

export function GlobeIcon({ size = 15 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <path d="M2 12h20" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  )
}

// A neutral placeholder plate: fine concentric rings and a plumb line, no
// stock photography. Pass `src` to show a real image (archival grade is applied
// in CSS) and `alt` for its description.
export function ImagePlate({ src, alt = '', shape = 'rect', label, mono, className = '' }) {
  return (
    <div className={`dn-plate-frame ${shape} ${className}`.trim()} role={src ? undefined : 'img'} aria-label={src ? undefined : label || undefined} aria-hidden={src || label ? undefined : true}>
      {src ? (
        <img src={src} alt={alt} loading="lazy" />
      ) : (
        <svg viewBox="0 0 120 160" preserveAspectRatio="xMidYMid slice" fill="none" stroke="var(--dn-acc)" aria-hidden="true">
          <g strokeWidth="0.6" opacity="0.28">
            {[14, 26, 38, 50, 62, 74].map((r) => (
              <circle key={r} cx="60" cy="86" r={r} />
            ))}
          </g>
          <g strokeWidth="0.6" opacity="0.2">
            <path d="M60 10v140M14 86h92" />
          </g>
          {mono ? (
            <text x="60" y="96" textAnchor="middle" fontSize="34" fill="var(--dn-acc)" stroke="none" opacity="0.9" style={{ fontFamily: 'var(--font-heading)' }}>
              {mono}
            </text>
          ) : null}
        </svg>
      )}
    </div>
  )
}
