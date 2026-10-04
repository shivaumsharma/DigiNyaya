"""Score the LLM judge against one or more humans' hand labels.

Reads the CSV(s) filled in from scripts/build_human_label_sheet.py and the hidden
key of the judge's own answers, and reports -- for each of the judge's three
questions and for the derived verdict (match / partial / mismatch) -- raw
agreement and Cohen's kappa (chance-corrected: raw agreement flatters a judge
when one answer, e.g. "claimant prevailed = Y", is common), each with a 95%
bootstrap confidence interval.

With two or more label files it also reports human-vs-human agreement (the
ceiling: where two people disagree, the judge cannot be expected to do better)
and judge agreement on the rows the humans agreed on.

relief_similar is only scored on rows where the human marked both
claimant_prevailed_* as Y (that is the only case the judge's verdict uses it).

A row is skipped (and counted as "unsure") when the labeller left a required
answer blank or wrote "?". A row with no derivable verdict is excluded from the
derived-verdict comparison only, never silently treated as "partial": that is
both-Y with relief_similar blank, and both-N (the claimant lost under both
decisions -- humans are not asked about relief there, so the judge's similarity
call has nothing to be compared with). Both-N rows still count for the two
prevailed questions and their number is printed.

The sheet is stratified by the JUDGE's verdict (an even split of match /
partial / mismatch), so overall agreement is NOT an estimate of the judge's
population-wide accuracy. The per-judge-verdict breakdown at the end is the
number that is valid under that sampling: "of the cases the judge called X,
what fraction does a human also call X".

Run (from backend/):
  python -m scripts.judge_human_agreement --labels data_cache/human_label_sheet.csv
  python -m scripts.judge_human_agreement --labels a.csv b.csv --names alice bob \\
      --disagreements data_cache/judge_disagreements.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data_cache"
VERDICTS = ("match", "partial", "mismatch")
BOOTSTRAP_RESAMPLES = 2000
SMALL_SAMPLE_WARNING = 100


def _yn(v: str):
    v = (v or "").strip().lower()
    if v in ("y", "yes", "true", "1"):
        return True
    if v in ("n", "no", "false", "0"):
        return False
    return None


def kappa(pairs: list[tuple]) -> float | None:
    """Cohen's kappa for two raters over any categorical labels. None when there
    are no pairs or chance agreement is already 1 (kappa is undefined then)."""
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum((ca[c] / n) * (cb[c] / n) for c in set(ca) | set(cb))
    return (po - pe) / (1 - pe) if pe < 1 else None


def agreement(pairs: list[tuple]) -> float | None:
    return sum(a == b for a, b in pairs) / len(pairs) if pairs else None


def bootstrap_ci(pairs: list[tuple], stat, seed: int = 7, resamples: int = BOOTSTRAP_RESAMPLES):
    """Percentile 95% CI of stat(pairs), resampling rows with replacement. Seeded so a
    re-run on the same labels prints the same interval. Resamples where the statistic is
    undefined are dropped; returns None if fewer than half remain."""
    if len(pairs) < 2:
        return None
    rng = random.Random(seed)
    n = len(pairs)
    vals = []
    for _ in range(resamples):
        s = stat([pairs[rng.randrange(n)] for _ in range(n)])
        if s is not None:
            vals.append(s)
    if len(vals) < resamples / 2:
        return None
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[min(int(0.975 * len(vals)), len(vals) - 1)]


def verdict_of(ai: bool, real: bool, similar: bool | None) -> str | None:
    """None when the verdict cannot be derived: both prevailed but similarity unanswered, or
    both lost (humans are not asked relief_similar there, while the judge still makes a
    similarity call, so the two derived verdicts are not comparable -- those rows are
    counted and reported, and still scored on the two prevailed questions)."""
    if ai != real:
        return "mismatch"
    if not ai or similar is None:
        return None
    return "match" if similar else "partial"


def read_labels(path: str) -> dict[str, dict]:
    """case_id -> {ai, real, sim, verdict} with None for any answer left blank/unsure."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out: dict[str, dict] = {}
    for r in rows:
        ai, real = _yn(r.get("claimant_prevailed_ai")), _yn(r.get("claimant_prevailed_real"))
        sim = _yn(r.get("relief_similar")) if (ai and real) else None
        out[r["case_id"]] = {
            "ai": ai, "real": real, "sim": sim,
            "verdict": verdict_of(ai, real, sim) if ai is not None and real is not None else None,
        }
    return out


def judge_answers(key: dict, case_id: str) -> dict | None:
    k = key.get(case_id)
    if not k:
        return None
    return {"ai": k["claimant_prevailed_ai"], "real": k["claimant_prevailed_real"],
            "sim": k["relief_similar"], "verdict": k["verdict"]}


def question_pairs(a: dict[str, dict], b: dict[str, dict]) -> dict[str, list[tuple]]:
    """Paired answers per question for the case_ids both sides answered. `a` and `b` map
    case_id -> {ai, real, sim, verdict}; any None answer drops that row from that question."""
    pairs: dict[str, list[tuple]] = {"ai": [], "real": [], "sim": [], "verdict": []}
    for cid in sorted(set(a) & set(b)):
        x, y = a[cid], b[cid]
        for q in ("ai", "real"):
            if x[q] is not None and y[q] is not None:
                pairs[q].append((x[q], y[q]))
        # relief_similar only counts where BOTH sides saw both prevailed answers as Y
        if x["ai"] and x["real"] and y["ai"] and y["real"] and x["sim"] is not None and y["sim"] is not None:
            pairs["sim"].append((x["sim"], y["sim"]))
        if x["verdict"] is not None and y["verdict"] is not None:
            pairs["verdict"].append((x["verdict"], y["verdict"]))
    return pairs


_QUESTIONS = [("ai", "Did the claimant win under the AI decision"),
              ("real", "Did the claimant win under the real decision"),
              ("sim", "Is the relief comparable (both won only)"),
              ("verdict", "Derived verdict (match/partial/mismatch)")]


def _fmt_ci(ci) -> str:
    return f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "      n/a     "


def report(title: str, pairs: dict[str, list[tuple]]) -> None:
    print(f"\n== {title}")
    print(f"{'question':44s} {'n':>4s} {'agree%':>7s} {'95% CI':>14s} {'kappa':>7s} {'95% CI':>14s}")
    for q, label in _QUESTIONS:
        p = pairs[q]
        ag, kp = agreement(p), kappa(p)
        ag_ci = bootstrap_ci(p, agreement)
        kp_ci = bootstrap_ci(p, kappa)
        print(f"{label:44s} {len(p):4d} "
              f"{(100 * ag if ag is not None else float('nan')):6.1f}% {_fmt_ci(ag_ci):>14s} "
              f"{(kp if kp is not None else float('nan')):7.2f} {_fmt_ci(kp_ci):>14s}")


def confusion(title: str, pairs: list[tuple[str, str]], row_name: str, col_name: str) -> None:
    """3x3 table of (row, col) verdicts; the % is of each ROW, i.e. 'of the cases `row_name`
    called X, what fraction `col_name` also called Y'."""
    print(f"\n== {title}  (rows: {row_name}, columns: {col_name}; % of row)")
    print(f"{'':>10s} " + " ".join(f"{v:>14s}" for v in VERDICTS))
    counts = Counter(pairs)
    for r in VERDICTS:
        tot = sum(counts[(r, c)] for c in VERDICTS)
        cells = " ".join(f"{counts[(r, c)]:>5d} ({100 * counts[(r, c)] / tot if tot else 0:4.0f}%)  " for c in VERDICTS)
        print(f"{r:>10s} {cells}")


def write_disagreements(path: str, sheets: dict[str, list[dict]], names: list[str], labels: dict, key: dict) -> int:
    """Rows where any labeller's verdict differs from the judge's, with the sheet's own text
    next to both answers -- the input to error analysis. Written only AFTER labelling is
    finished, because it exposes the judge's answers."""
    first = names[0]
    out = []
    for r in sheets[first]:
        cid = r["case_id"]
        j = judge_answers(key, cid)
        if not j:
            continue
        humans = {n: labels[n].get(cid, {}).get("verdict") for n in names}
        if all(v is None or v == j["verdict"] for v in humans.values()):
            continue
        row = {"case_id": cid, "judge_verdict": j["verdict"],
               **{f"{n}_verdict": humans[n] for n in names},
               "facts": r.get("facts", ""), "REAL_COURT_DECIDED": r.get("REAL_COURT_DECIDED", ""),
               "AI_DECIDED_order": r.get("AI_DECIDED_order", "")}
        out.append(row)
    if out:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
    return len(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True, nargs="+", help="one filled CSV per labeller")
    ap.add_argument("--names", nargs="+", help="labeller names, same order as --labels (default: labeller1, ...)")
    ap.add_argument("--key", default=str(DATA / "human_label_key.json"))
    ap.add_argument("--disagreements", help="write the rows where a human differs from the judge to this CSV")
    args = ap.parse_args()

    names = args.names or [f"labeller{i + 1}" for i in range(len(args.labels))]
    if len(names) != len(args.labels):
        ap.error("--names must have the same number of entries as --labels")
    if len(set(names)) != len(names):
        ap.error("--names must be unique")

    key = json.loads(Path(args.key).read_text(encoding="utf-8"))
    labels = {n: read_labels(p) for n, p in zip(names, args.labels)}
    judge = {cid: judge_answers(key, cid) for cid in key}

    for n in names:
        unknown = [c for c in labels[n] if c not in key]
        unsure = [c for c, v in labels[n].items() if c in key and (v["ai"] is None or v["real"] is None)]
        both_lost = [c for c, v in labels[n].items() if c in key and v["ai"] is False and v["real"] is False]
        no_sim = [c for c, v in labels[n].items() if c in key and v["ai"] and v["real"] and v["sim"] is None]
        print(f"{n}: {len(labels[n]) - len(unknown)} sheet rows in key, {len(unsure)} unsure/blank "
              f"(excluded){f', {len(unknown)} case_ids not in key (ignored)' if unknown else ''}.")
        print(f"  {len(both_lost)} both-lost row(s) and {len(no_sim)} both-won row(s) with relief_similar blank: "
              "scored on the prevailed questions only, not on the derived verdict.")
        if len(labels[n]) - len(unsure) < SMALL_SAMPLE_WARNING:
            print(f"  WARNING: fewer than {SMALL_SAMPLE_WARNING} usable rows -- the intervals below will be wide.")

    for n in names:
        report(f"JUDGE vs {n}", question_pairs(labels[n], judge))

    if len(names) >= 2:
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                report(f"{names[i]} vs {names[j]} (human-human ceiling)", question_pairs(labels[names[i]], labels[names[j]]))
        # Judge vs the rows where every human agreed on the verdict
        agreed = {c: v for c, v in labels[names[0]].items()
                  if v["verdict"] is not None and all(labels[n].get(c, {}).get("verdict") == v["verdict"] for n in names[1:])}
        report(f"JUDGE vs rows ALL labellers agreed on ({len(agreed)})", question_pairs(agreed, judge))

    confusion_pairs = [(judge[c]["verdict"], v["verdict"]) for c, v in labels[names[0]].items()
                       if c in judge and judge[c] and v["verdict"] is not None]
    confusion(f"Judge verdict vs {names[0]}", confusion_pairs, "judge", names[0])
    print("\nThe sheet is split evenly across the judge's own verdicts, so overall agreement above is "
          "not the judge's population-wide accuracy; read each ROW of the table instead.")
    print("Rule of thumb: kappa >= 0.8 strong, 0.6-0.8 substantial, < 0.6 the judge is not reliable enough to lean on. "
          "If human-human kappa is itself below 0.6 the task is ambiguous -- fix the labelling guide first.")

    if args.disagreements:
        sheets = {}
        for n, p in zip(names, args.labels):
            with open(p, encoding="utf-8-sig", newline="") as f:
                sheets[n] = list(csv.DictReader(f))
        count = write_disagreements(args.disagreements, sheets, names, labels, key)
        print(f"\nWrote {count} judge/human disagreement row(s) -> {args.disagreements}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
