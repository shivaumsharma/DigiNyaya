import { describe, it, expect, vi } from 'vitest'
import { createReconnector } from './streamReconnect.js'

function harness(overrides = {}) {
  const calls = []
  let cursor = 0
  let go = true
  const open = vi.fn((after, h) => { calls.push({ after, h }); return vi.fn() })
  const timers = []
  const setTimer = (fn, ms) => { timers.push({ fn, ms }); return timers.length }
  const r = createReconnector({ open, getCursor: () => cursor, shouldContinue: () => go, setTimer, clearTimer: () => {}, ...overrides })
  return { r, calls, timers, open, setCursor: (c) => { cursor = c }, setGo: (g) => { go = g } }
}

describe('createReconnector', () => {
  it('reconnects from the latest cursor after the stream ends while the work is still running', () => {
    const h = harness()
    h.r.start(0, () => {})
    h.setCursor(7)
    h.calls[0].h.onDone()
    expect(h.timers).toHaveLength(1)
    h.timers[0].fn()
    expect(h.calls[1].after).toBe(7)
  })

  it('does not reconnect once the work has finished', () => {
    const h = harness()
    h.r.start(0, () => {})
    h.setGo(false)
    h.calls[0].h.onDone()
    expect(h.timers).toHaveLength(0)
  })

  it('backs off exponentially, caps the delay, and gives up after maxRetries', () => {
    const onGiveUp = vi.fn()
    const h = harness({ maxRetries: 3, baseDelayMs: 1000, maxDelayMs: 3000, onGiveUp })
    h.r.start(0, () => {})
    for (let i = 0; i < 3; i++) { h.calls[i].h.onError(new Error('x')); h.timers[i].fn() }
    expect(h.timers.map((t) => t.ms)).toEqual([1000, 2000, 3000])
    h.calls[3].h.onError(new Error('x'))
    expect(onGiveUp).toHaveBeenCalledOnce()
    expect(h.timers).toHaveLength(3)
  })

  it('resets the retry count when an event arrives', () => {
    const h = harness({ maxRetries: 2 })
    const seen = []
    h.r.start(0, (e) => seen.push(e))
    h.calls[0].h.onError(new Error('x')); h.timers[0].fn()
    h.calls[1].h.onEvent({ seq: 1 })
    h.calls[1].h.onError(new Error('x'))
    expect(h.timers[1].ms).toBe(1000) // back to the base delay, not 2000
    expect(seen).toEqual([{ seq: 1 }])
  })

  it('stop() cancels the pending retry and aborts the open stream', () => {
    const clear = vi.fn()
    const h = harness({ clearTimer: clear })
    h.r.start(0, () => {})
    h.calls[0].h.onDone()
    h.r.stop()
    expect(clear).toHaveBeenCalled()
    h.timers[0].fn()
    expect(h.open).toHaveBeenCalledTimes(1)
  })
})
