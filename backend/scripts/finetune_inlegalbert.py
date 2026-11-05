"""Fine-tune InLegalBERT to predict who prevailed, as the strong supervised baseline the paper needs.

QUESTION. Given only a case description, can a fine-tuned legal language model name the real winner better than the
pipeline, than TF-IDF, than "always claimant"? And does it still do so on the LEAKAGE-FREE CONTROL descriptions
(scripts/build_leakage_free_control.py), which were rebuilt from pre-decision text only?

SETUP. Label = the real court's winner (claimant_prevailed_real from the judge's verdicts). Input = case_description.
  * TEST  = every control case that was judged (so original and control descriptions can be compared on the SAME
            cases) plus a random --test-frac of the remaining cases;
  * TRAIN = everything else, on ORIGINAL descriptions. No control case, original or rebuilt, is ever trained on.
The model is scored on the test cases' original descriptions and, for the control cases, on the rebuilt ones.
Reported against: always-claimant (majority), and the pipeline's own winner on the same cases when its verdict
is available. Also writes the model's confidence per test case, which the abstention analysis can use as a selector.

LOCAL ONLY. Loads the model from --model-dir (downloaded once, never fetched at run time). The weights file is a
pickle: transformers loads it with weights_only, and the directory is kept apart from the code (models_untrusted/).

Run from backend/ (GPU used if present):  python -m scripts.finetune_inlegalbert
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
DEFAULT_MODEL = Path("D:/Coding/Projects/DigiNyaya/models_untrusted/InLegalBERT")


def bucket(seed: int, case_id: str) -> float:
    return int(hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).hexdigest()[:12], 16) / float(16 ** 12)


def make_split(cases: list[dict], control_ids: set[str], test_frac: float, seed: int) -> tuple[list[dict], list[dict]]:
    """Control cases are always TEST; the rest split deterministically by case id."""
    train, test = [], []
    for c in cases:
        if c["case_id"] in control_ids or bucket(seed, c["case_id"]) < test_frac:
            test.append(c)
        else:
            train.append(c)
    return train, test


def metrics(y_true: list[int], y_pred: list[int]) -> dict:
    """Winner accuracy, macro-F1 and per-class recall (1 = claimant prevailed)."""
    n = len(y_true)
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)

    def f1(tp_, fp_, fn_):
        return 0.0 if tp_ == 0 else 2 * tp_ / (2 * tp_ + fp_ + fn_)

    return {
        "n": n, "accuracy": (tp + tn) / n if n else None,
        "macro_f1": (f1(tp, fp, fn) + f1(tn, fn, fp)) / 2 if n else None,
        "recall_claimant": tp / (tp + fn) if tp + fn else None,
        "recall_respondent": tn / (tn + fp) if tn + fp else None,
        "majority_rate": max(tp + fn, tn + fp) / n if n else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", default=str(DEFAULT_MODEL))
    ap.add_argument("--dataset", default=str(DATA / "eval_judgments_with_signals.json"))
    ap.add_argument("--verdicts", default=str(DATA / "real_judgment_verdict_comparison_v2.json"))
    ap.add_argument("--control-cases", default=str(DATA / "eval_leakfree_control.json"))
    ap.add_argument("--control-verdicts", default=str(DATA / "leakfree_control_verdicts.json"))
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--test-frac", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(DATA / "inlegalbert_report.json"))
    ap.add_argument("--preds-out", default=str(DATA / "inlegalbert_predictions.json"))
    args = ap.parse_args()

    import numpy as np
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_dir = Path(args.model_dir)
    if not (model_dir / "config.json").exists():
        print(f"Model not found at {model_dir}. Download law-ai/InLegalBERT there first.")
        return 1

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    verdicts = {v["case_id"]: v for v in json.loads(Path(args.verdicts).read_text(encoding="utf-8"))
                if v.get("verdict") in ("match", "partial", "mismatch") and v.get("claimant_prevailed_real") is not None}
    cases = [c for c in json.loads(Path(args.dataset).read_text(encoding="utf-8")) if c["case_id"] in verdicts]
    control = {c["case_id"]: c for c in json.loads(Path(args.control_cases).read_text(encoding="utf-8"))}
    control_verdicts = {v["case_id"]: v for v in json.loads(Path(args.control_verdicts).read_text(encoding="utf-8"))
                        if v.get("verdict") in ("match", "partial", "mismatch")}
    control_ids = {i for i in control if i in verdicts}
    train, test = make_split(cases, control_ids, args.test_frac, args.seed)
    print(f"Train {len(train)} | test {len(test)} (of which {len(control_ids)} control cases)")

    label = lambda c: int(bool(verdicts[c["case_id"]]["claimant_prevailed_real"]))  # noqa: E731
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir), num_labels=2, local_files_only=True).to(device)

    def encode(texts):
        return tok(texts, truncation=True, max_length=args.max_len, padding=True, return_tensors="pt")

    # Class-weighted loss: claimant wins are ~83% of cases, so without weights the model collapses to "claimant".
    y_train = [label(c) for c in train]
    pos = sum(y_train)
    weights = torch.tensor([len(y_train) / (2 * max(1, len(y_train) - pos)), len(y_train) / (2 * max(1, pos))],
                           dtype=torch.float, device=device)
    loss_fn = torch.nn.CrossEntropyLoss(weight=weights)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps = args.epochs * ((len(train) + args.batch_size - 1) // args.batch_size)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / max(1, int(0.06 * steps))) * max(0.0, 1 - s / steps))

    use_amp = device == "cuda"  # fp16 autocast: the 6 GB laptop GPU runs out of memory in fp32
    scaler = torch.amp.GradScaler(device, enabled=use_amp)
    t0 = time.time()
    order = list(range(len(train)))
    step = 0
    for epoch in range(args.epochs):
        random.shuffle(order)
        model.train()
        running = 0.0
        for i in range(0, len(order), args.batch_size):
            batch = [train[j] for j in order[i:i + args.batch_size]]
            enc = {k: v.to(device) for k, v in encode([c["case_description"] for c in batch]).items()}
            y = torch.tensor([label(c) for c in batch], device=device)
            with torch.autocast(device_type=device, dtype=torch.float16, enabled=use_amp):
                logits = model(**enc).logits
            loss = loss_fn(logits.float(), y)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
            opt.zero_grad()
            running += float(loss.detach())
            step += 1
            if step % 50 == 0:
                print(f"  epoch {epoch + 1} step {step}/{steps} loss {running / 50:.3f}  ({time.time() - t0:.0f}s elapsed)", flush=True)
                running = 0.0

    @torch.no_grad()
    def predict(texts):
        model.eval()
        probs = []
        for i in range(0, len(texts), 32):
            enc = {k: v.to(device) for k, v in encode(texts[i:i + 32]).items()}
            with torch.autocast(device_type=device, dtype=torch.float16, enabled=use_amp):
                logits = model(**enc).logits
            probs.extend(torch.softmax(logits.float(), dim=-1)[:, 1].cpu().tolist())
        return probs

    p_orig = predict([c["case_description"] for c in test])
    preds = {c["case_id"]: {"p_claimant_original": p, "real_claimant": label(c), "is_control": c["case_id"] in control_ids}
             for c, p in zip(test, p_orig)}
    ctl_cases = [control[i] for i in sorted(control_ids)]
    for c, p in zip(ctl_cases, predict([c["case_description"] for c in ctl_cases])):
        preds[c["case_id"]]["p_claimant_control"] = p
    for cid, pr in preds.items():  # the pipeline's own winner on the same case, where it exists
        v = verdicts.get(cid)
        pr["pipeline_claimant_original"] = None if v is None else v.get("claimant_prevailed_ai")
        cv = control_verdicts.get(cid)
        pr["pipeline_claimant_control"] = None if cv is None else cv.get("claimant_prevailed_ai")
    Path(args.preds_out).write_text(json.dumps(preds), encoding="utf-8")

    def score(rows, key, pipeline_key=None):
        rows = [r for r in rows if r.get(key) is not None and (pipeline_key is None or r.get(pipeline_key) is not None)]
        y = [r["real_claimant"] for r in rows]
        out = {"model": metrics(y, [int(r[key] > 0.5) for r in rows])}
        if pipeline_key:
            out["pipeline"] = metrics(y, [int(bool(r[pipeline_key])) for r in rows])
        return out

    allrows, ctlrows = list(preds.values()), [r for r in preds.values() if r["is_control"]]
    report = {
        "train_n": len(train), "test_n": len(test), "control_n": len(ctl_cases), "epochs": args.epochs,
        "test_all_original": score(allrows, "p_claimant_original", "pipeline_claimant_original"),
        "control_cases_original": score(ctlrows, "p_claimant_original", "pipeline_claimant_original"),
        "control_cases_rebuilt": score(ctlrows, "p_claimant_control", "pipeline_claimant_control"),
    }
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name in ("test_all_original", "control_cases_original", "control_cases_rebuilt"):
        for who, m in report[name].items():
            print(f"{name:<24} {who:<9} n={m['n']:<5} acc={100 * m['accuracy']:.1f}% macroF1={m['macro_f1']:.3f} "
                  f"recall claimant={100 * m['recall_claimant']:.0f}% respondent={100 * (m['recall_respondent'] or 0):.0f}% "
                  f"(majority {100 * m['majority_rate']:.1f}%)")
    print(f"Wrote {args.out} and {args.preds_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
