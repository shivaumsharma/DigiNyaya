import { useLanguage } from '../../i18n/LanguageContext.jsx'

const NAV = [
  ['#problem', 'why'],
  ['#tiers', 'tiers'],
  ['#agents', 'agents'],
  ['#gate', 'safeguards'],
  ['#outcomes', 'outcomes'],
  ['#team', 'team'],
]

export default function NavLinks() {
  const { t } = useLanguage()
  return (
    <div className="dn-links">
      {NAV.map(([href, key]) => (
        <a key={href} href={href}>
          {t(`landing.nav.${key}`)}
        </a>
      ))}
    </div>
  )
}
