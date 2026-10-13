"""Census of how many civil cases the free eCourts district-court bucket can actually supply.

Two numbers matter, and they differ a lot:
  * METADATA candidates  - rows the sourcing script's own filter would accept (civil case-type code, has_pdf,
    at least one final order). Cheap to count, but not all of them can be downloaded.
  * DOWNLOADABLE candidates - the same rows restricted to court-complex archives (data.tar) that really exist in the
    bucket. Several large states (Delhi, Haryana, Punjab, ...) have metadata but no archives.

The archive check is a HEAD request per (year, state, district, complex), so it is archive-level only: it proves the
archive exists, not that every case's PDF is inside it.

Output: data_cache/district_supply.json (one row per complex-year with a candidate), consumed by
scripts/plan_corpus_scale.py. Anonymous, read-only access to a public bucket; nothing is downloaded but metadata.

Run (from backend/):  python -m scripts.district_supply_census
                      python -m scripts.district_supply_census --min-year 2015 --no-tar-check
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
OUT = DATA / "district_supply.json"


def civil_candidates(meta, state_code: str):
    """The exact filter scripts/source_district_court_judgments.py applies to a state-year metadata frame."""
    from scripts.source_district_court_judgments import _civil_codes_for_state

    ct = meta["case_type"].fillna("").astype(str).str.strip().str.upper()
    return meta[ct.isin(_civil_codes_for_state(state_code)) & meta["has_pdf"] & (meta["final_orders"] > 0)]


def complex_groups(cands, year: str, state_code: str) -> list[dict]:
    """One row per court complex: how many candidate cases it holds."""
    if len(cands) == 0:
        return []
    state_name = str(cands.iloc[0]["state_name"])
    counts = cands.groupby(["district_code", "complex_code"]).size()
    return [{"year": str(year), "state": str(state_code), "state_name": state_name, "district": str(d),
             "complex": str(k), "candidates": int(n), "tar_bytes": None} for (d, k), n in counts.items()]


def _list_years(s3, bucket: str) -> list[str]:
    years, tok = [], None
    while True:
        kw = dict(Bucket=bucket, Prefix="metadata/parquet/", Delimiter="/")
        if tok:
            kw["ContinuationToken"] = tok
        r = s3.list_objects_v2(**kw)
        years += [p["Prefix"].split("year=")[1].strip("/") for p in r.get("CommonPrefixes", []) if "year=" in p["Prefix"]]
        if not r.get("IsTruncated"):
            return sorted(years)
        tok = r["NextContinuationToken"]


def summarise(groups: list[dict]) -> dict:
    """Totals by state and year, split into metadata-only vs downloadable. `tar_bytes` None = not checked."""
    checked = any(g["tar_bytes"] is not None for g in groups)
    out = {"metadata_candidates": sum(g["candidates"] for g in groups), "by_state": {}, "by_year": {}}
    for g in groups:
        ok = bool(g["tar_bytes"])
        for key, bucket in ((g["state_name"], "by_state"), (g["year"], "by_year")):
            d = out[bucket].setdefault(key, {"metadata": 0, "downloadable": 0, "gb": 0.0})
            d["metadata"] += g["candidates"]
            if ok:
                d["downloadable"] += g["candidates"]
                d["gb"] += g["tar_bytes"] / 1e9
    out["downloadable_candidates"] = sum(v["downloadable"] for v in out["by_state"].values()) if checked else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-year", type=int, default=2010)
    ap.add_argument("--no-tar-check", action="store_true", help="skip the per-complex archive HEAD requests")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    import boto3
    import pandas as pd
    from botocore import UNSIGNED
    from botocore.config import Config

    from scripts.source_district_court_judgments import ALL_STATE_CODES, BUCKET

    s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED, max_pool_connections=args.workers * 2,
                                          retries={"max_attempts": 3}))
    years = [y for y in _list_years(s3, BUCKET) if y.isdigit() and int(y) >= args.min_year]

    def scan(task):
        year, state = task
        try:
            body = s3.get_object(Bucket=BUCKET, Key=f"metadata/parquet/year={year}/state={state}/cases.parquet")["Body"].read()
        except Exception as e:  # NoSuchKey is the normal "this state-year was never synced" case
            return [] if "NoSuchKey" in type(e).__name__ or "NoSuchKey" in str(e) else [("error", year, state, repr(e))]
        return complex_groups(civil_candidates(pd.read_parquet(io.BytesIO(body)), state), year, state)

    groups: list[dict] = []
    errors = []
    with cf.ThreadPoolExecutor(args.workers) as ex:
        for res in ex.map(scan, [(y, s) for y in years for s in ALL_STATE_CODES]):
            for r in res:
                (errors if isinstance(r, tuple) else groups).append(r)
    print(f"{len(groups)} complex-year group(s) with civil candidates; {len(errors)} metadata read error(s)")
    for e in errors[:5]:
        print("  !", e)

    if not args.no_tar_check:
        def head(g):
            key = f"data/tar/year={g['year']}/state={g['state']}/district={g['district']}/complex={g['complex']}/data.tar"
            try:
                g["tar_bytes"] = int(s3.head_object(Bucket=BUCKET, Key=key)["ContentLength"])
            except Exception:
                g["tar_bytes"] = 0
            return g

        with cf.ThreadPoolExecutor(args.workers * 2) as ex:
            groups = list(ex.map(head, groups))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(groups), encoding="utf-8")
    s = summarise(groups)
    print(f"\nMetadata candidates:     {s['metadata_candidates']:>9,}")
    if s["downloadable_candidates"] is not None:
        print(f"Downloadable candidates: {s['downloadable_candidates']:>9,}   (archive exists; not proof every PDF is inside)")
    print(f"\n{'state':22s}{'metadata':>10s}{'downloadable':>14s}{'archive GB':>12s}")
    for k, v in sorted(s["by_state"].items(), key=lambda kv: -kv[1]["metadata"])[:12]:
        print(f"{k:22s}{v['metadata']:>10,}{v['downloadable']:>14,}{v['gb']:>12.1f}")
    print(f"\n{'year':22s}{'metadata':>10s}{'downloadable':>14s}")
    for k, v in sorted(s["by_year"].items()):
        print(f"{k:22s}{v['metadata']:>10,}{v['downloadable']:>14,}")
    print(f"\nSaved {args.out}")
    share = collections.Counter()
    for g in groups:
        if g["tar_bytes"]:
            share[g["state_name"]] += g["candidates"]
    tot = sum(share.values())
    if tot:
        name, n = share.most_common(1)[0]
        print(f"Largest single state among downloadable cases: {name} = {100 * n / tot:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
