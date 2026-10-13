"""Plan a corpus expansion: how to draw N accepted cases from the downloadable supply without over-drawing from one
state, year or court.

Input is data_cache/district_supply.json from scripts/district_supply_census.py. The plan is only as good as the
ASSUMED YIELD (the share of attempted cases the LLM filter accepts as genuine two-party civil merits decisions). The
yield is unmeasured until a pilot has run, so run the pilot first, read the yield off its summary, and re-plan:

    python -m scripts.plan_corpus_scale --target 60000 --pilot 150      # prints the pilot commands only
    (run them, compute accepted / attempted)
    python -m scripts.plan_corpus_scale --target 60000 --assumed-yield 0.20

What the plan does:
  1. Estimates each state-year's capacity = sum over its complexes of min(per-complex cap, candidates x yield).
  2. Splits the target across STATES (no state above --max-state-share), then across YEARS within each state (no year
     above --max-year-share of its state). Proportional to capacity, capped, with the excess redistributed.
  3. For each state-year, works out how many court complexes must be tried and how much archive data that is.
  4. Prints one sourcing command per state-year. Each writes its own file under data_cache/dc_slices/, so slices can
     run in parallel and none touches the canonical eval_judgments.json. scripts/corpus_gate.py then checks and merges.

It reports a SHORTFALL rather than quietly breaking a cap: with the free data, nearly all supply is Andhra Pradesh and
Telangana, so a low --max-state-share may not be reachable.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
JUDGE_HOURS_PER_CASE = 4.0 / 23_841  # the "~4 hours" figure in build_eval_sample.py's docstring; an extrapolation


def allocate(capacity: dict, total: int, cap_share: float) -> dict:
    """Split `total` across keys proportionally to `capacity`, never giving a key more than its capacity or
    cap_share x total, redistributing the excess. Returns ints; the sum can fall short of `total` when the caps
    make it infeasible (the caller reports the shortfall)."""
    cap_each = cap_share * total
    room = {k: min(float(v), cap_each) for k, v in capacity.items() if v > 0}
    got = {k: 0.0 for k in room}
    remaining = float(total)
    active = set(room)
    while remaining > 1e-6 and active:
        weight = sum(capacity[k] for k in active)
        gave = 0.0
        for k in list(active):
            give = min(remaining * capacity[k] / weight, room[k] - got[k])
            got[k] += give
            gave += give
            if room[k] - got[k] <= 1e-9:
                active.discard(k)
        remaining -= gave
        if gave <= 1e-9:
            break
    return {k: int(math.floor(v + 1e-9)) for k, v in got.items()}


def expected_accepted(cands: int, yield_: float, per_complex_cap: int) -> float:
    return min(float(per_complex_cap), cands * yield_)


def complexes_needed(complex_cands: list[int], quota: int, yield_: float, per_complex_cap: int, safety: float = 1.3) -> int:
    """How many complexes (the script visits them largest-first) it takes to expect `quota` x safety accepted cases."""
    need, have, n = quota * safety, 0.0, 0
    for c in sorted(complex_cands, reverse=True):
        if have >= need:
            break
        have += expected_accepted(c, yield_, per_complex_cap)
        n += 1
    return n


def build_plan(groups: list[dict], target: int, assumed_yield: float, *, min_year: int = 2015, max_state_share: float = 0.65,
               max_year_share: float = 0.25, per_complex_cap: int = 25, states: set[str] | None = None) -> dict:
    usable = [g for g in groups if g.get("tar_bytes") and int(g["year"]) >= min_year and (not states or g["state"] in states)]
    by_sy: dict[tuple, list[dict]] = defaultdict(list)
    for g in usable:
        by_sy[(g["state"], g["year"])].append(g)

    cap_sy = {k: sum(expected_accepted(g["candidates"], assumed_yield, per_complex_cap) for g in v) for k, v in by_sy.items()}
    cap_state: dict[str, float] = defaultdict(float)
    for (s, _), c in cap_sy.items():
        cap_state[s] += c
    state_quota = allocate(dict(cap_state), target, max_state_share)

    rows = []
    for s, sq in state_quota.items():
        years = {y: c for (st, y), c in cap_sy.items() if st == s}
        year_quota = allocate(years, sq, max_year_share)
        for y, q in year_quota.items():
            if q <= 0:
                continue
            groups_sy = sorted(by_sy[(s, y)], key=lambda g: -g["candidates"])
            n = complexes_needed([g["candidates"] for g in groups_sy], q, assumed_yield, per_complex_cap)
            chosen = groups_sy[:n]
            rows.append({"state": s, "state_name": groups_sy[0]["state_name"], "year": y, "quota": q, "complexes": n,
                         "candidates_in_chosen": sum(g["candidates"] for g in chosen),
                         "archive_gb": sum(g["tar_bytes"] for g in chosen) / 1e9})
    rows.sort(key=lambda r: (r["state"], r["year"]))
    planned = sum(r["quota"] for r in rows)
    attempts = planned / assumed_yield if assumed_yield else 0
    return {
        "target": target, "planned": planned, "shortfall": target - planned, "assumed_yield": assumed_yield,
        "rows": rows, "per_complex_cap": per_complex_cap, "max_state_share": max_state_share,
        "max_year_share": max_year_share, "min_year": min_year,
        "archive_gb": sum(r["archive_gb"] for r in rows),
        "attempts_estimate": int(attempts),
        "judge_hours_estimate": planned * JUDGE_HOURS_PER_CASE,
        "state_totals": {s: sum(r["quota"] for r in rows if r["state"] == s) for s in {r["state"] for r in rows}},
    }


def command_for(row: dict, per_complex_cap: int, out_dir: str = r"data_cache\dc_slices",
                exclude_from: str = r"data_cache\eval_judgments.json") -> str:
    return (f"python -m scripts.source_district_court_judgments --states {row['state']} --years {row['year']} "
            f"--target {row['quota']} --max-complexes {row['complexes']} --max-per-complex {per_complex_cap} "
            f"--exclude-from {exclude_from} --out {out_dir}\\dc_{row['state']}_{row['year']}.json")


def pilot_commands(groups: list[dict], n: int, *, min_year: int = 2015, per_complex_cap: int = 25) -> list[str]:
    """Three slices from the largest downloadable state-years, so the measured yield is not one court's quirk."""
    usable = [g for g in groups if g.get("tar_bytes") and int(g["year"]) >= min_year]
    courts: dict[str, set] = defaultdict(set)
    for g in usable:
        courts[g["state"]].add((g["district"], g["complex"]))
    usable = [g for g in usable if len(courts[g["state"]]) >= 10]  # a one-court state says nothing about yield in general
    totals: dict[tuple, int] = defaultdict(int)
    for g in usable:
        totals[(g["state"], g["year"])] += g["candidates"]
    top = [k for k, _ in sorted(totals.items(), key=lambda kv: -kv[1])]
    picks: list[tuple] = []
    seen_states: set[str] = set()
    for k in top:  # prefer different states first
        if k[0] not in seen_states:
            picks.append(k)
            seen_states.add(k[0])
    picks += [k for k in top if k not in picks]
    picks = picks[:3]
    each = max(1, n // max(len(picks), 1))
    return [f"python -m scripts.source_district_court_judgments --states {s} --years {y} --target {each} "
            f"--max-complexes 40 --max-per-complex {per_complex_cap} --exclude-from data_cache\\eval_judgments.json "
            f"--out data_cache\\dc_slices\\pilot_{s}_{y}.json"
            for s, y in picks]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--supply", default=str(DATA / "district_supply.json"))
    ap.add_argument("--target", type=int, default=60_000, help="accepted cases wanted")
    ap.add_argument("--assumed-yield", type=float, default=0.20,
                    help="share of attempted cases the LLM filter accepts. UNMEASURED until a pilot has run")
    ap.add_argument("--min-year", type=int, default=2015)
    ap.add_argument("--max-state-share", type=float, default=0.65)
    ap.add_argument("--max-year-share", type=float, default=0.25, help="max share of a state's quota in one year")
    ap.add_argument("--per-complex-cap", type=int, default=150,
                    help="max accepted cases from one court complex. The sourcing script's own default of 25 is a guard "
                         "for tiny targets; at tens of thousands of cases it, not the data, becomes the ceiling. "
                         "scripts/corpus_gate.py separately fails any court above 2%% of the batch")
    ap.add_argument("--already-have", type=int, default=0, help="cases already in the corpus; the plan sources target - already-have")
    ap.add_argument("--states", default="", help="comma-separated state codes to restrict to")
    ap.add_argument("--pilot", type=int, default=0, help="print only pilot commands totalling about this many accepted-target cases")
    ap.add_argument("--out", default=str(DATA / "corpus_plan.json"))
    args = ap.parse_args()

    p = Path(args.supply)
    if not p.exists():
        print(f"{p} not found. Run: python -m scripts.district_supply_census")
        return 1
    groups = json.loads(p.read_text(encoding="utf-8"))

    if args.pilot:
        print("Run these first; yield = accepted / (added + rejected + extraction_failed + classify_failed) from each summary line.\n")
        for c in pilot_commands(groups, args.pilot, min_year=args.min_year, per_complex_cap=args.per_complex_cap):
            print(c)
        return 0

    states = {s.strip() for s in args.states.split(",") if s.strip()} or None
    new_needed = max(args.target - args.already_have, 0)
    plan = build_plan(groups, new_needed, args.assumed_yield, min_year=args.min_year, max_state_share=args.max_state_share,
                      max_year_share=args.max_year_share, per_complex_cap=args.per_complex_cap, states=states)
    print(f"Corpus goal {args.target:,}; already held {args.already_have:,}; sourcing {new_needed:,} NEW accepted cases.")
    print(f"Assumed yield of {100 * plan['assumed_yield']:.0f}% "
          f"(unmeasured until a pilot has run).\n")
    print(f"{'state':18s}{'year':>6s}{'quota':>8s}{'complexes':>11s}{'cands':>9s}{'GB':>8s}")
    for r in plan["rows"]:
        print(f"{r['state_name']:18s}{r['year']:>6s}{r['quota']:>8,}{r['complexes']:>11,}{r['candidates_in_chosen']:>9,}{r['archive_gb']:>8.1f}")
    print(f"\nPlanned {plan['planned']:,} of {plan['target']:,}"
          + (f"  -> SHORTFALL {plan['shortfall']:,}: the caps and the available supply cannot reach the target." if plan["shortfall"] > 0 else ""))
    for s, n in sorted(plan["state_totals"].items(), key=lambda kv: -kv[1]):
        name = next(r["state_name"] for r in plan["rows"] if r["state"] == s)
        print(f"  {name}: {n:,} ({100 * n / max(plan['planned'], 1):.0f}%)")
    print(f"\nCapacity: ~{plan['attempts_estimate']:,} extraction attempts, {plan['archive_gb']:.0f} GB of archives to download, "
          f"~{plan['judge_hours_estimate']:.0f} h of judging (extrapolated from the repo's ~4 h per 23,841 cases; sourcing "
          f"time is NOT estimated - measure it in the pilot).")
    cmds = [command_for(r, plan["per_complex_cap"]) for r in plan["rows"]]
    cmd_path = Path(args.out).with_suffix(".commands.txt")
    cmd_path.write_text("\n".join(cmds) + "\n", encoding="utf-8")
    Path(args.out).write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"\n{len(cmds)} sourcing commands written to {cmd_path}\nPlan saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
