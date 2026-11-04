const FAMILY_ORDER = ['criminal', 'family', 'civil_other', 'tax', 'service', 'constitutional', 'ip']

export function groupMatters(matters, query = '') {
  const q = query.trim().toLowerCase()
  const visible = q
    ? matters.filter((m) => `${m.label} ${m.description}`.toLowerCase().includes(q))
    : matters
  return {
    resolve: visible.filter((m) => m.status === 'live' && m.can_resolve),
    soon: visible.filter((m) => m.status === 'preview'),
    guides: FAMILY_ORDER.map((family) => ({ family, items: visible.filter((m) => m.status === 'navigator' && m.family === family) }))
      .filter((g) => g.items.length > 0),
  }
}

