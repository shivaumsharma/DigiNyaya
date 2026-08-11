// Minimal inline SVG icon set (no icon-library dependency).
const base = {
  width: 20,
  height: 20,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
}

export const Scales = (p) => (
  <svg {...base} {...p}>
    <path d="M12 3v18M7 21h10M5 7h14M5 7l-3 6a3 3 0 0 0 6 0L5 7zM19 7l-3 6a3 3 0 0 0 6 0l-3-6z" />
  </svg>
)
// Logo mark used in the header and the resolution document seal -- distinct
// two-tone rendering (ink circle/base, gold scale) rather than a single
// currentColor stroke, so it reads correctly regardless of surrounding text color.
export const Logo = (p) => (
  <svg width="28" height="28" viewBox="0 0 40 40" fill="none" {...p}>
    <circle cx="20" cy="20" r="19" stroke="var(--color-text)" strokeWidth="1.4" />
    <path
      d="M20 8v22M20 8l-8 6M20 8l8 6M8 14h4M28 14h4M12 14l-4 8a4 4 0 0 0 8 0l-4-8ZM28 14l-4 8a4 4 0 0 0 8 0l-4-8Z"
      stroke="var(--color-accent)"
      strokeWidth="1.3"
      strokeLinejoin="round"
    />
    <rect x="14" y="30" width="12" height="2.4" rx="1.2" fill="var(--color-text)" />
  </svg>
)
export const Eye = (p) => (
  <svg {...base} {...p}>
    <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7Z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
)
export const EyeOff = (p) => (
  <svg {...base} {...p}>
    <path d="M2 2l20 20M9.9 9.9a3 3 0 0 0 4.2 4.2M6.5 6.7C4 8.3 2 12 2 12s4 7 11 7c1.9 0 3.6-.5 5-1.2M15.6 5.4A11.6 11.6 0 0 1 23 12s-1 1.8-2.9 3.5" />
  </svg>
)
export const ShoppingBag = (p) => (
  <svg {...base} {...p}>
    <path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4zM3 6h18M16 10a4 4 0 0 1-8 0" />
  </svg>
)
export const Banknote = (p) => (
  <svg {...base} {...p}>
    <rect x="2" y="6" width="20" height="12" rx="2" /><circle cx="12" cy="12" r="2.5" />
    <path d="M6 12h.01M18 12h.01" />
  </svg>
)
export const FileText = (p) => (
  <svg {...base} {...p}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6M9 13h6M9 17h6" />
  </svg>
)
export const Mic = (p) => (
  <svg {...base} {...p}>
    <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
    <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4M8 22h8" />
  </svg>
)
export const Volume2 = (p) => (
  <svg {...base} {...p}>
    <path d="M4 9v6h4l5 5V4L8 9H4z" />
    <path d="M17.5 8.5a5 5 0 0 1 0 7M20.5 5.5a9 9 0 0 1 0 13" />
  </svg>
)
export const PauseCircle = (p) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="10" />
    <path d="M10 9v6M14 9v6" />
  </svg>
)
export const Receipt = (p) => (
  <svg {...base} {...p}>
    <path d="M5 2v20l2-1.5L9 22l2-1.5L13 22l2-1.5L17 22l2-1.5V2l-2 1.5L15 2l-2 1.5L11 2 9 3.5 7 2z" />
    <path d="M8 7h8M8 11h8M8 15h5" />
  </svg>
)
export const Cpu = (p) => (
  <svg {...base} {...p}>
    <rect x="6" y="6" width="12" height="12" rx="2" /><rect x="9" y="9" width="6" height="6" />
    <path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2" />
  </svg>
)
export const Search = (p) => (
  <svg {...base} {...p}><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
)
export const GitCompare = (p) => (
  <svg {...base} {...p}>
    <circle cx="6" cy="6" r="3" /><circle cx="18" cy="18" r="3" />
    <path d="M9 6h6a3 3 0 0 1 3 3v6M15 18H9a3 3 0 0 1-3-3V9" />
  </svg>
)
export const Handshake = (p) => (
  <svg {...base} {...p}>
    <path d="m11 17 2 2a1 1 0 0 0 1.4 0l3.6-3.6M3 12l4-4 4 4M21 12l-4-4-4 4" />
    <path d="m7 8 3 3M17 8l-3 3M13 19l-1.5-1.5" />
  </svg>
)
export const Gavel = (p) => (
  <svg {...base} {...p}>
    <path d="m14 11-6 6M3 22h10M14.5 3.5l6 6M9 9l6-6 3 3-6 6zM12 12l3 3" />
  </svg>
)
export const Check = (p) => (
  <svg {...base} {...p}><path d="M20 6 9 17l-5-5" /></svg>
)
export const Shield = (p) => (
  <svg {...base} {...p}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="m9 12 2 2 4-4" /></svg>
)
export const Clock = (p) => (
  <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
)
export const ArrowRight = (p) => (
  <svg {...base} {...p}><path d="M5 12h14M13 6l6 6-6 6" /></svg>
)
export const ArrowLeft = (p) => (
  <svg {...base} {...p}><path d="M19 12H5M11 18l-6-6 6-6" /></svg>
)
export const Download = (p) => (
  <svg {...base} {...p}><path d="M12 3v12M7 10l5 5 5-5M5 21h14" /></svg>
)
export const UploadCloud = (p) => (
  <svg {...base} {...p}>
    <path d="M16 16l-4-4-4 4M12 12v9" />
    <path d="M20.4 18.5A4.5 4.5 0 0 0 18 10h-1.3A7 7 0 1 0 4 15.9" />
  </svg>
)
export const AlertTriangle = (p) => (
  <svg {...base} {...p}>
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    <path d="M12 9v4M12 17h.01" />
  </svg>
)
export const XCircle = (p) => (
  <svg {...base} {...p}><circle cx="12" cy="12" r="10" /><path d="m15 9-6 6M9 9l6 6" /></svg>
)
export const Bot = (p) => (
  <svg {...base} {...p}>
    <rect x="4" y="8" width="16" height="12" rx="3" /><path d="M12 8V4M9 4h6" />
    <circle cx="9" cy="14" r="1" /><circle cx="15" cy="14" r="1" /><path d="M2 13v3M22 13v3" />
  </svg>
)

export const DISPUTE_ICONS = {
  'shopping-bag': ShoppingBag,
  banknote: Banknote,
  'file-text': FileText,
  receipt: Receipt,
}

export const AGENT_ICONS = {
  orchestrator: Bot,
  ingestion: FileText,
  research: Search,
  analysis: GitCompare,
  mediation: Handshake,
  resolution: Gavel,
}
