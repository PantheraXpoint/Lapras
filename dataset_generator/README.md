# DOORE Caption Dataset Generator

This directory turns the raw **DOORE smart-room sensor recordings** into a
**caption fine-tuning dataset**: one expressive, evidence-grounded natural-language
caption (with several worded variants) per recording session. The goal is to
*extend DOORE's fixed activity labels* into rich text supervision for a
sensor-to-text captioning model.

It was built in three stages — **explore → generate → quality-check** — described
below from scratch.

---

## 0. The data

- **Source:** `../smart_home_gs/doore/` — 696 recording sessions in 5 activity-class
  folders (`Eating`, `Reading`, `Small_Talk`, `Study_together`,
  `Technical_discussion`). Heavily imbalanced (Small_Talk 303 … Study_together 21).
- **Each session = two files:**
  - `metadata/<name>.json` — `label`, sub-label, start/end (epoch ms),
    `duration`, `avg_n_human`, and the list of active sensors.
  - `sensor/<name>.csv` — long format `timestamp(ms), sensor_name, value`,
    event-based and multi-rate.
- **Sensors** map to a common schema: sound (L/C/R/podium, continuous), motion
  (8 zones, boolean), seat (12 seats, boolean), podium IR (continuous), door
  (event), temperature/humidity/brightness (continuous), lights/AC/projector
  (On/Off state).

---

## 1. Exploratory analysis — `analyze_doore.py`

Decides *how* captioning should work by answering two questions with numbers.

**What it does**
1. Loads all 696 sessions; reports per-class / per-sub-label counts, duration and
   occupancy distributions, and a **sensor-availability matrix**.
2. Builds **one feature vector per session** (per-sensor mean/std/min/max/range,
   event rates, per-channel sound energy, motion/seat density, door & equipment
   activity, first-half vs second-half temporal deltas). Missing sensors handled
   explicitly (never treated as zero).
3. **Separability:** PCA/UMAP projection + a random-forest classifier
   (per-class precision/recall, confusion matrix, top discriminative features).
4. **Within-class spread:** intra- vs inter-class distances, per-class variance,
   and whether **sub-labels form sub-clusters**.
5. Flags data issues (missing sensors, irregular cadence, outliers, imbalance).

**Key findings that shaped the pipeline**
- Classes are **only weakly separable** (RF ~0.76 acc but macro-F1 ~0.56;
  silhouette ≈ 0). **Only `Technical_discussion` is cleanly identifiable** from
  sensors; `Eating`/`Study_together` are essentially sensor-invisible.
- **Within-class variation is large**, and separation is driven by *context*
  (occupancy, projector/podium use, sound) — not an intrinsic "activity signature".

**Design decisions (locked in from this):**
- **Intrinsic** captioning (describe each session's own evidence), **not**
  contrastive against a class prototype.
- Condition on the true label as **identity**, but **never fabricate**
  activity-specific evidence; **hedge** when signals are weak.
- **Incremental/rolling** aggregation over time windows.

**Outputs:** `doore_session_features.csv`, `sensor_availability_matrix.csv`,
`figures/` (PCA/UMAP, confusion matrix, feature importance, sub-cluster plots).

---

## 2. Caption generation — `doore_caption_pipeline.py`

Offline/batch (no MQTT). Reuses the live system's vLLM client
(`smart_home_gs/rag/llm_client.py`, `init_llm_client` / `generate_response`,
Qwen3-8B served by vLLM). Per session:

1. **Load** metadata + sensor CSV; map raw sensor names to the common schema;
   clean dirty names (`Light_2\t`, `Projector ` whitespace).
2. **Numeric window digests (no LLM):** split the session into ~20 s windows and
   compute a compact digest per window — occupancy (seat count + motion zones,
   anchored by `avg_humans`), per-channel sound, motion/seat density, door events,
   equipment state (carried forward), temp/humidity/brightness.
   **Missing ≠ zero:** an absent sensor is reported as *absent*, never silence,
   and sensor presence/absence is never used as evidence.
3. **Adaptive macro-segmentation:** group windows into **≤ `max_segments`**
   segments, placing boundaries at the largest sensor-drift points (phase changes),
   so long sessions don't explode the LLM call count.
4. **Incremental reasoning pass** (thinking ON, low temp 0.2): walk segments in
   order; each step makes **one LLM call** that updates a compact, code-side
   **running-state object** (running occupancy, equipment state, recent
   motion/door, sound trend, a short running-understanding paragraph).
5. **Evidence-strength auto-assessment** (`strong`/`moderate`/`weak`) computed from
   the session's own sensors. This **drives hedging**: weak → caption must hedge
   and set `confidence=low`; moderate → at most `medium`.
6. **Final synthesis** (thinking ON, temp 0.7): feed accumulated state + label +
   sub-label into a final call → the caption. Re-run **K times** for worded
   variants, with **K weighted up for rare classes** to rebalance the dataset:
   `Small_Talk×1, Reading×2, Technical_discussion×3, Eating×5, Study_together×8`.
7. **QC (auto):** an **unconditioned guess** — the model sees a label-free digest
   and guesses the activity; compared to ground truth to set `qc_correct` and a
   **`low_evidence`** flag (wrong guess or low confidence). Confirms the analysis:
   ~96% correct on `Technical_discussion`, near-0 elsewhere → most non-TD rows are
   correctly flagged low-evidence.
8. **Write** the training JSONL + a richer per-session detail JSONL + a run summary.

**Caption prompt rules (enforced):** stable backbone `"This is a <label> session,
characterized by ..."` + variable distinctive layer; evidence = readings only;
no invented numbers; no narrating an activity scene; never name absent sensors —
if thin, say only "signals are sparse".

**Operational features**
- `--concurrency N` — process N sessions in parallel (vLLM continuous-batches them).
- `--base-urls ...` — round-robin across multiple vLLM endpoints; unreachable ones
  are probed and skipped; a retry fails a call over to another endpoint.
- `--resume` — skip sessions already in the detail file and append (crash-safe).
- Robustness: per-request timeout, auto-retry with a bigger budget when a
  thinking response is truncated, and per-call failures are non-fatal (a bad
  segment/variant/QC is skipped, never aborts the session).
- `--max-segments`, `--window-sec`, `--temp-*`, `--k-<class>`,
  `--include-thinking` (store reasoning traces; off by default) are all CLI args.

**The actual production run used:**
`--concurrency 16 --max-segments 8 --window-sec 20 --temp-incremental 0.2
--temp-final 0.7` with the default K multipliers, Qwen3-8B on
`http://localhost:8000/v1`. Result: **696 sessions → 1,490 caption rows, 0 failures.**

Example:
```bash
python3 doore_caption_pipeline.py \
  --concurrency 16 --max-segments 8 --resume \
  --base-urls http://localhost:8000/v1 http://localhost:8001/v1 \
  --out-prefix doore_captions
# quick smoke first:
python3 doore_caption_pipeline.py --smoke --max-segments 8 --out-prefix smoke
# build digests only, no LLM:
python3 doore_caption_pipeline.py --smoke --dry-run
```

---

## 3. Diversity quality-check — `recompute_diversity.py`

Post-hoc QC: within each class, does **caption-embedding distance correlate with
session-feature distance** (grounded variation, not random wording)? Embeds one
caption per session and Spearman-correlates caption distances with feature
distances. Uses `all-MiniLM-L6-v2` for text embedding (jina-clip-v1 emits NaN text
embeddings in this environment). **Output:** `doore_diversity_check.json`.
Result: grounding is weak overall (strongest in Small_Talk), expected given the
heavy template and the sensor-similarity of the confusable classes.

---

## Files in this directory

| File | What |
|---|---|
| `analyze_doore.py` | Stage 1 exploratory analysis |
| `doore_caption_pipeline.py` | Stage 2 batch caption generator |
| `recompute_diversity.py` | Stage 3 diversity QC |
| `make_splits.py` | session-stratified train/val[/test] split (no variant leakage) |
| `doore_session_features.csv` | per-session feature table (analysis) |
| `sensor_availability_matrix.csv` | sensor presence by class |
| `figures/` | analysis plots |
| **`doore_captions.jsonl`** | **the fine-tuning dataset** (1 row per session×variant) |
| `doore_captions_detail.jsonl` | per-session provenance (all variants + digests) |
| `doore_captions_run_summary.json` | run counts, QC summary, output paths |
| `doore_diversity_check.json` | diversity QC results |
| `doore_train.json`, `doore_detail.json` | pretty-printed JSON-array mirrors of the two JSONL files (for browsing) |

See **`OUTPUT_FORMAT.md`** for the exact field schema and usage.
