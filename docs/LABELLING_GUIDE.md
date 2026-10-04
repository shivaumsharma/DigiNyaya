# Labelling guide — checking the DigiNyaya judge by hand

Thank you for helping. This takes roughly 8–12 hours for ~150 cases; you can stop and resume.
You do **not** need to be a lawyer. You need to read carefully and answer three yes/no questions per row.

## What this is for

DigiNyaya's AI drafts a decision for a dispute. We score it against what a real Indian court decided,
using an LLM as the scorer (the "judge"). Before anyone trusts those scores, we need to know whether the
judge reads the two decisions the way a careful person does. **Your answers are the yardstick. The judge's
answers are hidden from you on purpose — keep it that way.**

## Rules that keep the result honest

1. **Do not open `human_label_key.json`** or any judge output before you finish. Do not ask the project
   owner what the judge said.
2. **Work alone.** If a second person labels the same sheet, do not compare notes until both are done.
   Your disagreements are data, not mistakes.
3. **Do not use an AI tool to help you read or label.** That would measure the judge against another
   model, not against a person.
4. **Label what is written, not what you think is fair.** You are not grading whether the AI or the court
   was right — only what each decided.
5. **Unsure? Write `?` and move on.** A blank or `?` row is dropped from scoring and counted; a guess is
   not. Aim for fewer than ~10% unsure. Add a short reason in the optional `notes` column if you like.
6. Write your name and the time you start/finish each session somewhere. We report time per case.

## Easiest way: the terminal labeller

From `backend/` (PowerShell or any shell):

```
python -m scripts.label_cases --name yourname
```

It shows one case at a time, asks the three questions, and saves after every answer. Quit with `q` (or
Ctrl+C) and run the same command to resume where you stopped. `b` redoes a case, `h` shows the definitions.
It writes `data_cache/labels_yourname.csv` in the format the agreement script reads and never opens the
judge's answer key. Everything below describes the questions; the spreadsheet route works too.

## The sheet

Alternatively, open `data_cache/human_label_sheet.csv` in a spreadsheet (UTF-8). Columns you read:

| Column | Meaning |
| --- | --- |
| `facts` | The dispute as filed |
| `REAL_COURT_DECIDED` | What the real court ordered |
| `AI_DECIDED_relief_type` / `AI_DECIDED_amount` / `AI_DECIDED_order` | What the AI decided |

Columns you fill in (use `Y`, `N`, or `?`):

| Column | Question |
| --- | --- |
| `claimant_prevailed_ai` | Under the **AI's** decision, did the claimant get real relief? |
| `claimant_prevailed_real` | Under the **real court's** decision, did the **same** claimant get real relief? |
| `relief_similar` | **Only if both answers above are `Y`:** is the relief comparable? Otherwise leave blank. |

### Who is "the claimant"?

The party who **brought the claim** — the plaintiff / petitioner / complainant in the real judgment — even if
it is a bank or company and not an individual consumer. Map roles by *who sued whom*, not by which side
sounds like it is asking for a refund. Use that same party for both questions.

### Question 1 and 2: "real relief"

`Y` if the claimant gets something substantive: money, an injunction, possession, replacement, a
declaration in their favour. A **smaller or different** award than asked for still counts as `Y`.

`N` if the respondent wins, the claim is dismissed, or the claimant gets nothing of substance.

Answer each question **independently**. Do not let the amount, or the AI/court disagreeing, change either answer.

**Ignore the AI order's standard boilerplate.** Every AI order says it is a "recommendation", "provisional",
"non-binding", or "pending human counter-signature". That is fixed platform wording on every case and
says nothing about who won. Judge only the substance: who gets what.

### Question 3: "comparable relief"

Only when both are `Y`. `Y` if the relief is **the same type** (money, injunction, replacement…) **and**, for
money, **the AI's amount is within about 20% of the court's**. Compare against the court's main award
(the principal or decreed sum), not an incidental amount such as costs. A non-monetary order can be `Y` when
the court also ordered something non-monetary of the same kind, even if an incidental ₹ figure differs.
`N` if the type differs (court: refund; AI: injunction) or the money is clearly further off than 20%.

## Worked examples (invented for illustration — none are rows from the sheet)

| Court decided | AI decided | Q1 AI | Q2 real | Q3 | Why |
| --- | --- | --- | --- | --- | --- |
| Plaintiff shop awarded ₹2,00,000 for unpaid invoices | Pay ₹1,90,000 | Y | Y | Y | Both for plaintiff; 5% apart |
| Plaintiff shop awarded ₹2,00,000 | Pay ₹60,000 | Y | Y | N | Both for plaintiff; amounts far apart |
| Complaint dismissed, no deficiency of service | Refund ₹15,000 to complainant | Y | N | blank | Opposite sides won; Q3 not asked |
| Plaintiff awarded ₹80,000; court also awards ₹5,000 costs | Pay ₹82,000 | Y | Y | Y | Compare to ₹80,000, not ₹85,000 |
| Bank (plaintiff) awarded ₹4,50,000 recovery | Dismiss, respondent's case stronger | N | Y | blank | Claimant is the bank, not the borrower |
| Suit decreed for permanent injunction, ₹0 damages | Injunction granted, ₹0 | Y | Y | Y | Same non-monetary relief |
| Suit decreed for ₹3,00,000 | Injunction against respondent | Y | Y | N | Both for claimant but relief type differs |
| Court: "disposed of as withdrawn / settled out of court" | Refund ₹10,000 | Y | ? | blank | No substantive decision on the merits — use `?` |

## Edge cases

- **Counterclaim / both sides partly succeed:** answer from the claimant's side — did *they* get something
  substantive? If the claim fails and only the counterclaim succeeds, that is `N`.
- **Real judgment is procedural** (remand, limitation, jurisdiction, adjournment) with no outcome on relief: `?`.
- **Real judgment unclear about who the claimant is:** `?`, and note why.
- **Interest and costs:** ignore for the 20% comparison unless the decree is *only* those.
- **Real court awards several sums:** compare to the principal/main award; if the AI matches any
  clearly-main component, `Y`.
- **You genuinely can't tell after one careful read:** `?`. Do not agonise.

## When you are finished

Save as CSV (UTF-8), keep the same column order, and send it back named `labels_<yourname>.csv`. The project
owner then runs:

```
python -m scripts.judge_human_agreement --labels labels_<name1>.csv labels_<name2>.csv --names <name1> <name2>
```

Your answers are used only for this agreement check and may be acknowledged in any resulting write-up if
you wish; tell the project owner whether you do.
