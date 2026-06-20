#!/usr/bin/env python3
"""Session-stratified train/val[/test] split for the DOORE caption dataset.

Splits are made by SESSION (never by row), so all K worded variants of a session
stay on the same side — otherwise near-duplicate captions leak across splits.
Splitting is stratified by class and deterministic given --seed.

Optional quality filters let you build a higher-precision subset using the QC
flags (see OUTPUT_FORMAT.md).

Examples
--------
# default 90/10 train/val, all rows
python3 make_splits.py

# 80/10/10 train/val/test, drop sensor-invisible rows
python3 make_splits.py --val-frac 0.1 --test-frac 0.1 --drop-low-evidence

# confident subset only (strong+moderate evidence)
python3 make_splits.py --min-evidence moderate --out-prefix doore_conf
"""
import os
import json
import random
import argparse
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
EV_RANK = {"weak": 0, "moderate": 1, "strong": 2}


def main():
    ap = argparse.ArgumentParser(description="Session-stratified split of the caption dataset")
    ap.add_argument("--input", default=os.path.join(HERE, "doore_captions.jsonl"))
    ap.add_argument("--out-prefix", default="doore",
                    help="writes <prefix>_train.jsonl / _val.jsonl / _test.jsonl")
    ap.add_argument("--val-frac", type=float, default=0.10)
    ap.add_argument("--test-frac", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--drop-low-evidence", action="store_true",
                    help="drop rows where low_evidence is true")
    ap.add_argument("--min-evidence", choices=["weak", "moderate", "strong"], default=None,
                    help="keep only rows with evidence_strength >= this level")
    args = ap.parse_args()

    assert 0 <= args.val_frac < 1 and 0 <= args.test_frac < 1, "fractions must be in [0,1)"
    assert args.val_frac + args.test_frac < 1, "val+test must leave room for train"

    rows = [json.loads(l) for l in open(args.input)]
    total_in = len(rows)

    # ---- optional quality filters (row-level) ----
    if args.drop_low_evidence:
        rows = [r for r in rows if not r.get("low_evidence")]
    if args.min_evidence:
        floor = EV_RANK[args.min_evidence]
        rows = [r for r in rows if EV_RANK.get(r.get("evidence_strength", "weak"), 0) >= floor]
    print(f"loaded {total_in} rows; {len(rows)} kept after filters")

    # ---- group rows by session, remember each session's class ----
    by_session = defaultdict(list)
    sess_class = {}
    for r in rows:
        by_session[r["session"]].append(r)
        sess_class[r["session"]] = r["class"]

    # ---- assign whole sessions to splits, stratified by class ----
    rng = random.Random(args.seed)
    sessions_by_class = defaultdict(list)
    for s, c in sess_class.items():
        sessions_by_class[c].append(s)

    split_of = {}
    for c, sess in sessions_by_class.items():
        sess = sorted(sess)          # stable base order before shuffle
        rng.shuffle(sess)
        n = len(sess)
        n_val = round(n * args.val_frac)
        n_test = round(n * args.test_frac)
        for i, s in enumerate(sess):
            if i < n_val:
                split_of[s] = "val"
            elif i < n_val + n_test:
                split_of[s] = "test"
            else:
                split_of[s] = "train"

    # ---- write splits ----
    buckets = {"train": [], "val": [], "test": []}
    for s, rs in by_session.items():
        buckets[split_of[s]].extend(rs)

    written = {}
    for name, items in buckets.items():
        if name == "test" and args.test_frac == 0:
            continue
        path = os.path.join(HERE, f"{args.out_prefix}_{name}.jsonl")
        with open(path, "w") as f:
            for r in items:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        written[name] = (path, items)

    # ---- report (rows + sessions + per-class) ----
    print("\n==== SPLIT SUMMARY (rows | sessions) ====")
    for name, (path, items) in written.items():
        sess = {r["session"] for r in items}
        per_class = Counter(r["class"] for r in items)
        print(f"{name:5s}: {len(items):5d} rows | {len(sess):4d} sessions | "
              f"{dict(sorted(per_class.items()))}")
        print(f"       -> {path}")

    # ---- leakage assertion ----
    sets = {n: {r['session'] for r in items} for n, (_, items) in written.items()}
    names = list(sets)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            overlap = sets[names[i]] & sets[names[j]]
            assert not overlap, f"SESSION LEAK between {names[i]}/{names[j]}: {overlap}"
    print("\nOK: no session appears in more than one split (no variant leakage).")


if __name__ == "__main__":
    main()
