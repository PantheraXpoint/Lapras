# DOORE Caption Dataset — Output Format

This describes every output file and field, and what you need to **train** with it.

---

## Files

| File | Rows | One record = | Role |
|---|---|---|---|
| **`doore_captions.jsonl`** | 1,490 | one **(session × variant)** | **the training dataset** |
| `doore_captions_detail.jsonl` | 696 | one **session** (variants nested) | provenance / debugging |
| `doore_captions_run_summary.json` | 1 obj | the run | counts + QC summary |
| `doore_diversity_check.json` | 1 obj | the QC | grounded-diversity correlations |
| `doore_session_features.csv` | 696 | one session | numeric features (analysis) |
| `doore_train.json` / `doore_detail.json` | — | — | pretty-printed array mirrors of the two JSONL files |

> **1 detail line = K training lines.** The detail file stores one session with a
> `variants` array; the training file flattens it to one row per variant. K is the
> per-class variant multiplier (`Small_Talk 1, Reading 2, Technical_discussion 3,
> Eating 5, Study_together 8`).

---

## `doore_captions.jsonl` — the training file

One JSON object per line. Fields:

| Field | Type | Meaning |
|---|---|---|
| `session` | str | session id, e.g. `"Eating together_10"` |
| `class` | str | folder class (`Eating`, `Reading`, `Small_Talk`, `Study_together`, `Technical_discussion`) |
| `label` | str | human activity label given to the model (`"Eating"`, `"Technical Discussion"`…) — **the input identity, NOT the caption** |
| `sub_label` | str | finer activity (`"Phone call"`, `"Seminar"`, …) |
| `caption` | str | ✅ **the generated caption** (flat copy of the assistant turn) |
| `variant_idx` | int | which worded variant (0…K-1) |
| `messages` | list | **chat-format training example** (see below) |
| `fields` | obj | structured caption fields: `occupancy, sound, motion, equipment, distinctive_features, confidence` |
| `confidence` | str | model's self-rated confidence: `high` / `medium` / `low` |
| `evidence_strength` | str | auto-assessed from sensors: `strong` / `moderate` / `weak` (gates hedging) |
| `qc_unconditioned_guess` | str | what a **label-free** QC pass guessed the activity was |
| `qc_correct` | bool | did that guess match the true class? |
| `low_evidence` | bool | **filtering flag** — true if QC was wrong or low-confidence (sensors don't support the label) |
| `feature_ref` | obj | `{features_csv, session_key}` → row in `doore_session_features.csv` |

### `messages` (the actual training signal)
```json
[
  {"role": "system",    "content": "<caption prompt: rules, intrinsic, hedge, no fabrication>"},
  {"role": "user",      "content": "Sensor session digest ...\nActivity: <label> (sub-label: <sub>).\nWrite an expressive, evidence-grounded caption of this session."},
  {"role": "assistant", "content": "<the caption>"}        ← TRAINING TARGET
]
```
The model is trained to produce the **assistant** message from system+user.
`caption` == the assistant content (provided flat for convenience).

> Note: there is **no `thinking` field** in the training file. In the detail file
> each variant has `"thinking": null` because the run was executed without
> `--include-thinking` (the model *did* reason; the trace was intentionally not
> stored, and is excluded from the training target by design).

---

## `doore_captions_detail.jsonl` — per-session provenance

One object per session. Not for training directly — use it to inspect *why* a
caption was produced.

| Field | Meaning |
|---|---|
| `session, class, label, sub_label` | as above |
| `duration_sec, avg_humans` | from metadata |
| `n_windows, n_segments` | windowing / macro-segmentation counts |
| `sensors_present` | sensor groups available in this session |
| `evidence_strength`, `evidence_reasons` | auto evidence assessment + why |
| `session_aggregate_digest` | the whole-session numeric digest text fed to synthesis |
| `qc_digest` | the label-free digest fed to the QC guess |
| `variants` | array of `{variant_idx, caption, fields, confidence, thinking}` |
| `incremental_traces` | per-segment thinking (null unless `--include-thinking`) |

---

## `doore_captions_run_summary.json`
`selected_sessions`, `per_class_selected`, `variant_multipliers`, `training_rows`,
`per_class_rows`, `qc_unconditioned` (per-class correct / low_evidence counts),
`diversity_check` (superseded by `doore_diversity_check.json`), `outputs`.

## `doore_diversity_check.json`
Per class: `n`, `n_features_used`, `spearman_rho`, `p_value` — correlation between
caption-embedding distance and feature distance (higher rho = caption variation
tracks sensor variation).

---

## How to use it for fine-tuning

1. **Train on the chat format.** Feed `messages`; mask everything but the
   **assistant** turn as the loss target. (Or use `caption` as the target with
   `system`+`user` as the prompt if your trainer expects flat fields.)

2. **Filtering / weighting (recommended).** Roughly 80% of non-`Technical_discussion`
   rows have `low_evidence=true` — the activity isn't actually visible in the
   sensors. Options:
   - keep everything (model learns to hedge on weak evidence — that's intended), or
   - **down-weight or drop** `low_evidence=true` rows if you want a higher-precision set, or
   - train only on `evidence_strength in {strong, moderate}` for a "confident" subset.

3. **Splitting.** Split **by `session`**, not by row — all K variants of a session
   must stay on the same side to avoid leakage. Stratify by `class`. Use
   **`make_splits.py`** (does exactly this, deterministically, and asserts no leak):
   ```bash
   python3 make_splits.py --val-frac 0.1 --test-frac 0.1            # 80/10/10
   python3 make_splits.py --drop-low-evidence                        # confident set
   python3 make_splits.py --min-evidence moderate --out-prefix doore_conf
   ```
   Writes `<prefix>_train.jsonl` / `_val.jsonl` / `_test.jsonl`.
   ⚠️ Naming note: the split file `doore_train.jsonl` is **not** the same as
   `doore_train.json` (the pretty-printed mirror of the *full* dataset).

4. **Class balance** is already handled by the K multipliers (rows per class:
   Eating 280, Reading 426, Small_Talk 303, Study_together 168,
   Technical_discussion 303) — far flatter than the raw 14× session imbalance.

5. **Provenance.** `feature_ref.session_key` joins back to
   `doore_session_features.csv`; `doore_captions_detail.jsonl` (keyed by `session`)
   has the digests behind each caption.

### Quick load (Python)
```python
import json
rows = [json.loads(l) for l in open("doore_captions.jsonl")]
# confident subset, deduped to one caption per session:
conf = [r for r in rows if not r["low_evidence"]]
# group variants by session for a clean split:
from collections import defaultdict
by_session = defaultdict(list)
for r in rows: by_session[r["session"]].append(r)
```
