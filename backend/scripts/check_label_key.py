"""Is the hand-label sheet's hidden key still describing the CURRENT judge?

human_label_key.json stamps every row with the hash of the judge's prompt/logic that produced
it (judge_real_outcomes._judge_prompt_hash). If that differs from the hash of the code in this
checkout, labelling the sheet validates an OLDER judge than the one behind today's numbers.
This prints the key's hashes next to the current one, plus the verdict mix and row count.
It does not print any individual judge answer, so it is safe to run before labelling.

Run (from backend/): python -m scripts.check_label_key
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"


def main() -> int:
    from scripts.judge_real_outcomes import _judge_prompt_hash

    key_path = DATA / "human_label_key.json"
    if not key_path.exists():
        print(f"Not found: {key_path}")
        return 1
    key = json.loads(key_path.read_text(encoding="utf-8"))
    hashes = Counter(v.get("judge_prompt_hash") for v in key.values())
    verdicts = Counter(v["verdict"] for v in key.values())
    current = _judge_prompt_hash()
    print(f"{len(key)} rows in the key.")
    print(f"Verdict mix (judge's, aggregate only): {dict(verdicts)}")
    print(f"Judge hash(es) stamped in the key: {dict(hashes)}")
    print(f"Judge hash of the code in this checkout: {current}")
    if set(hashes) == {current}:
        print("MATCH: the key was produced by the current judge. Labelling it validates the judge behind today's numbers.")
    else:
        print("DIFFERENT: the judge has changed since this key was made. Labels would validate the older judge; "
              "re-run the current judge on these case_ids (or rebuild the sheet) before treating them as validating today's numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
