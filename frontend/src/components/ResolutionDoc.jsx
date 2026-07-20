import { Logo, Download, Check } from '../icons.jsx'
import { useLanguage } from '../i18n/LanguageContext.jsx'

export default function ResolutionDoc({ doc }) {
  const { t } = useLanguage()

  function download() {
    const lines = []
    lines.push(doc.header)
    lines.push(doc.subheader)
    lines.push('='.repeat(60))
    lines.push(`${t('resolutionDoc.case')} ${doc.case_id}`)
    lines.push(`${t('resolutionDoc.date')} ${doc.date}`)
    lines.push(`${t('resolutionDoc.claimant')} ${doc.parties.claimant}`)
    lines.push(`${t('resolutionDoc.respondent')} ${doc.parties.respondent}`)
    lines.push(`${t('resolutionDoc.txtBasisPrefix')} ${doc.basis}`)
    lines.push('')
    lines.push(t('resolutionDoc.txtFindings'))
    doc.findings.forEach((f, i) => lines.push(`  ${i + 1}. ${f}`))
    lines.push('')
    lines.push(t('resolutionDoc.txtCitedPrecedents'))
    doc.cited_precedents.forEach((c) => {
      lines.push(`  - ${c.citation}`)
      lines.push(`    ${c.principle}`)
    })
    lines.push('')
    lines.push(t('resolutionDoc.txtOrder'))
    doc.order.forEach((o, i) => lines.push(`  ${i + 1}. ${o}`))
    lines.push('')
    lines.push(`${t('resolutionDoc.txtComplianceDeadline')} ${doc.compliance_deadline} (${t('resolve.side.complianceDays', { n: doc.compliance_days })})`)
    lines.push('')
    lines.push(doc.footer)
    const blob = new Blob([lines.join('\n')], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${doc.case_id}_resolution.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="fade-in">
      <div className="doc">
        <div className="doc-head">
          <div className="seal">
            <span className="s-logo">
              <Logo width={22} height={22} />
            </span>
            <div>
              <h3>{doc.header}</h3>
              <div className="sub2">{doc.subheader}</div>
            </div>
          </div>
        </div>
        <div className="doc-body">
          <div className="doc-meta">
            <span><strong>{t('resolutionDoc.case')}</strong> {doc.case_id}</span>
            <span><strong>{t('resolutionDoc.date')}</strong> {doc.date}</span>
            <span><strong>{t('resolutionDoc.claimant')}</strong> {doc.parties.claimant}</span>
            <span><strong>{t('resolutionDoc.respondent')}</strong> {doc.parties.respondent}</span>
          </div>

          <div className="doc-section">
            <h4>{t('resolutionDoc.basisTitle')}</h4>
            <p>{t('resolutionDoc.basisText', { basis: doc.basis, amount: doc.claim_amount_display })}</p>
          </div>

          <div className="doc-section">
            <h4>{t('resolutionDoc.findingsTitle')}</h4>
            <ol>
              {doc.findings.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ol>
          </div>

          <div className="doc-section">
            <h4>{t('resolutionDoc.precedentsTitle')}</h4>
            {doc.cited_precedents.map((c, i) => (
              <div className="doc-cite" key={i}>
                <div className="cc">{c.citation}</div>
                <div className="cp">{c.principle}</div>
              </div>
            ))}
          </div>

          <div className="doc-section">
            <h4>{t('resolutionDoc.orderTitle')}</h4>
            <div className="doc-order">
              <ol>
                {doc.order.map((o, i) => (
                  <li key={i}>{o}</li>
                ))}
              </ol>
            </div>
          </div>

          <div className="doc-footer">{doc.footer}</div>
        </div>
      </div>

      <div className="flex gap mt">
        <button className="btn btn-primary" onClick={download}>
          <Download width={16} height={16} /> {t('resolutionDoc.download')}
        </button>
        <span className="resolved-badge">
          <Check width={16} height={16} /> {t('resolutionDoc.binding')} · {doc.via_mediation ? t('resolutionDoc.byMediation') : t('resolutionDoc.autonomous')}
        </span>
      </div>
    </div>
  )
}
