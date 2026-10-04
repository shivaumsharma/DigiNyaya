// Pure drawing code for the hero background: a breathing set of concentric
// rings, 24 rotating spokes and 72 ticks (an Ashoka-chakra nod), a gold glow
// that follows the cursor, and drifting dust. `k` is the motion factor
// (0 = static frame, 1 = full motion).

const ACCENT = '#d9b273'
const GLOW = '182,130,53'

export function createDust(n) {
  return Array.from({ length: n }, () => ({
    x: Math.random(),
    y: Math.random(),
    r: Math.random() * 1.4 + 0.3,
    s: Math.random() * 0.00025 + 0.00006,
    a: Math.random() * 0.6 + 0.15,
    p: Math.random() * 6.28,
  }))
}

export function drawHero(ctx, W, H, t, mouse, k, dust) {
  ctx.clearRect(0, 0, W, H)

  const gx = W * (0.5 + (mouse.x - 0.5) * 0.25)
  const gy = H * (0.52 + (mouse.y - 0.5) * 0.25)
  const g = ctx.createRadialGradient(gx, gy, 0, gx, gy, Math.max(W, H) * 0.5)
  g.addColorStop(0, `rgba(${GLOW},0.14)`)
  g.addColorStop(1, `rgba(${GLOW},0)`)
  ctx.fillStyle = g
  ctx.fillRect(0, 0, W, H)

  const cx = W / 2 + (mouse.x - 0.5) * -30
  const cy = H * 0.52 + (mouse.y - 0.5) * -30
  const R = Math.min(W, H) * 0.42

  ctx.strokeStyle = ACCENT
  ctx.lineWidth = 1
  ;[1, 0.78, 0.56, 1.3].forEach((q, i) => {
    ctx.globalAlpha = i === 3 ? 0.06 : 0.12
    ctx.beginPath()
    ctx.arc(cx, cy, R * q + Math.sin(t * 0.6 + i) * 4 * k, 0, Math.PI * 2)
    ctx.stroke()
  })

  const rot = t * 0.04 * k
  ctx.globalAlpha = 0.1
  for (let i = 0; i < 24; i++) {
    const a = rot + (i * Math.PI) / 12
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(a) * R * 0.56, cy + Math.sin(a) * R * 0.56)
    ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R)
    ctx.stroke()
  }

  ctx.globalAlpha = 0.22
  for (let i = 0; i < 72; i++) {
    const a = -rot * 1.5 + (i * Math.PI) / 36
    const l = i % 6 === 0 ? 12 : 5
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R)
    ctx.lineTo(cx + Math.cos(a) * (R + l), cy + Math.sin(a) * (R + l))
    ctx.stroke()
  }

  ctx.fillStyle = ACCENT
  dust.forEach((d) => {
    d.y -= d.s * k * 16
    if (d.y < -0.02) {
      d.y = 1.02
      d.x = Math.random()
    }
    const x = (d.x + Math.sin(t * 0.3 + d.p) * 0.01) * W + (mouse.x - 0.5) * -60 * d.r
    const y = d.y * H + (mouse.y - 0.5) * -40 * d.r
    ctx.globalAlpha = d.a * (0.6 + 0.4 * Math.sin(t * 1.5 + d.p))
    ctx.beginPath()
    ctx.arc(x, y, d.r, 0, Math.PI * 2)
    ctx.fill()
  })
  ctx.globalAlpha = 1
}
