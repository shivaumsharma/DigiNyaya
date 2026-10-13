"""Admission checks for newly sourced eval cases, run BEFORE they are merged or scored.

For every case in the batch, in order of severity:
  REJECT  malformed record; duplicate of an existing case or of an earlier one in the batch (exact id/docid, or a
          near-duplicate description); too short to be a case; not English (Indic script); personal identifiers left
          in the text (phone, Aadhaar-style number, e-mail, PAN); description states the court's decision
          (strong outcome phrasing) or names the judge; category the eval harness cannot map; overlaps the precedents.
  FLAG    kept but reported: honorific + name left in the text (e.g. "Smt. Rao"), some Indic script, very long text.

Then the batch as a whole is judged against concentration and quality thresholds (PASS / WARN / FAIL). Several defaults
WILL warn on the free data: nearly all downloadable supply is Andhra Pradesh and Telangana, and that is information
the reader of any result built on this corpus needs.

Nothing canonical is touched. `--write-clean` writes the survivors to a NEW file; it refuses to overwrite an input.

Run (from backend/):
  python -m scripts.corpus_gate --batch-dir data_cache\\dc_slices
  python -m scripts.corpus_gate --batch data_cache\\dc_slices\\pilot_2_2023.json --write-clean data_cache\\pilot_clean.json
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import zlib
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
PRECEDENTS = Path(__file__).resolve().parent.parent / "app" / "data" / "precedents.json"

MIN_DESC_CHARS, MIN_OUTCOME_CHARS, LONG_DESC_CHARS = 250, 40, 6000
INDIC_REJECT, INDIC_FLAG = 0.30, 0.02
NEAR_DUP_JACCARD = 0.8

_PII = {
    "phone": re.compile(r"(?<![\d.])(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d|\.\d)"),
    "aadhaar": re.compile(r"(?<![\d.])\d{4}\s\d{4}\s\d{4}(?!\d)"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
}
_HONORIFIC_NAME = re.compile(r"\b(?:Sri|Shri|Smt|Mr|Mrs|Ms|Dr)\.?\s+[A-Z][a-z]{2,}")


# ---------------------------------------------------------------- near-duplicates
def _shingles(text: str, k: int = 5, cap: int = 400) -> set[int]:
    words = re.findall(r"[a-z]+", (text or "").lower())[:cap]
    if len(words) < k:
        return {zlib.crc32(" ".join(words).encode())}
    return {zlib.crc32(" ".join(words[i:i + k]).encode()) for i in range(len(words) - k + 1)}


class NearDupIndex:
    """MinHash signatures with LSH bands. Jaccard is ESTIMATED from the signatures (fraction of equal minima), so a
    pair near the threshold can go either way; that is acceptable for de-duplication, not for a statistical claim."""

    P = (1 << 61) - 1

    def __init__(self, perms: int = 16, rows: int = 2, threshold: float = NEAR_DUP_JACCARD, seed: int = 11):
        import numpy as np

        self.np = np
        rng = np.random.default_rng(seed)
        self.a = rng.integers(1, 2**31 - 1, size=perms, dtype=np.int64)
        self.b = rng.integers(0, 2**31 - 1, size=perms, dtype=np.int64)
        self.perms, self.rows, self.threshold = perms, rows, threshold
        self.buckets: dict[tuple, list[int]] = collections.defaultdict(list)
        self.sigs: list = []
        self.ids: list[str] = []

    def signature(self, text: str):
        np = self.np
        h = np.fromiter((x & 0x7FFFFFFF for x in _shingles(text)), dtype=np.int64)
        return ((self.a[:, None] * h[None, :] + self.b[:, None]) % (2**31 - 1)).min(axis=1)

    def add_or_match(self, case_id: str, text: str) -> str | None:
        """Returns the id of an earlier near-duplicate, else registers this text and returns None."""
        np = self.np
        sig = self.signature(text)
        keys = [(band, tuple(sig[band * self.rows:(band + 1) * self.rows])) for band in range(self.perms // self.rows)]
        seen: set[int] = set()
        for key in keys:
            for idx in self.buckets.get(key, ()):
                if idx in seen:
                    continue
                seen.add(idx)
                if float(np.mean(self.sigs[idx] == sig)) >= self.threshold:
                    return self.ids[idx]
        idx = len(self.sigs)
        self.sigs.append(sig)
        self.ids.append(case_id)
        for key in keys:
            self.buckets[key].append(idx)
        return None


# ---------------------------------------------------------------- per-row checks
def indic_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(0x0900 <= ord(c) <= 0x0DFF for c in letters) / len(letters) if letters else 0.0


def check_row(case: dict, categories: set[str]) -> tuple[list[str], list[str]]:
    """(reject_reasons, flag_reasons) for one case, excluding duplicate checks (those need the whole batch)."""
    from scripts.audit_outcome_leakage import audit_text

    reject: list[str] = []
    flag: list[str] = []
    desc, outcome = case.get("case_description"), case.get("expected_outcome")
    src = case.get("source") or {}
    if not case.get("case_id") or not isinstance(desc, str) or not isinstance(outcome, str) or not src.get("docid"):
        return ["malformed"], []
    if case.get("category") not in categories:
        reject.append("category_not_mappable")
    if len(desc.strip()) < MIN_DESC_CHARS:
        reject.append("description_too_short")
    if len(outcome.strip()) < MIN_OUTCOME_CHARS:
        reject.append("outcome_too_short")
    if len(desc) > LONG_DESC_CHARS:
        flag.append("very_long_description")
    share = max(indic_share(desc), indic_share(outcome))
    if share >= INDIC_REJECT:
        reject.append("not_english")
    elif share >= INDIC_FLAG:
        flag.append("some_indic_script")
    for text in (desc, outcome):
        for name, rx in _PII.items():
            if rx.search(text):
                reject.append(f"pii_{name}")
    reject = list(dict.fromkeys(reject))
    if _HONORIFIC_NAME.search(desc) or _HONORIFIC_NAME.search(outcome):
        flag.append("honorific_name_left")
    a = audit_text(case)
    if a["outcome_language"]:
        reject.append("states_the_decision")
    if a["names_judge"]:
        reject.append("names_the_judge")
    if a["weak_language"]:
        flag.append("weak_outcome_language")
    if a["amount_echo"]:
        flag.append("amount_echo")
    return reject, flag


def _complex_of(case: dict) -> str:
    return (case.get("source") or {}).get("court") or "unknown"


def _state_of(case: dict) -> str:
    parts = _complex_of(case).split(",")
    return parts[-1].strip() or "unknown"


def run_gate(batch: list[dict], existing: list[dict], precedent_docids: set[str], categories: set[str]) -> dict:
    """Pure: returns {"survivors", "rejected": {case_id: [reasons]}, "flags": {case_id: [...]}, "counts"}."""
    existing_ids = {c.get("case_id") for c in existing}
    existing_docids = {str((c.get("source") or {}).get("docid")) for c in existing}
    index = NearDupIndex()
    for c in existing:  # earlier corpus wins over the batch
        if isinstance(c.get("case_description"), str):
            index.add_or_match(str(c.get("case_id")), c["case_description"])

    seen_ids: set[str] = set()
    seen_docids: set[str] = set()
    rejected: dict[str, list[str]] = {}
    flags: dict[str, list[str]] = {}
    survivors: list[dict] = []
    for i, c in enumerate(batch):
        cid = str(c.get("case_id") or f"<row {i}>")
        reasons, fl = check_row(c, categories)
        docid = str((c.get("source") or {}).get("docid"))
        if "malformed" not in reasons:
            if cid in existing_ids or cid in seen_ids or docid in existing_docids or docid in seen_docids:
                reasons.append("duplicate_id")
            if docid in precedent_docids:
                reasons.append("in_precedent_corpus")
            if not reasons:  # near-dup check last, so a case rejected for another reason does not become a "first copy"
                twin = index.add_or_match(cid, c["case_description"])
                if twin:
                    reasons.append(f"near_duplicate_of:{twin}")
        seen_ids.add(cid)
        seen_docids.add(docid)
        if reasons:
            rejected[cid] = reasons
        else:
            survivors.append(c)
            if fl:
                flags[cid] = fl
    counts = collections.Counter(r.split(":")[0] for rs in rejected.values() for r in rs)
    return {"survivors": survivors, "rejected": rejected, "flags": flags, "reject_reasons": dict(counts)}


# ---------------------------------------------------------------- batch-level verdict
def _share(counter: collections.Counter) -> tuple[str, float]:
    total = sum(counter.values())
    if not total:
        return "-", 0.0
    k, n = counter.most_common(1)[0]
    return k, n / total


def batch_checks(batch_size: int, survivors: list[dict], flags: dict[str, list[str]]) -> list[tuple[str, str, str]]:
    """List of (PASS|WARN|FAIL, check, detail)."""
    from scripts.audit_outcome_leakage import audit_text

    out: list[tuple[str, str, str]] = []
    n = len(survivors)
    out.append(("FAIL" if n == 0 else "PASS", "survivors", f"{n} of {batch_size} kept"))
    if n == 0:
        return out
    rej = 1 - n / batch_size
    out.append(("FAIL" if rej > 0.5 else "WARN" if rej > 0.3 else "PASS", "gate rejection rate", f"{100 * rej:.1f}% of the batch rejected"))

    cat = collections.Counter(c["category"] for c in survivors)
    thin = {k: v for k, v in cat.items() if v < 100}
    out.append(("WARN" if thin else "PASS", "category depth (>=100 each)", f"{dict(sorted(cat.items()))}" + (f"; thin: {thin}" if thin else "")))

    st = collections.Counter(_state_of(c) for c in survivors)
    name, s = _share(st)
    out.append(("FAIL" if s > 0.9 else "WARN" if s > 0.6 else "PASS", "largest state share", f"{name} = {100 * s:.0f}%; {len(st)} state(s) in all"))
    cx = collections.Counter(_complex_of(c) for c in survivors)
    name, s = _share(cx)
    out.append(("FAIL" if s > 0.02 and n >= 500 else "PASS", "largest court share (<=2%)", f"{name} = {100 * s:.1f}%; {len(cx)} courts"))
    yr = collections.Counter(str((c.get("source") or {}).get("year")) for c in survivors)
    name, s = _share(yr)
    out.append(("WARN" if s > 0.3 else "PASS", "largest year share", f"{name} = {100 * s:.0f}%"))

    audits = [audit_text(c) for c in survivors]
    weak = sum(bool(a["weak_language"]) for a in audits) / n
    echo = sum(bool(a["amount_echo"]) for a in audits) / n
    out.append(("WARN" if weak > 0.25 else "PASS", "weak outcome phrasing in description", f"{100 * weak:.1f}% (flag, not reject)"))
    out.append(("WARN" if echo > 0.5 else "PASS", "amount echo (description amount == outcome amount)",
                f"{100 * echo:.1f}%: if high, an 'amount within 20%' metric is a soft upper bound"))
    ver = sum(bool(c.get("verified")) for c in survivors)
    out.append(("WARN" if ver == 0 else "PASS", "human-verified cases", f"{ver} of {n} (all LLM-extracted; verified=false is the convention)"))
    return out


def _load_cases(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        rows += data if isinstance(data, list) else []
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", nargs="*", default=[], help="one or more case JSON files")
    ap.add_argument("--batch-dir", default=None, help="directory of case JSON files (all *.json, pilot files included)")
    ap.add_argument("--existing", default=str(DATA / "eval_judgments.json"))
    ap.add_argument("--precedents", default=str(PRECEDENTS))
    ap.add_argument("--write-clean", default=None, help="write the survivors to this NEW file")
    ap.add_argument("--report", default=str(DATA / "corpus_gate_report.json"))
    args = ap.parse_args()

    paths = [Path(p) for p in args.batch]
    if args.batch_dir:
        paths += sorted(Path(args.batch_dir).glob("*.json"))
    paths = [p for p in paths if p.exists()]
    if not paths:
        print("No batch files found. Pass --batch FILE... or --batch-dir DIR.")
        return 1

    from scripts.run_real_judgment_eval import CATEGORY_TO_DISPUTE_TYPE

    batch = _load_cases(paths)
    ex_path = Path(args.existing)
    existing = _load_cases([ex_path]) if ex_path.exists() and ex_path not in paths else []
    prec = json.loads(Path(args.precedents).read_text(encoding="utf-8")) if Path(args.precedents).exists() else []
    prec_docids = {str(p.get("docid")) for p in prec if p.get("docid")}
    print(f"Batch: {len(batch)} case(s) from {len(paths)} file(s). Existing corpus compared against: {len(existing)}. "
          f"Precedent docids: {len(prec_docids)}.\n")

    res = run_gate(batch, existing, prec_docids, set(CATEGORY_TO_DISPUTE_TYPE))
    print("Rejected by reason:")
    for k, v in sorted(res["reject_reasons"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:28s}{v:>7,}")
    flag_counts = collections.Counter(f for fs in res["flags"].values() for f in fs)
    print("Kept but flagged:", dict(flag_counts) or "none")

    print()
    checks = batch_checks(len(batch), res["survivors"], res["flags"])
    for status, name, detail in checks:
        print(f"[{status}] {name}: {detail}")

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps({"batch": len(batch), "survivors": len(res["survivors"]),
                                             "reject_reasons": res["reject_reasons"], "checks": checks,
                                             "rejected": res["rejected"]}, indent=2), encoding="utf-8")
    if args.write_clean:
        out = Path(args.write_clean)
        if out in paths or out == ex_path:
            print("\nRefusing to overwrite an input file. Choose a new --write-clean path.")
            return 1
        out.write_text(json.dumps(res["survivors"], indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote {len(res['survivors'])} clean case(s) to {out}")
    print(f"\nReport: {args.report}")
    return 1 if any(s == "FAIL" for s, _, _ in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
