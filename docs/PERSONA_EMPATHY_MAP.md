# Customer persona and empathy map (draft hypotheses)

**Status: hypotheses, not research.** No users have been interviewed. Everything marked *Data* comes from DigiNyaya's own
corpus of 23,623 real district-court cases; everything marked *Assumption* is a guess to test in interviews (section 5).
The personas are composites, not real people. Do not present them as findings.

## 1. What the data says about who has these disputes
| Fact (Data) | Source |
|---|---|
| 64.2% of court cases are small-claims debt recovery, 26.1% property / neighbour, 6.7% contract, 1.9% tenancy, 0.9% consumer | `scripts/tag_disposal.py` by category |
| 53.3% ended in an ex parte decree: the other side never appeared | `scripts/tag_disposal.py` |
| Almost all cases are from Andhra Pradesh and Telangana district courts | `scripts/district_supply_census.py` |
| Product scope: modest money disputes, unpaid invoice, refund, bounced cheque, in the filer's own language | README |

Limits: these are people whose cases reached a court. Those who gave up, settled informally or never filed are invisible, so the
data over-represents people with enough time, money or stubbornness to litigate.

## 2. Persona A: the owed-money small trader (primary claimant) [composite]
* **Who (Assumption):** runs a small shop or supplies goods on credit; roughly 35 to 55; comfortable on a phone, less so with
  forms and legal English; prefers Telugu / Hindi / their regional language; income irregular.
* **Situation (Data-informed):** a customer or business owes them a sum that is real money to them but small to a lawyer
  (the corpus is dominated by small-claims debt recovery). Informal reminders have failed.
* **Goal:** get paid, or a credible outcome, without losing weeks to a court and without paying a lawyer a large share of the sum.
* **Needs from the product:** plain-language intake, voice or local-language input, a clear idea of what they will get and how
  long it takes, a document the other side takes seriously.
* **Blockers (Assumption):** distrust of anything "AI" deciding money; fear the other side will ignore it; no idea what
  evidence is "enough"; shame or friction in taking a customer to court.

### Empathy map (Persona A)
| | |
|---|---|
| **Says** (Assumption) | "He will not pay unless there is a case." "Lawyers cost more than the money." "Will this actually be valid?" |
| **Thinks** | "If I file, will the customer stop buying from me?" "Is a machine really going to read my bills?" "What if I lose both the money and the time?" |
| **Does** | Calls and messages the debtor repeatedly; keeps bills and chat screenshots; asks a relative, agent or local lawyer what to do; delays filing. |
| **Feels** | Frustrated and disrespected by the non-payment; anxious about cost and delay; unsure the system is for people like them. |
| **Pains** | Cost versus claim size; years of delay; legal jargon and English; not knowing what counts as evidence; the other side ignoring everything. |
| **Gains** | A fast, cheap, understandable path; a written order in their language; an outcome both sides can accept; a human who signs off, so it carries weight. |

## 3. Persona B: the respondent who may never show up [composite]
Why this persona exists: in the data, 53% of decided cases were decided without the defendant appearing. If the same happens
here, half of the product's counterpart never engages, and the claimant's experience depends on it.
* **Who (Assumption):** a customer or supplier who owes money or disputes the amount; may be unreachable, may not read formal
  notices, may fear court, may think ignoring it makes it go away.
* **Goal (Assumption):** avoid the cost and stigma; pay less, or later, or not at all; some genuinely dispute the claim.
* **Empathy map:** *Says* "I already paid", "the goods were bad". *Thinks* "It is a message, not a court; I can ignore it."
  *Does* ignores the notice, calls the claimant, or goes silent. *Feels* defensive, scared, or indifferent.
  *Pains* legal pressure, unclear consequences. *Gains* a way to give their side cheaply, a face-saving settlement, more time.
* **Design implication to test:** a 72-hour response window and a clear "what happens if you do not reply" message.

## 4. Persona C: the human reviewer who counter-signs [composite]
* **Who (Assumption):** a lawyer or retired judicial officer who reviews AI-drafted orders on Tier 2 cases.
* **Needs:** a fast queue, the case reduced to the facts and evidence, the AI's reasoning and citations checkable in seconds, a
  tamper-evident record, and honest confidence signals. The current confidence score correlates only 0.19 with correctness, so
  it should not be presented as reliable.
* **Pains:** being the rubber stamp for a system they cannot audit; time pressure; liability.

## 5. How to validate (next step)
1. Interview 8 to 12 people: small traders or service providers who were owed money in the last 2 years (include some who did
   not file), plus 3 to 4 people who were on the other side, plus 2 or 3 lawyers or reviewers.
2. Ask about the last time it happened, not hypotheticals: what they did, who they asked, what it cost, how long it took, why
   they stopped or continued, whom they trusted.
3. Test five hypotheses and write down what would change the design:
   * H1 The claimant will not trust a purely AI decision (kept: human counter-signature on Tier 2).
   * H2 Local language and voice input matter more than speed.
   * H3 Respondents ignore digital notices; a different channel is needed.
   * H4 The real value is a document the other side takes seriously, not the AI analysis.
   * H5 People will not pay for this; a free, sponsored or per-case-fee model has to be decided.
4. Replace this document's assumptions with quotes and counts, and rewrite the personas from the interviews.

## 6. What synthetic users can and cannot do
Synthetic or simulated users (for example persona-prompted LLMs) can rehearse the interview guide and surface obvious
usability issues. They cannot tell you what real traders in Andhra Pradesh feel or do, and their answers should never be
reported as user research.
