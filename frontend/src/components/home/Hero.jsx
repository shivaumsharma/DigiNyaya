import { useEffect, useRef, useState } from 'react'
import { useLanguage } from '../../i18n/LanguageContext.jsx'
import LanguageMenu from './LanguageMenu.jsx'
import NavLinks from './NavLinks.jsx'
import { ImagePlate } from './shared.jsx'
import { usePrefersReducedMotion } from './hooks.js'
import { createDust, drawHero } from './heroCanvas.js'

const CHIP_LAYOUT = [
  { side: 'left', x: 8, y: 19, depth: 30 },
  { side: 'right', x: 8, y: 17, depth: 18 },
  { side: 'left', x: 24, y: 29, depth: 14, minor: true },
  { side: 'right', x: 22, y: 29, depth: 26, minor: true },
  { side: 'left', x: 12, y: 68, depth: 22 },
  { side: 'right', x: 12, y: 67, depth: 34 },
]

function Letters({ text, offset }) {
  return text.split('').map((ch, i) => (
    <span key={i} className="dn-ch" style={{ '--d': `${(offset + i * 0.06).toFixed(2)}s` }}>
      {ch}
    </span>
  ))
}

export default function Hero({ onFile, account }) {
  const { t } = useLanguage()
  const reduced = usePrefersReducedMotion()
  const heroRef = useRef(null)
  const canvasRef = useRef(null)
  const chipsRef = useRef(null)
  const plateRef = useRef(null)
  const [entered, setEntered] = useState(false)
  const loaded = reduced || entered
  const chips = t('landing.chips')

  useEffect(() => {
    if (reduced) return undefined
    const id = setTimeout(() => setEntered(true), 80)
    return () => clearTimeout(id)
  }, [reduced])

  useEffect(() => {
    const cv = canvasRef.current
    const hero = heroRef.current
    if (!cv || !hero) return undefined
    const ctx = cv.getContext('2d')
    if (!ctx) return undefined
    const fine = window.matchMedia ? window.matchMedia('(pointer: fine)').matches : true
    const mouse = { x: 0.5, y: 0.5, tx: 0.5, ty: 0.5 }
    const dust = createDust(window.innerWidth < 760 ? 36 : 90)
    const k = reduced ? 0 : 1
    let W = 0
    let H = 0
    let raf = 0
    let running = false
    let frames = 0
    const t0 = performance.now()

    const size = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      W = cv.clientWidth
      H = cv.clientHeight
      cv.width = W * dpr
      cv.height = H * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    // Hide a floating chip if it would overlap the copy/stat blocks at the bottom.
    const collide = () => {
      const box = chipsRef.current
      if (!box) return
      const foot = [...hero.querySelectorAll('[data-hero-foot] > *')].map((e) => e.getBoundingClientRect()).filter((r) => r.width)
      const M = 32
      const hit = (a, b) => a.left < b.right + M && a.right + M > b.left && a.top < b.bottom + M && a.bottom + M > b.top
      box.querySelectorAll('[data-depth]').forEach((el) => {
        const r = el.getBoundingClientRect()
        const hide = (el.dataset.minor === '1' && W < 1180) || foot.some((f) => hit(r, f))
        el.style.visibility = hide ? 'hidden' : 'visible'
      })
    }

    const paint = (now) => {
      const t = (now - t0) / 1000
      mouse.x += (mouse.tx - mouse.x) * 0.05
      mouse.y += (mouse.ty - mouse.y) * 0.05
      drawHero(ctx, W, H, t, mouse, k, dust)
      const box = chipsRef.current
      if (box) {
        box.querySelectorAll('[data-depth]').forEach((el) => {
          const q = Number(el.dataset.depth)
          el.style.transform = `translate(${(mouse.x - 0.5) * -q * 1.6}px, ${(mouse.y - 0.5) * -q * 1.6}px)`
        })
      }
      if (plateRef.current) {
        plateRef.current.style.transform = `perspective(900px) rotateY(${(mouse.x - 0.5) * 8}deg) rotateX(${(mouse.y - 0.5) * -8}deg)`
      }
    }

    const frame = (now) => {
      if (!running) return
      if (cv.clientWidth !== W || cv.clientHeight !== H) {
        size()
        collide()
      }
      paint(now)
      if (++frames % 30 === 0) collide()
      raf = requestAnimationFrame(frame)
    }
    const start = () => {
      if (running || reduced) return
      running = true
      raf = requestAnimationFrame(frame)
    }
    const stop = () => {
      running = false
      cancelAnimationFrame(raf)
    }

    const onResize = () => {
      size()
      collide()
      if (reduced || !running) paint(performance.now())
    }
    const onMove = (e) => {
      mouse.tx = e.clientX / window.innerWidth
      mouse.ty = e.clientY / window.innerHeight
    }
    const onVisibility = () => (document.hidden ? stop() : visible && start())

    let visible = true
    const io = typeof IntersectionObserver !== 'undefined'
      ? new IntersectionObserver(([e]) => {
          visible = e.isIntersecting
          if (visible && !document.hidden) start()
          else stop()
        })
      : null
    io?.observe(hero)

    size()
    collide()
    paint(performance.now())
    start()
    window.addEventListener('resize', onResize)
    document.addEventListener('visibilitychange', onVisibility)
    if (fine && !reduced) window.addEventListener('mousemove', onMove)
    const settle = setTimeout(collide, 1500) // chips finish fading in after layout settles

    return () => {
      stop()
      io?.disconnect()
      clearTimeout(settle)
      window.removeEventListener('resize', onResize)
      window.removeEventListener('mousemove', onMove)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [reduced])

  return (
    <section className={`dn-hero ${loaded ? 'is-loaded' : ''}`} ref={heroRef} data-hero>
      <canvas ref={canvasRef} className="dn-hero-canvas" aria-hidden="true" />
      <div className="dn-hero-vignette" aria-hidden="true" />

      <nav className="dn-hero-nav" aria-label="Primary">
        <div className="dn-logo">
          Digi<em>Nyaya</em>
        </div>
        <NavLinks />
        <div className="dn-hero-nav-actions">
          <LanguageMenu />
          <button type="button" className="dn-signin" onClick={account.onClick}>
            {account.label}
          </button>
          <button type="button" className="dn-btn sm" onClick={onFile}>
            {t('landing.fileDispute')}
          </button>
        </div>
      </nav>

      <div className="dn-wm-wrap">
        <h1 className="dn-wordmark" aria-label="DigiNyaya">
          <span className="dn-wm-l" aria-hidden="true">
            <Letters text="Digi" offset={0.15} />
          </span>
          <span className="dn-wm-r" aria-hidden="true">
            <Letters text="Nyaya" offset={0.45} />
          </span>
        </h1>
      </div>

      <div className="dn-plate" aria-hidden="true">
        <div className="dn-plate-inner" ref={plateRef}>
          <ImagePlate shape="arch" />
        </div>
      </div>

      <div className="dn-chips" ref={chipsRef} aria-hidden="true">
        {CHIP_LAYOUT.map((c, i) => (
          <div
            key={i}
            className="dn-chip-pos"
            data-depth={c.depth}
            data-minor={c.minor ? '1' : ''}
            style={{ [c.side === 'left' ? 'left' : 'right']: `${c.x}%`, top: `${c.y}%` }}
          >
            <div className="dn-chip-float" style={{ '--d': `${(1.2 + i * 0.15).toFixed(2)}s`, '--dur': `${5 + (i % 3)}s`, '--fd': `${-i * 0.8}s` }}>
              <div className="dn-chip">
                <span className="dn-chip-k">{chips[i]?.k}</span>
                {chips[i]?.v}
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="dn-sample-note" aria-hidden="true">
        {t('landing.sampleNote')}
      </div>

      <div className="dn-hero-foot" data-hero-foot>
        <div className="dn-hero-copy">
          <p>{t('landing.heroTitle')}</p>
          <div className="dn-hero-actions">
            <button type="button" className="dn-btn" onClick={onFile}>
              {t('landing.ctaFile')}
            </button>
            <a href="#lifecycle" className="dn-textlink">
              {t('landing.ctaWatch')}
            </a>
          </div>
        </div>
        <div className="dn-scroll-cue" aria-hidden="true">
          <span>{t('landing.scroll')}</span>
          <span className="dn-scroll-line">
            <span />
          </span>
        </div>
        <div className="dn-hero-stat">
          <div className="dn-hero-stat-v tnum">{t('landing.stat1Value')}</div>
          <div className="dn-hero-stat-l">{t('landing.stat1Label')}</div>
        </div>
      </div>
    </section>
  )
}
