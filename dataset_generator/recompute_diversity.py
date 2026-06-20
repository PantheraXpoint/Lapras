#!/usr/bin/env python3
"""Recompute the diversity QC for the DOORE caption dataset (robust version).

Within each class, does caption-embedding distance correlate with session-feature
distance? A positive Spearman rho => captions vary WITH the underlying sensor
evidence (grounded variation), not random wording.

Fixes the NaN bug in the inline check: drops all-NaN / zero-variance feature
columns within each class subset before computing distances.
"""
import os
import json
import numpy as np
import pandas as pd
from scipy.spatial.distance import pdist
from scipy.stats import spearmanr
from sentence_transformers import SentenceTransformer

HERE = os.path.dirname(os.path.abspath(__file__))
JSONL = os.path.join(HERE, "doore_captions.jsonl")
FEATS = os.path.join(HERE, "doore_session_features.csv")

# one caption per session (variant 0) for a clean per-session comparison
per_sess = {}
for line in open(JSONL):
    r = json.loads(line)
    if r.get("variant_idx") == 0:
        per_sess[r["session"]] = {"class": r["class"],
                                  "caption": r["messages"][-1]["content"]}
print(f"sessions with a variant-0 caption: {len(per_sess)}")

feats = pd.read_csv(FEATS).set_index("session")
num = feats.select_dtypes("number")

by_class = {}
for s, d in per_sess.items():
    if s in num.index:
        by_class.setdefault(d["class"], []).append((s, d["caption"]))

# NOTE: jina-clip-v1's text encoder returns NaN embeddings in this environment
# (RoPE buffer issue), so we use a reliable general-purpose sentence embedder for
# this text-only diversity QC. (jina-clip is only the RAG pipeline's image/text
# index model; any good sentence embedder is valid for measuring caption variation.)
print("loading sentence-transformers/all-MiniLM-L6-v2 ...")
model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

result = {}
for cls, items in sorted(by_class.items()):
    n = len(items)
    if n < 5:
        result[cls] = {"n": n, "status": "too few"}
        continue
    sess_ids = [s for s, _ in items]
    caps = [c for _, c in items]

    F = num.loc[sess_ids].copy()
    F = F.dropna(axis=1, how="all")              # drop all-NaN columns
    F = F.fillna(F.median(numeric_only=True)).fillna(0.0)
    F = F.loc[:, F.std(axis=0) > 0]              # drop zero-variance columns
    Fz = (F - F.mean()) / F.std()

    emb = model.encode(caps, normalize_embeddings=True)
    d_cap = pdist(emb, metric="cosine")
    d_feat = pdist(Fz.values, metric="euclidean")

    ok = np.isfinite(d_cap).all() and np.isfinite(d_feat).all()
    if not ok or d_cap.std() == 0 or d_feat.std() == 0:
        result[cls] = {"n": n, "status": "degenerate distances"}
        continue
    rho, p = spearmanr(d_cap, d_feat)
    result[cls] = {"n": n, "n_features_used": F.shape[1],
                   "spearman_rho": round(float(rho), 3),
                   "p_value": round(float(p), 5)}

print("\n==== DIVERSITY CHECK (caption-embedding dist vs feature dist) ====")
print(json.dumps(result, indent=2))
with open(os.path.join(HERE, "doore_diversity_check.json"), "w") as f:
    json.dump(result, f, indent=2)
print("\nwrote doore_diversity_check.json")
