import { Scales, Download, Check } from '../icons.jsx'

export default function ResolutionDoc({ doc }) {
  function download() {
    const lines = []
    lines.push(doc.header)
    lines.push(doc.subheader)
    lines.push('='.repeat(60))
    lines.push(`Case ID: ${doc.case_id}`)
    lines.push(`Date: ${doc.date}`)
    lines.push(`Claimant: ${doc.parties.claimant}`)
    lines.push(`Respondent: ${doc.parties.respondent}`)
    lines.push(`Basis: Resolution ${doc.basis}`)
    lines.push('')
    lines.push('FINDINGS')
    doc.findings.forEach((f, i) => lines.push(`  ${i + 1}. ${f}`))
    lines.push('')
    lines.push('CITED PRECEDENTS')
    doc.cited_precedents.forEach((c) => {
      lines.push(`  - ${c.citation}`)
      lines.push(`    ${c.principle}`)
    })
    lines.push('')
    lines.push('ORDER')
    doc.order.forEach((o, i) => lines.push(`  ${i + 1}. ${o}`))
    lines.push('')
    lines.push(`Compliance deadline: ${doc.compliance_deadline} (${doc.compliance_days} days)`)
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
              <Scales width={22} height={22} />
            </span>
            <div>
              <h3>{doc.header}</h3>
              <div className="sub2">{doc.subheader}</div>
            </div>
          </div>
        </div>
        <div className="doc-body">
          <div className="doc-meta">
            <span><strong>Case:</strong> {doc.case_id}</span>
            <span><strong>Date:</strong> {doc.date}</span>
            <span><strong>Claimant:</strong> {doc.parties.claimant}</span>
            <span><strong>Respondent:</strong> {doc.parties.respondent}</span>
          </div>

          <div className="doc-section">
            <h4>Basis of Resolution</h4>
            <p>This order is passed {doc.basis}, in the matter concerning a claim of {doc.claim_amount_display}.</p>
          </div>

          <div className="doc-section">
            <h4>Findings</h4>
            <ol>
              {doc.findings.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ol>
          </div>

          <div className="doc-section">
            <h4>Precedents Relied Upon</h4>
            {doc.cited_precedents.map((c, i) => (
              <div className="doc-cite" key={i}>
                <div className="cc">{c.citation}</div>
                <div className="cp">{c.principle}</div>
              </div>
            ))}
          </div>

          <div className="doc-section">
            <h4>Operative Order</h4>
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
          <Download width={16} height={16} /> Download resolution order
        </button>
        <span className="resolved-badge">
          <Check width={16} height={16} /> Binding · {doc.via_mediation ? 'by mediation' : 'autonomous'}
        </span>
      </div>
    </div>
  )
}
