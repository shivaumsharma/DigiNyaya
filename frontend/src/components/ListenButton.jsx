import { useEffect, useRef, useState } from 'react'
import { Volume2, PauseCircle, Clock } from '../icons.jsx'
import { useLanguage } from '../i18n/LanguageContext.jsx'

// Fetches narration audio on first click (lazily -- narration costs a real
// Sarvam Bulbul call, so nothing plays until the citizen actually asks to
// listen), then toggles play/pause on the same clip for subsequent clicks.
// `fetchAudio` is one of api.mediationAudio / api.resolutionAudio.
export default function ListenButton({ fetchAudio, label, listeningLabel, unavailableLabel }) {
  const { t } = useLanguage()
  const [state, setState] = useState('idle') // idle | loading | playing | paused | unavailable
  const audioRef = useRef(null)
  const urlRef = useRef(null)

  useEffect(() => {
    return () => {
      audioRef.current?.pause()
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    }
  }, [])

  async function handleClick() {
    if (state === 'playing') {
      audioRef.current.pause()
      setState('paused')
      return
    }
    if (state === 'paused') {
      audioRef.current.play()
      setState('playing')
      return
    }
    if (state === 'loading') return

    setState('loading')
    try {
      const blob = await fetchAudio()
      const url = URL.createObjectURL(blob)
      urlRef.current = url
      const audio = new Audio(url)
      audio.onended = () => setState('paused')
      audioRef.current = audio
      await audio.play()
      setState('playing')
    } catch {
      setState('unavailable')
    }
  }

  if (state === 'unavailable') {
    return <span className="sub" style={{ fontSize: '0.8rem' }}>{unavailableLabel || t('audio.unavailable')}</span>
  }

  return (
    <button type="button" className="btn btn-ghost" onClick={handleClick} disabled={state === 'loading'}>
      {state === 'loading' && <Clock width={16} height={16} />}
      {state === 'playing' && <PauseCircle width={16} height={16} />}
      {(state === 'idle' || state === 'paused') && <Volume2 width={16} height={16} />}
      {' '}
      {state === 'loading' ? t('audio.loading') : state === 'playing' ? (listeningLabel || t('audio.listening')) : label}
    </button>
  )
}
