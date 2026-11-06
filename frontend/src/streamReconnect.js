// Keeps a live event stream going across the server's idle timeout (about 120 s without events) and network drops.
// `open(after, handlers)` starts one stream from a cursor and returns a function that aborts it. When a stream ends or
// fails while `shouldContinue()` is true, the next one starts from `getCursor()` after an exponentially growing delay.
// The retry count resets whenever an event arrives, so a long pipeline can reconnect many times but a dead server cannot
// cause an endless loop.
export function createReconnector({ open, getCursor, shouldContinue, maxRetries = 6, baseDelayMs = 1000, maxDelayMs = 10000, onGiveUp,
  setTimer = setTimeout, clearTimer = clearTimeout }) {
  let abort = null
  let timer = null
  let retries = 0
  let stopped = false

  function schedule() {
    if (stopped || !shouldContinue()) return
    if (retries >= maxRetries) {
      onGiveUp && onGiveUp()
      return
    }
    const delay = Math.min(baseDelayMs * 2 ** retries, maxDelayMs)
    retries += 1
    timer = setTimer(() => {
      timer = null
      if (!stopped && shouldContinue()) start(getCursor())
    }, delay)
  }

  function start(after) {
    if (abort) abort()
    abort = open(after, {
      onEvent: (ev) => { retries = 0; handlers.onEvent && handlers.onEvent(ev) },
      onDone: schedule,
      onError: schedule,
    })
  }

  const handlers = {}
  return {
    start(after, onEvent) {
      stopped = false
      retries = 0
      handlers.onEvent = onEvent
      start(after)
    },
    stop() {
      stopped = true
      if (timer) clearTimer(timer)
      timer = null
      if (abort) abort()
      abort = null
    },
  }
}
