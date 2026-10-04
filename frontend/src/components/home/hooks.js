import { useEffect, useState } from 'react'

const REDUCED_QUERY = '(prefers-reduced-motion: reduce)'

export function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(REDUCED_QUERY).matches : false,
  )
  useEffect(() => {
    if (!window.matchMedia) return undefined
    const mq = window.matchMedia(REDUCED_QUERY)
    const onChange = (e) => setReduced(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}

// True once the element has been at least `threshold` visible. With `once`
// (default) it stays true. Without IntersectionObserver it is simply true.
export function useInView(ref, { threshold = 0, rootMargin = '0px 0px -8% 0px', once = true } = {}) {
  const [inView, setInView] = useState(() => typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return undefined
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            setInView(true)
            if (once) io.unobserve(el)
          } else if (!once) {
            setInView(false)
          }
        })
      },
      { threshold, rootMargin },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [ref, threshold, rootMargin, once])
  return inView
}

const ease = (p) => 1 - Math.pow(1 - p, 3)

// 0..1 progress that runs once when `active` flips true (ease-out cubic).
// Reduced motion jumps straight to 1 so final numbers show immediately.
export function useCountUp(active, { ms = 1600, disabled = false } = {}) {
  const [p, setP] = useState(0)
  useEffect(() => {
    if (!active || disabled) return undefined
    let raf
    const t0 = performance.now()
    const step = (now) => {
      const x = Math.min(1, (now - t0) / ms)
      setP(ease(x))
      if (x < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [active, ms, disabled])
  return disabled && active ? 1 : p
}

// Scales the first ASCII number in a display string by p (keeps decimals,
// thousands separators and surrounding text). Strings in native digits
// (Bengali, Gujarati, Odia, Punjabi) don't match and show their final value.
export function countStr(str, p) {
  return String(str ?? '').replace(/\d[\d,]*(?:\.\d+)?/, (n) => {
    const target = parseFloat(n.replace(/,/g, ''))
    const v = target * p
    if (n.includes('.')) return v.toFixed(n.split('.')[1].length)
    const rounded = Math.round(v)
    return n.includes(',') ? rounded.toLocaleString('en-IN') : String(rounded)
  })
}

// Cycles an index 0..n-1 every `ms` while `active`; returns [index, setIndex].
export function useCycle(n, { ms = 4200, active = true } = {}) {
  const [i, setI] = useState(0)
  useEffect(() => {
    if (!active || n < 2) return undefined
    const id = setInterval(() => setI((x) => (x + 1) % n), ms)
    return () => clearInterval(id)
  }, [active, n, ms])
  return [i, setI]
}

// Locales whose existing copy uses native digits (kept consistent for numbers
// computed at runtime, e.g. the gate score).
const NATIVE_DIGITS = {
  'bn-IN': '০১২৩৪৫৬৭৮৯',
  'gu-IN': '૦૧૨૩૪૫૬૭૮૯',
  'od-IN': '୦୧୨୩୪୫୬୭୮୯',
  'pa-IN': '੦੧੨੩੪੫੬੭੮੯',
}

export function localizeDigits(value, lang) {
  const table = NATIVE_DIGITS[lang]
  const str = String(value)
  return table ? str.replace(/\d/g, (d) => table[Number(d)]) : str
}
