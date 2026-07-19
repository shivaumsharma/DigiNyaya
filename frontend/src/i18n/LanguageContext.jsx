import { createContext, useContext, useMemo, useState } from 'react'
import en from './en.json'
import hi from './hi.json'
import ta from './ta.json'
import te from './te.json'
import kn from './kn.json'
import ml from './ml.json'
import mr from './mr.json'
import bn from './bn.json'
import gu from './gu.json'
import pa from './pa.json'
import od from './od.json'

const LanguageContext = createContext(null)

const KEY = 'diginyaya_lang'
const DEFAULT_LANG = 'en-IN'

// Mirrors app.language.config.SUPPORTED_LANGUAGES on the backend -- keep in
// sync if a language is added/removed there.
export const SUPPORTED_UI_LANGUAGES = [
  { code: 'en-IN', label: 'English' },
  { code: 'hi-IN', label: 'हिन्दी' },
  { code: 'ta-IN', label: 'தமிழ்' },
  { code: 'te-IN', label: 'తెలుగు' },
  { code: 'kn-IN', label: 'ಕನ್ನಡ' },
  { code: 'ml-IN', label: 'മലയാളം' },
  { code: 'mr-IN', label: 'मराठी' },
  { code: 'bn-IN', label: 'বাংলা' },
  { code: 'gu-IN', label: 'ગુજરાતી' },
  { code: 'pa-IN', label: 'ਪੰਜਾਬੀ' },
  { code: 'od-IN', label: 'ଓଡ଼ିଆ' },
]

const DICTIONARIES = {
  'en-IN': en,
  'hi-IN': hi,
  'ta-IN': ta,
  'te-IN': te,
  'kn-IN': kn,
  'ml-IN': ml,
  'mr-IN': mr,
  'bn-IN': bn,
  'gu-IN': gu,
  'pa-IN': pa,
  'od-IN': od,
}

function lookup(dict, key) {
  return key.split('.').reduce((node, part) => (node && typeof node === 'object' ? node[part] : undefined), dict)
}

function interpolate(str, vars) {
  if (!vars) return str
  return str.replace(/\{\{(\w+)\}\}/g, (match, name) => (name in vars ? String(vars[name]) : match))
}

export function LanguageProvider({ children }) {
  const [lang, setLangState] = useState(() => {
    try {
      return localStorage.getItem(KEY) || DEFAULT_LANG
    } catch {
      return DEFAULT_LANG
    }
  })

  const setLang = (code) => {
    setLangState(code)
    try {
      localStorage.setItem(KEY, code)
    } catch {
      // localStorage unavailable (private mode etc.) -- preference just won't persist
    }
  }

  const t = useMemo(() => {
    const dict = DICTIONARIES[lang] || en
    return (key, vars) => {
      const value = lookup(dict, key) ?? lookup(en, key) ?? key
      return interpolate(value, vars)
    }
  }, [lang])

  return (
    <LanguageContext.Provider value={{ lang, setLang, t }}>
      {children}
    </LanguageContext.Provider>
  )
}

export const useLanguage = () => useContext(LanguageContext)
