import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LanguageProvider } from '../i18n/LanguageContext.jsx'
import ResolutionDoc from './ResolutionDoc.jsx'

const baseDoc = {
  header: 'DIGINYAYA',
  subheader: 'Recommended Resolution',
  case_id: 'DN-1',
  date: '2026-10-05',
  parties: { claimant: 'A', respondent: 'B' },
  basis: 'by mutual consent',
  claim_amount_display: 'Rs. 5,000',
  findings: ['The claim is established.'],
  order: ['B shall pay A Rs. 5,000.'],
  cited_precedents: [{ citation: 'X v. Y (2020)', principle: 'A principle.' }],
  relief_amount: 5000,
  relief_amount_display: 'Rs. 5,000',
  compliance_days: 30,
  compliance_deadline: '2026-11-04',
  via_mediation: true,
  requires_human_signoff: false,
  footer: 'footer',
}

function renderDoc(doc) {
  return render(
    <LanguageProvider>
      <ResolutionDoc doc={doc} caseId="DN-1" lang="en-IN" />
    </LanguageProvider>,
  )
}

describe('ResolutionDoc statutory provisions', () => {
  it('shows nothing about statutes when the API returns none (the default)', () => {
    renderDoc(baseDoc)
    expect(screen.queryByText('Statutory Provisions Considered')).toBeNull()
    expect(screen.getByText('Precedents Relied Upon')).toBeTruthy()
  })

  it('renders the section when statutes are present', () => {
    renderDoc({ ...baseDoc, cited_statutes: [{ citation: 'Consumer Protection Act, 2019, section 39', principle: 'Lists the reliefs.' }] })
    expect(screen.getByText('Statutory Provisions Considered')).toBeTruthy()
    expect(screen.getByText('Consumer Protection Act, 2019, section 39')).toBeTruthy()
  })

  it('treats an empty list the same as absent', () => {
    renderDoc({ ...baseDoc, cited_statutes: [] })
    expect(screen.queryByText('Statutory Provisions Considered')).toBeNull()
  })
})
