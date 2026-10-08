"""Label the blind hand-label sheet one case at a time in the terminal.

Reads data_cache/human_label_sheet.csv (built by scripts/build_human_label_sheet.py) and writes
data_cache/labels_<name>.csv in exactly the format scripts/judge_human_agreement.py reads. It never
opens the judge's answer key, so the labels stay blind. Read docs/LABELLING_GUIDE.md first.

Every answer is saved immediately, so you can quit (q or Ctrl+C) at any point and re-run the same
command to resume at the first unlabelled case.

Per case you answer, with y / n / ? (? = unsure; that case is dropped from scoring and counted):
  1. Under the AI's decision, did the claimant get real relief?
  2. Under the real court's decision, did the SAME claimant get real relief?
  3. (only if both were y) Is the relief comparable in type and, for money, within about 20%?
Any question also accepts:
  b   undo: start this case again (at the first question: go back to the previous case)
  q   save and quit
  h   show the definitions again

When you answer ?, you are asked for a one-line reason (Enter to skip); it goes in the `notes` column.
The time spent per case is stored in `seconds` (capped at 15 minutes a case so a break doesn't count).

Run (from backend/):  python -m scripts.label_cases --name you
  --goto N   jump to case number N (1-based) instead of the first unlabelled one
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import textwrap
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data_cache"
ANSWER_COLS = ("claimant_prevailed_ai", "claimant_prevailed_real", "relief_similar")
EXTRA_COLS = ("notes", "seconds")
MAX_SECONDS_PER_CASE = 900

HELP = """
  claimant  = the party who BROUGHT the claim (plaintiff/petitioner/complainant), even if it is a bank.
  Q1/Q2     = y if that decision gives the claimant real relief (money, injunction, possession, a declaration
              in their favour; a smaller award still counts). n if the respondent wins, the claim is dismissed,
              or the claimant gets nothing of substance. Answer each one independently of the other.
  Ignore    = the AI order's "recommendation / provisional / non-binding / pending counter-signature" wording:
              it is on every case and says nothing about who won.
  Q3        = only when both were y. y if same relief type AND (for money) within ~20% of the court's main
              award (not costs/interest). n if the type differs or the money is clearly further apart.
  ?         = you genuinely cannot tell after one careful read (withdrawn/settled, procedural, unclear parties).
  b = redo this case (at Q1: previous case)   q = save and quit   h = this help
"""

_YES, _NO, _UNSURE = {"y", "yes"}, {"n", "no"}, {"?"}


def _norm(raw: str) -> str | None:
    raw = raw.strip().lower()
    if raw in _YES:
        return "y"
    if raw in _NO:
        return "n"
    if raw in _UNSURE:
        return "?"
    if raw in ("b", "back"):
        return "b"
    if raw in ("q", "quit"):
        return "q"
    if raw in ("h", "help"):
        return "h"
    return None


def read_sheet(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    missing = [c for c in ("case_id", "facts", "REAL_COURT_DECIDED", *ANSWER_COLS) if c not in fields]
    if missing:
        raise SystemExit(f"{path} is missing expected column(s): {', '.join(missing)}")
    return fields, rows


def load_progress(out_path: Path) -> dict[str, dict]:
    if not out_path.exists():
        return {}
    with open(out_path, encoding="utf-8-sig", newline="") as f:
        return {r["case_id"]: r for r in csv.DictReader(f)}


def is_done(row: dict) -> bool:
    return bool((row.get("claimant_prevailed_ai") or "").strip() and (row.get("claimant_prevailed_real") or "").strip())


def save(out_path: Path, fields: list[str], rows: list[dict]) -> None:
    """Atomic write, so quitting or crashing mid-save can never corrupt the labels file."""
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    cols = list(fields) + [c for c in EXTRA_COLS if c not in fields]
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out_path)


def show_case(row: dict, i: int, n: int, done: int, unsure: int, width: int, out=print) -> None:
    def block(label: str, text: str) -> None:
        out(f"\n{label}")
        for para in (text or "(empty)").splitlines() or [""]:
            out(textwrap.fill(para, width=width, initial_indent="  ", subsequent_indent="  ") or "")

    out("\n" + "=" * width)
    out(f"CASE {i + 1} of {n}   ({row['case_id']})   labelled so far: {done}, unsure: {unsure}")
    out("=" * width)
    block("FACTS", row["facts"])
    block("REAL COURT DECIDED", row["REAL_COURT_DECIDED"])
    ai_bits = [row.get("AI_DECIDED_relief_type"), row.get("AI_DECIDED_amount")]
    block("AI DECIDED", f"Relief type: {ai_bits[0]}   Amount: {ai_bits[1]}\n{row.get('AI_DECIDED_order', '')}")
    out("\n" + "-" * width)


def ask(prompt: str, input_fn, out=print) -> str:
    while True:
        r = _norm(input_fn(prompt))
        if r == "h":
            out(HELP)
            continue
        if r is None:
            out("  Please answer y, n, ?, b (redo), q (quit) or h (help).")
            continue
        return r


def run(sheet: Path, out_path: Path, goto: int | None = None, input_fn=input, out=print,
        width: int = 100, clock=time.monotonic) -> int:
    fields, sheet_rows = read_sheet(sheet)
    prior = load_progress(out_path)
    rows = []
    for r in sheet_rows:
        merged = dict(r)
        merged.update({k: v for k, v in prior.get(r["case_id"], {}).items() if k in (*ANSWER_COLS, *EXTRA_COLS)})
        for c in (*ANSWER_COLS, *EXTRA_COLS):
            merged.setdefault(c, "")
        rows.append(merged)
    n = len(rows)
    if not n:
        out("The sheet has no rows.")
        return 1

    i = next((k for k, r in enumerate(rows) if not is_done(r)), n)
    if goto is not None:
        i = min(max(goto - 1, 0), n - 1)
    if i >= n:
        out(f"All {n} cases are already labelled in {out_path}. Use --goto N to revisit one.")
        return 0
    out(f"{sum(is_done(r) for r in rows)} of {n} already labelled. Answer y / n / ? ; b = redo, q = quit, h = help.")

    try:
        while 0 <= i < n:
            row = rows[i]
            done = sum(is_done(r) for r in rows)
            unsure = sum(1 for r in rows if "?" in (r["claimant_prevailed_ai"], r["claimant_prevailed_real"]))
            show_case(row, i, n, done, unsure, width, out)
            started = clock()

            a1 = ask("1. AI decision: did the claimant get real relief? [y/n/?] > ", input_fn, out)
            if a1 == "q":
                break
            if a1 == "b":
                i = max(i - 1, 0)
                continue
            a2 = ask("2. REAL court: did the SAME claimant get real relief? [y/n/?] > ", input_fn, out)
            if a2 == "q":
                break
            if a2 == "b":
                continue
            a3 = ""
            if a1 == "y" and a2 == "y":
                a3 = ask("3. Is the relief comparable (same type; money within ~20%)? [y/n/?] > ", input_fn, out)
                if a3 == "q":
                    break
                if a3 == "b":
                    continue

            note = ""
            if "?" in (a1, a2, a3):
                note = input_fn("   Reason for ? (Enter to skip) > ").strip()
            spent = int(min(clock() - started, MAX_SECONDS_PER_CASE))
            row.update({"claimant_prevailed_ai": a1, "claimant_prevailed_real": a2,
                        "relief_similar": a3 if a3 != "?" else "?", "notes": note,
                        "seconds": str(int(float(row.get("seconds") or 0)) + spent)})
            save(out_path, fields, rows)
            i += 1
    except (KeyboardInterrupt, EOFError):
        out("\nInterrupted.")

    save(out_path, fields, rows)
    done = sum(is_done(r) for r in rows)
    unsure = sum(1 for r in rows if "?" in (r["claimant_prevailed_ai"], r["claimant_prevailed_real"]))
    minutes = sum(int(float(r.get("seconds") or 0)) for r in rows) / 60
    out(f"\nSaved {out_path}\n{done} of {n} cases labelled ({unsure} unsure), about {minutes:.0f} minutes of labelling time recorded.")
    if done == n:
        out("All done. Send this file back, or run scripts.judge_human_agreement on it (see docs/LABELLING_GUIDE.md).")
    else:
        out("Re-run the same command to resume where you stopped.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="you", help="your name; labels go to data_cache/labels_<name>.csv")
    ap.add_argument("--sheet", default=str(DATA / "human_label_sheet.csv"))
    ap.add_argument("--out", help="override the output path")
    ap.add_argument("--goto", type=int, help="jump to this case number (1-based)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sheet = Path(args.sheet)
    if not sheet.exists():
        print(f"Not found: {sheet}")
        return 1
    safe = "".join(c for c in args.name if c.isalnum() or c in "-_") or "you"
    out_path = Path(args.out) if args.out else DATA / f"labels_{safe}.csv"
    width = max(60, min(shutil.get_terminal_size((100, 20)).columns - 2, 110))
    return run(sheet, out_path, goto=args.goto, width=width)


if __name__ == "__main__":
    raise SystemExit(main())
