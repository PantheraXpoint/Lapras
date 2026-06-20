#!/usr/bin/env python3
"""DOORE offline/batch caption-generation pipeline.

Mirrors the LiveCaptionEngine spirit from
  smart_home_gs/scripts/start_3d_visualization_stream.py
(same vLLM client via init_llm_client / generate_response, same
flatten -> summarize -> caption flow) but runs OFFLINE and in BATCH over the
recorded DOORE sessions (no MQTT).

Design (decided, do not change):
  * INTRINSIC captioning: describe each session's own sensor evidence.
  * Label + sub-label are IDENTITY (stated, never used to fabricate evidence).
  * INCREMENTAL/ROLLING aggregation over windows -> macro-segments.
  * Missing sensor = ABSENT (no data), never zero; presence is never evidence.

Flow per session:
  1. Load metadata JSON + sensor CSV; map raw sensor names to a common schema.
  2. Build cheap NUMERIC 20s window digests (no LLM).
  3. Segment the session into <=N macro-segments at the biggest drift points.
  4. Incremental pass: one thinking-mode LLM call per segment, low temp,
     carrying a compact structured running-state object forward.
  5. Final synthesis: K thinking-mode calls at moderate temp -> caption variants.
     K is weighted up for rare classes.
  6. QC: label-free guess on the aggregated digest (+ low_evidence flag);
     diversity check (caption-embedding distance vs feature distance).
  7. Emit a chat/instruction JSONL fine-tuning dataset + a run summary.
"""
import os
import re
import sys
import json
import glob
import argparse
import threading
from collections import defaultdict, OrderedDict

import numpy as np
import pandas as pd

# --- import the shared vLLM client from smart_home_gs (same as the live engine)
_HERE = os.path.dirname(os.path.abspath(__file__))
_SMART_HOME_GS = os.path.abspath(os.path.join(_HERE, "..", "smart_home_gs"))
if _SMART_HOME_GS not in sys.path:
    sys.path.insert(0, _SMART_HOME_GS)
from rag.llm_client import init_llm_client  # noqa: E402

DEFAULT_DATA_ROOT = "/panthera/Lapras/smart_home_gs/doore"
DEFAULT_OUT_DIR = _HERE
DEFAULT_FEATURES_CSV = os.path.join(_HERE, "doore_session_features.csv")

# Class -> default variant multiplier K (rare classes weighted up).
DEFAULT_K = {
    "Small_Talk": 1,
    "Reading": 2,
    "Technical_discussion": 3,
    "Eating": 5,
    "Study_together": 8,
}
CLASS_TO_HUMAN = {
    "Small_Talk": "Small Talk",
    "Reading": "Reading",
    "Technical_discussion": "Technical Discussion",
    "Eating": "Eating",
    "Study_together": "Study Together",
}

# ---------------------------------------------------------------------------
# Sensor schema mapping (confirmed, derived from the actual CSVs)
# ---------------------------------------------------------------------------
CONTINUOUS = {"Sound", "Temperature", "Humidity", "Brightness", "PodiumIR"}
BOOLEAN = {"Motion", "Seat"}                 # True/False
STATE = {"Light", "Aircon", "Projector"}     # On/Off
EVENT = {"Door"}                             # "activate"
GROUP_ORDER = ["Sound", "Motion", "Seat", "PodiumIR", "Door",
               "Temperature", "Humidity", "Brightness", "Light", "Aircon", "Projector"]


def canonical_group(raw_name):
    name = str(raw_name).strip()  # strips the dirty 'Light_2\t' / 'Projector ' variants
    for g in GROUP_ORDER:
        if name == g or name.startswith(g + "_") or name == g:
            return g
    return "Other"


def parse_value(group, raw):
    s = str(raw).strip()
    if group in CONTINUOUS:
        try:
            return float(s)
        except Exception:
            return None
    if group in BOOLEAN:
        if s.lower() in ("true", "1"):
            return 1.0
        if s.lower() in ("false", "0"):
            return 0.0
        return None
    if group in STATE:
        if s.lower() == "on":
            return 1.0
        if s.lower() == "off":
            return 0.0
        return None
    if group in EVENT:
        return 1.0 if s.lower() == "activate" else None
    return None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def discover_sessions(data_root):
    out = []
    for cls in sorted(d for d in os.listdir(data_root)
                      if os.path.isdir(os.path.join(data_root, d))):
        meta_dir = os.path.join(data_root, cls, "metadata")
        sens_dir = os.path.join(data_root, cls, "sensor")
        for mp in sorted(glob.glob(os.path.join(meta_dir, "*.json"))):
            base = os.path.splitext(os.path.basename(mp))[0]
            cp = os.path.join(sens_dir, base + ".csv")
            if os.path.exists(cp):
                out.append({"cls": cls, "session": base, "meta_path": mp, "csv_path": cp})
    return out


def load_session(rec):
    with open(rec["meta_path"]) as f:
        meta = json.load(f)
    df = pd.read_csv(rec["csv_path"])
    df["group"] = df["sensor_name"].map(canonical_group)
    df["raw_name"] = df["sensor_name"].astype(str).str.strip()
    df["val"] = [parse_value(g, v) for g, v in zip(df["group"], df["value"])]
    # session availability: groups whose sensors appear in the metadata list
    avail = OrderedDict()
    meta_sensors = [str(s).strip() for s in meta.get("sensors", [])]
    for g in GROUP_ORDER:
        chans = sorted({s for s in meta_sensors if canonical_group(s) == g})
        # also include channels that actually appear in the CSV (defensive)
        chans_csv = sorted(df.loc[df["group"] == g, "raw_name"].unique().tolist())
        merged = sorted(set(chans) | set(chans_csv))
        if merged:
            avail[g] = merged
    return meta, df, avail


# ---------------------------------------------------------------------------
# Numeric window digests (no LLM)
# ---------------------------------------------------------------------------
def build_windows(df, duration_sec, window_sec):
    if df.empty:
        return []
    t0 = df["timestamp"].min()
    df = df.assign(rel=(df["timestamp"] - t0) / 1000.0)
    total = max(float(duration_sec), float(df["rel"].max()), window_sec)
    n = int(np.ceil(total / window_sec))
    windows = []
    for i in range(n):
        lo, hi = i * window_sec, (i + 1) * window_sec
        w = df[(df["rel"] >= lo) & (df["rel"] < hi)]
        d = {"idx": i, "t_lo": lo, "t_hi": hi, "n_events": len(w)}
        # continuous: mean of present channels
        for g in ("Sound", "Temperature", "Humidity", "Brightness", "PodiumIR"):
            sub = w[(w["group"] == g) & w["val"].notna()]
            d[g] = float(sub["val"].mean()) if len(sub) else None
        # per sound channel
        for ch in ("Sound_L", "Sound_C", "Sound_R", "Sound_P"):
            sub = w[(w["raw_name"] == ch) & w["val"].notna()]
            d[ch] = float(sub["val"].mean()) if len(sub) else None
        # motion: fraction active + zones firing
        mo = w[(w["group"] == "Motion") & w["val"].notna()]
        d["motion_frac"] = float(mo["val"].mean()) if len(mo) else None
        d["motion_zones"] = int(mo.loc[mo["val"] > 0, "raw_name"].nunique()) if len(mo) else None
        # seat: number of distinct seats observed occupied
        se = w[(w["group"] == "Seat") & w["val"].notna()]
        d["seats_occupied"] = int(se.loc[se["val"] > 0, "raw_name"].nunique()) if len(se) else None
        # door events
        d["door_events"] = int((w["group"] == "Door").sum())
        # equipment last observed state this window (None if no reading)
        for g in ("Light", "Aircon", "Projector"):
            sub = w[(w["group"] == g) & w["val"].notna()]
            d[g] = (float(sub.sort_values("rel")["val"].iloc[-1]) if len(sub) else None)
        windows.append(d)
    # forward-fill equipment state (carry last known across windows; None until first seen)
    for g in ("Light", "Aircon", "Projector"):
        last = None
        for d in windows:
            if d[g] is not None:
                last = d[g]
            d[g + "_state"] = last  # carried-forward state (may stay None if never reported)
    return windows


def _window_vector(w, fallback):
    """Numeric vector for drift-based segmentation only (NOT evidence)."""
    def g(key):
        v = w.get(key)
        return fallback.get(key, 0.0) if v is None else v
    return np.array([
        g("Sound"), g("motion_frac"), g("seats_occupied"), g("PodiumIR"),
        w.get("door_events", 0) or 0, g("Temperature"), g("Humidity"), g("Brightness"),
        (w.get("Projector_state") or 0.0), (w.get("Light_state") or 0.0),
        (w.get("Aircon_state") or 0.0),
    ], dtype=float)


def segment_windows(windows, max_segments):
    """Adaptive: put segment boundaries at the largest drift points, capped."""
    n = len(windows)
    if n == 0:
        return []
    if n <= max_segments:
        return [(i, i + 1) for i in range(n)]
    # session-mean fallback per key
    keys = ["Sound", "motion_frac", "seats_occupied", "PodiumIR", "Temperature",
            "Humidity", "Brightness"]
    fallback = {}
    for k in keys:
        vals = [w[k] for w in windows if w.get(k) is not None]
        fallback[k] = float(np.mean(vals)) if vals else 0.0
    V = np.vstack([_window_vector(w, fallback) for w in windows])
    mu, sd = V.mean(0), V.std(0)
    sd[sd == 0] = 1.0
    Z = (V - mu) / sd
    jumps = np.linalg.norm(np.diff(Z, axis=0), axis=1)  # length n-1
    # pick the (max_segments-1) largest jumps as boundaries
    k = max_segments - 1
    bnd = sorted(np.argsort(jumps)[-k:] + 1)  # boundary index = start of new segment
    segs, start = [], 0
    for b in bnd:
        segs.append((start, b))
        start = b
    segs.append((start, n))
    return segs


# ---------------------------------------------------------------------------
# Segment digest rendering + running state
# ---------------------------------------------------------------------------
def _fmt(v, nd=1):
    return "absent" if v is None else f"{v:.{nd}f}"


def availability_text(avail):
    parts = []
    for g, chans in avail.items():
        if g == "Sound":
            ch = ",".join(c.split("_")[-1] for c in chans)
            parts.append(f"sound({ch})")
        elif g in ("Motion", "Seat"):
            parts.append(f"{g.lower()}({len(chans)})")
        else:
            parts.append(g.lower())
    return ", ".join(parts) if parts else "none"


def summarize_segment(windows_sub, avail):
    """Aggregate a list of windows into a numeric digest dict."""
    def mean_present(key):
        vals = [w[key] for w in windows_sub if w.get(key) is not None]
        return float(np.mean(vals)) if vals else None

    d = {
        "Sound_L": mean_present("Sound_L"), "Sound_C": mean_present("Sound_C"),
        "Sound_R": mean_present("Sound_R"), "Sound_P": mean_present("Sound_P"),
        "motion_frac": mean_present("motion_frac"),
        "motion_zones": (max([w["motion_zones"] for w in windows_sub
                              if w.get("motion_zones") is not None], default=None)),
        "seats_occupied": (max([w["seats_occupied"] for w in windows_sub
                                if w.get("seats_occupied") is not None], default=None)),
        "PodiumIR": mean_present("PodiumIR"),
        "door_events": int(sum(w.get("door_events", 0) for w in windows_sub)),
        "Temperature": mean_present("Temperature"),
        "Humidity": mean_present("Humidity"),
        "Brightness": mean_present("Brightness"),
    }
    # equipment carried state at end of segment
    for g in ("Projector", "Light", "Aircon"):
        st = None
        for w in windows_sub:
            if w.get(g + "_state") is not None:
                st = w[g + "_state"]
        d[g] = st
    d["t_lo"] = windows_sub[0]["t_lo"]
    d["t_hi"] = windows_sub[-1]["t_hi"]
    return d


def _state_word(v):
    if v is None:
        return "no reading"
    return "ON" if v >= 0.5 else "off"


def render_segment_text(seg, idx, total, avail):
    L = []
    L.append(f"Segment {idx+1}/{total}  (t={seg['t_lo']:.0f}-{seg['t_hi']:.0f}s)")
    L.append(f"  Sensors present this session: {availability_text(avail)}")
    snd = []
    for ch, lab in (("Sound_C", "C"), ("Sound_R", "R"), ("Sound_P", "podium"), ("Sound_L", "L")):
        snd.append(f"{lab}={_fmt(seg[ch])}")
    L.append("  Sound (mean level by channel): " + ", ".join(snd))
    if seg["motion_frac"] is None:
        L.append("  Motion: absent (no data)")
    else:
        L.append(f"  Motion: {seg['motion_frac']*100:.0f}% of readings active across "
                 f"{seg['motion_zones']} zone(s)")
    L.append(f"  Seats occupied: {'absent' if seg['seats_occupied'] is None else seg['seats_occupied']}")
    L.append(f"  PodiumIR: {_fmt(seg['PodiumIR'])}")
    L.append(f"  Door: {seg['door_events']} activation(s)")
    L.append(f"  Equipment: projector {_state_word(seg['Projector'])}, "
             f"lights {_state_word(seg['Light'])}, AC {_state_word(seg['Aircon'])}")
    L.append(f"  Temperature: {_fmt(seg['Temperature'])}  Humidity: {_fmt(seg['Humidity'])}  "
             f"Brightness: {_fmt(seg['Brightness'])}")
    return "\n".join(L)


def init_running_state(meta):
    return {
        "avg_humans": meta.get("avg_n_human"),
        "peak_seats": None,
        "peak_motion_zones": None,
        "equipment": {"Projector": None, "Light": None, "Aircon": None},
        "door_total": 0,
        "sound_running": {"Sound_L": [], "Sound_C": [], "Sound_R": [], "Sound_P": []},
        "n_segments_seen": 0,
        "understanding": "",  # latest LLM running-understanding paragraph (bounded)
    }


def update_running_state(state, seg):
    if seg["seats_occupied"] is not None:
        state["peak_seats"] = max(seg["seats_occupied"], state["peak_seats"] or 0)
    if seg["motion_zones"] is not None:
        state["peak_motion_zones"] = max(seg["motion_zones"], state["peak_motion_zones"] or 0)
    for g in ("Projector", "Light", "Aircon"):
        if seg[g] is not None:
            state["equipment"][g] = seg[g]
    state["door_total"] += seg["door_events"]
    for ch in state["sound_running"]:
        if seg[ch] is not None:
            state["sound_running"][ch].append(seg[ch])
    state["n_segments_seen"] += 1
    return state


def render_running_state_text(state):
    eq = state["equipment"]
    snd = []
    for ch, lab in (("Sound_C", "C"), ("Sound_R", "R"), ("Sound_P", "podium"), ("Sound_L", "L")):
        vals = state["sound_running"][ch]
        snd.append(f"{lab}={_fmt(np.mean(vals)) if vals else 'absent'}")
    L = [
        f"Cumulative context (through segment {state['n_segments_seen']}):",
        f"  Session avg occupancy (metadata anchor): {state['avg_humans']}",
        f"  Peak seats occupied so far: {state['peak_seats']}  "
        f"Peak motion zones: {state['peak_motion_zones']}",
        f"  Equipment state: projector {_state_word(eq['Projector'])}, "
        f"lights {_state_word(eq['Light'])}, AC {_state_word(eq['Aircon'])}",
        f"  Door activations so far: {state['door_total']}",
        f"  Sound running mean by channel: " + ", ".join(snd),
    ]
    if state["understanding"]:
        L.append("  Running understanding: " + state["understanding"])
    return "\n".join(L)


def render_session_aggregate(windows, avail, meta):
    """Whole-session digest used for final synthesis."""
    seg = summarize_segment(windows, avail)
    seg["t_lo"], seg["t_hi"] = 0, (windows[-1]["t_hi"] if windows else 0)
    return render_segment_text(seg, 0, 1, avail).replace("Segment 1/1", "Whole-session aggregate")


def article_for(label):
    return "an" if label[:1].lower() in "aeiou" else "a"


def backbone_for(label):
    return f"This is {article_for(label)} {label} session"


def compute_evidence_strength(windows, avail):
    """Auto-assess how much describable signal THIS session's own sensors carry.
    Used to drive hedging — NOT the class label (intrinsic, not contrastive)."""
    seats = [w["seats_occupied"] for w in windows if w.get("seats_occupied") is not None]
    motion = [w["motion_frac"] for w in windows if w.get("motion_frac") is not None]
    snd = [w["Sound"] for w in windows if w.get("Sound") is not None]
    peak_seats = max(seats) if seats else 0
    motion_active = float(np.mean(motion)) if motion else 0.0
    snd_range = (max(snd) - min(snd)) if len(snd) >= 2 else 0.0
    proj_on = any((w.get("Projector_state") or 0) >= 0.5 for w in windows)
    any_equip_on = any((w.get(g + "_state") or 0) >= 0.5
                       for g in ("Projector", "Light", "Aircon") for w in windows)
    door_total = sum(w.get("door_events", 0) for w in windows)
    coverage = len(avail) / 11.0

    strong = []
    if proj_on:
        strong.append("projector active")
    if peak_seats >= 3:
        strong.append(f"multi-seat occupancy (peak {peak_seats})")
    if motion_active >= 0.4:
        strong.append("sustained motion activity")
    if snd_range >= 12:
        strong.append("dynamic sound (clear level changes)")
    if door_total >= 10:
        strong.append(f"heavy door traffic ({door_total})")

    weak = (peak_seats <= 1) and (motion_active < 0.15) and (not any_equip_on)
    if len(strong) >= 2:
        return "strong", strong
    if weak or (len(strong) == 0 and coverage < 0.6):
        return "weak", ["low/absent occupancy, no distinctive equipment, and flat sound — "
                        "sensors do not clearly indicate any specific activity"]
    return "moderate", (strong or ["some signal present but no strong discriminator"])


def render_qc_digest(windows, avail):
    """Richer label-free digest for the unconditioned-guess QC.
    Deliberately EXCLUDES avg_humans (metadata leaks the class)."""
    half = max(len(windows) // 2, 1)

    def mean_present(key, ws):
        vals = [w[key] for w in ws if w.get(key) is not None]
        return float(np.mean(vals)) if vals else None

    seats = [w["seats_occupied"] for w in windows if w.get("seats_occupied") is not None]
    zones = [w["motion_zones"] for w in windows if w.get("motion_zones") is not None]
    proj = any((w.get("Projector_state") or 0) >= 0.5 for w in windows)
    light = any((w.get("Light_state") or 0) >= 0.5 for w in windows)
    ac = any((w.get("Aircon_state") or 0) >= 0.5 for w in windows)
    door = sum(w.get("door_events", 0) for w in windows)
    L = [
        "Aggregated sensor evidence (NO activity label given):",
        f"  Sensors present: {availability_text(avail)}",
        f"  Peak seats occupied: {max(seats) if seats else 'absent'}; "
        f"peak motion zones firing: {max(zones) if zones else 'absent'}",
        f"  Motion active fraction: first half {_fmt(mean_present('motion_frac', windows[:half]))} "
        f"-> second half {_fmt(mean_present('motion_frac', windows[half:]))}",
        f"  Sound overall mean: first half {_fmt(mean_present('Sound', windows[:half]))} "
        f"-> second half {_fmt(mean_present('Sound', windows[half:]))}",
        f"  Sound by channel mean: C={_fmt(mean_present('Sound_C', windows))}, "
        f"R={_fmt(mean_present('Sound_R', windows))}, podium={_fmt(mean_present('Sound_P', windows))}, "
        f"L={_fmt(mean_present('Sound_L', windows))}",
        f"  PodiumIR mean: {_fmt(mean_present('PodiumIR', windows))}",
        f"  Door activations total: {door}",
        f"  Equipment ever ON: projector={'yes' if proj else 'no'}, "
        f"lights={'yes' if light else 'no'}, AC={'yes' if ac else 'no'}",
        f"  Temperature {_fmt(mean_present('Temperature', windows))}, "
        f"Humidity {_fmt(mean_present('Humidity', windows))}, "
        f"Brightness {_fmt(mean_present('Brightness', windows))}",
    ]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a smart-room monitoring assistant writing an expressive, evidence-grounded
caption for ONE recording session of a known activity. Thinking is enabled - reason
before you write.

YOU ARE GIVEN
- Activity label and sub-label: the session's IDENTITY. Treat as given fact, not
  something to prove.
- Cumulative context so far: running people count, equipment state (projector/
  lights/AC), recent motion/door activity, and how the scene has evolved.
- Current window digest: this window's sensor readings (sound per channel L/C/R/
  podium, motion/seat, podium IR, door, temperature, humidity, brightness, equipment).
- Sensor availability: which sensors are PRESENT vs ABSENT in this session.

REASON, THEN WRITE
1. Think step by step about what THIS window's sensors actually show, and how it
   updates the running picture (more/fewer people, equipment toggled, scene calmer
   or livelier, drift since earlier).
2. Update the caption describing the session's specific, observable evidence:
   occupancy level, sound level and spatial pattern, motion/seat activity, door
   events, equipment state - and how these change over the session.
3. Capture what makes THIS session distinctive (crowd size, quiet vs lively,
   projector on, frequent door activity). This distinctiveness is the point.

HARD RULES
- The label is identity, NOT a mandate. NEVER fabricate activity-specific evidence
  to "prove" the label. If signals are sparse or ambiguous (common for quiet
  activities), describe what is actually observable and HEDGE: "signals are sparse
  but consistent with...".
- Evidence = sensor READINGS only. NEVER treat a sensor's mere presence or absence
  as evidence of the activity. ABSENT means no data, not zero.
- Ground every statement in the digest. No invented numbers.
- Keep a stable backbone ("This is a <label> session...") plus a variable
  distinctive layer ("...characterized by...") so class identity stays constant
  while the descriptive detail varies per session.

OUTPUT
One concise paragraph, optionally followed by structured fields:
occupancy, sound, motion, equipment, distinctive_features, confidence."""

# Enforcement shared by both passes (does NOT modify the user's SYSTEM prompt).
ENFORCEMENT = """
ENFORCEMENT (in addition to the hard rules above):
- Describe ONLY sensor readings. Do NOT narrate or assert that the activity is
  happening, and do NOT invent an activity scene (forbidden: "a dining scenario",
  "a focused meeting", "people are eating/reading", "students studying"). State the
  label once as identity, then describe sensors.
- Your confidence MUST match the provided Evidence strength. If it is "weak", you
  MUST hedge ("signals are sparse but consistent with...") and set confidence="low".
  If "moderate", confidence is at most "medium". Only "strong" allows "high".
- NEVER name or list which sensors are absent / "no reading" / "not recorded", and
  never describe sensor coverage as a feature (forbidden: "motion data is absent",
  "seat occupancy not recorded", "equipment shows no readings", "intermittent sensor
  gaps", "full-sensor coverage"). Which sensors are deployed is instrumentation, not
  evidence about the room or activity. If evidence is thin, say only that "signals are
  sparse" WITHOUT naming what is missing; describe only sensors that DID report."""

INCREMENTAL_SUFFIX = """
---
This is an INCREMENTAL step (not the final caption). Briefly reason about this
segment, then output ONLY a short updated "running understanding" paragraph (2-4
sentences) integrating this segment into the picture so far. Hedge if the evidence is
weak. Do not list structured fields yet.""" + ENFORCEMENT

FINAL_SUFFIX = """
---
This is the FINAL synthesis. Using the full cumulative context and the whole-session
aggregate above, write the definitive session caption now: ONE concise paragraph that
begins with EXACTLY this opening (verbatim, correct grammar): "{backbone}" followed by
", characterized by ..." with the variable distinctive layer. Then on a new line output:
FIELDS_JSON: {{"occupancy": "...", "sound": "...", "motion": "...", "equipment": "...", "distinctive_features": "...", "confidence": "high|medium|low"}}""" + ENFORCEMENT

QC_SYSTEM = """You are a smart-room sensor analyst. You are given an aggregated sensor digest from
ONE recording session, with NO activity label. Candidate activities are EXACTLY these
five: Eating, Reading, Small Talk, Study Together, Technical Discussion.
Reason briefly from the readings, then COMMIT to the single most likely activity — you
MUST pick exactly one of the five, even when unsure (do not answer "uncertain").
Express how sure you are via CONFIDENCE. Evidence = readings only; absent means no
data, not zero.
Output exactly:
GUESS: <one of the five candidates>
CONFIDENCE: <high|medium|low>"""


# ---------------------------------------------------------------------------
# LLM wrapper
# ---------------------------------------------------------------------------
THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _endpoint_alive(base_url, timeout=15.0, attempts=3):
    """Probe /models with retries. Servers may be saturated, so a slow response
    is not 'dead' — only a refused connection / persistent failure is."""
    import time
    import urllib.request
    url = base_url.rstrip("/") + "/models"
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                if r.status == 200:
                    return True
        except Exception:
            if i < attempts - 1:
                time.sleep(2)
    return False


class LLM:
    """Round-robins requests across one or more vLLM endpoints. Unreachable
    endpoints are dropped at startup; a retry fails the call over to the next
    endpoint, which also spreads load across the servers."""

    def __init__(self, base_urls, model, timeout):
        self.clients, self.endpoints = [], []
        for url in base_urls:
            if _endpoint_alive(url):
                # thinking ON for this pipeline (we parse <think> traces out of content)
                self.clients.append(init_llm_client(model=model, base_url=url,
                                                    timeout=timeout, enable_thinking=True))
                self.endpoints.append(url)
                print(f"  [endpoint OK] {url}")
            else:
                print(f"  [endpoint UNREACHABLE, skipping] {url}")
        if not self.clients:
            raise RuntimeError("No reachable vLLM endpoints: " + ", ".join(base_urls))
        self._rr = 0
        self._rr_lock = threading.Lock()

    def _next_client(self):
        with self._rr_lock:
            i = self._rr
            self._rr += 1
        return self.clients[i % len(self.clients)]

    def complete(self, system, user, temperature, max_tokens):
        """Call the model; auto-retry once with a bigger budget if the response
        was truncated inside the <think> block (no answer produced). Never raises:
        on a terminal error (e.g. timeout) returns ("", "") so the caller can
        skip just this call instead of aborting the whole session."""
        prompt = f"{system}\n\n{user}"
        budget, answer, thinking = max_tokens, "", ""
        for _ in range(2):
            client = self._next_client()  # round-robin across endpoints
            try:
                raw = client.generate_response(
                    {"text": prompt}, max_new_tokens=budget, temperature=temperature) or ""
            except Exception as e:
                print(f"      [llm call failed: {e}; retrying]", flush=True)
                raw = ""
            m = THINK_RE.search(raw)
            if m:                                   # complete <think>...</think>
                thinking = m.group(1).strip()
                answer = THINK_RE.sub("", raw).strip()
            elif "<think>" in raw and "</think>" not in raw:   # truncated mid-think
                thinking = raw.split("<think>", 1)[1].strip()
                answer = ""
            else:                                   # no think block
                thinking, answer = "", raw.strip()
            if answer:
                return answer, thinking
            budget = min(budget * 2, 8000)          # truncated/failed -> retry larger
        return answer, thinking


def parse_fields(answer):
    m = re.search(r"FIELDS_JSON:\s*(\{.*\})", answer, re.DOTALL)
    fields, conf = None, None
    paragraph = answer
    if m:
        paragraph = answer[:m.start()].strip()
        try:
            fields = json.loads(m.group(1))
            conf = fields.get("confidence")
        except Exception:
            fields = {"_raw": m.group(1)}
    return paragraph, fields, conf


# ---------------------------------------------------------------------------
# Per-session generation
# ---------------------------------------------------------------------------
def caption_session(llm, rec, args):
    meta, df, avail = load_session(rec)
    label = CLASS_TO_HUMAN.get(rec["cls"], rec["cls"])
    sub = meta.get("label", label)
    duration = meta.get("duration", 0)

    windows = build_windows(df, duration, args.window_sec)
    segs = segment_windows(windows, args.max_segments)
    state = init_running_state(meta)
    seg_digests = [summarize_segment(windows[a:b], avail) for (a, b) in segs]

    ev_level, ev_reasons = compute_evidence_strength(windows, avail)
    ev_line = (f"Evidence strength (auto-assessed from THIS session's sensors): "
               f"{ev_level} — {'; '.join(ev_reasons)}.")

    traces = []
    # ---- incremental pass (one call per macro-segment) ----
    for i, seg in enumerate(seg_digests):
        update_running_state(state, seg)
        ctx = render_running_state_text(state)
        digest = render_segment_text(seg, i, len(seg_digests), avail)
        user = (f"Activity label: {label}\nSub-label: {sub}\n{ev_line}\n\n{ctx}\n\n"
                f"Current segment digest:\n{digest}\n{INCREMENTAL_SUFFIX}")
        ans, think = llm.complete(SYSTEM_PROMPT, user, args.temp_incremental,
                                  args.max_tokens_incremental)
        if ans:  # skip a failed/empty segment; keep prior understanding
            state["understanding"] = ans.strip()[:1200]  # bounded carry-forward
        if args.include_thinking:
            traces.append({"segment": i, "thinking": think})

    # ---- final synthesis (K variants) ----
    agg = render_session_aggregate(windows, avail, meta)
    final_ctx = render_running_state_text(state)
    final_user = (f"Activity label: {label}\nSub-label: {sub}\n{ev_line}\n\n{final_ctx}\n\n"
                  f"Whole-session aggregate digest:\n{agg}\n"
                  + FINAL_SUFFIX.format(backbone=backbone_for(label)))
    variants = []
    for k in range(args.k_map.get(rec["cls"], 1)):
        ans, think = llm.complete(SYSTEM_PROMPT, final_user, args.temp_final,
                                  args.max_tokens_final)
        if not ans:  # skip a failed variant rather than emitting an empty caption
            print(f"      [variant {k} empty; skipped]", flush=True)
            continue
        paragraph, fields, conf = parse_fields(ans)
        variants.append({"variant_idx": k, "caption": paragraph,
                         "fields": fields, "confidence": conf,
                         "thinking": think if args.include_thinking else None})

    return {
        "session": rec["session"], "class": rec["cls"], "label": label,
        "sub_label": sub, "duration_sec": duration,
        "avg_humans": meta.get("avg_n_human"),
        "n_windows": len(windows), "n_segments": len(seg_digests),
        "sensors_present": list(avail.keys()),
        "evidence_strength": ev_level, "evidence_reasons": ev_reasons,
        "session_aggregate_digest": agg,
        "qc_digest": render_qc_digest(windows, avail),
        "variants": variants,
        "incremental_traces": traces if args.include_thinking else None,
    }


QC_CANDIDATES = ["Technical Discussion", "Small Talk", "Study Together", "Eating", "Reading"]


def qc_unconditioned(llm, sess_result, true_human_label):
    ans, think = llm.complete(QC_SYSTEM, sess_result["qc_digest"],
                              temperature=0.2, max_tokens=1024)
    gm = re.search(r"GUESS:\s*(.+)", ans)
    cm = re.search(r"CONFIDENCE:\s*(\w+)", ans)
    guess = gm.group(1).strip() if gm else ""
    conf = cm.group(1).strip().lower() if cm else None
    if not guess:  # fallback: scan answer (then thinking) for a candidate name
        for src in (ans, think):
            for cand in QC_CANDIDATES:
                if cand.lower() in (src or "").lower():
                    guess = cand
                    break
            if guess:
                break
    norm = guess.strip().lower().rstrip(".")
    correct = (norm == true_human_label.lower())
    # forced-guess QC: a wrong guess OR a low-confidence guess marks low evidence
    low_evidence = (not correct) or (conf == "low")
    return {"guess": guess, "confidence": conf, "correct": correct,
            "low_evidence": low_evidence}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def to_training_rows(sess, qc, features_ref, include_thinking):
    rows = []
    user_msg = (f"Sensor session digest for a smart-room recording:\n"
                f"{sess['session_aggregate_digest']}\n\n"
                f"Activity: {sess['label']} (sub-label: {sess['sub_label']}).\n"
                f"Write an expressive, evidence-grounded caption of this session.")
    for v in sess["variants"]:
        row = {
            "session": sess["session"], "class": sess["class"],
            "label": sess["label"], "sub_label": sess["sub_label"],
            "variant_idx": v["variant_idx"],
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": v["caption"]},
            ],
            "fields": v["fields"],
            "confidence": v["confidence"],
            "evidence_strength": sess.get("evidence_strength"),
            "qc_unconditioned_guess": qc["guess"] if qc else None,
            "qc_correct": qc["correct"] if qc else None,
            "low_evidence": qc["low_evidence"] if qc else None,
            "feature_ref": features_ref,  # traceability to digest/feature vector
        }
        if include_thinking:
            row["thinking_trace"] = v["thinking"]
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description="DOORE offline batch caption pipeline")
    ap.add_argument("--data-root", default=DEFAULT_DATA_ROOT)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--features-csv", default=DEFAULT_FEATURES_CSV)
    ap.add_argument("--out-prefix", default="doore_captions")
    ap.add_argument("--window-sec", type=int, default=20)
    ap.add_argument("--max-segments", type=int, default=30)
    ap.add_argument("--temp-incremental", type=float, default=0.2)
    ap.add_argument("--temp-final", type=float, default=0.7)
    ap.add_argument("--max-tokens-incremental", type=int, default=1536)
    ap.add_argument("--max-tokens-final", type=int, default=2560)
    ap.add_argument("--include-thinking", action="store_true",
                    help="Store thinking traces (excluded from training target by default)")
    ap.add_argument("--base-urls", nargs="+", default=None,
                    help="One or more vLLM OpenAI base URLs to round-robin across. "
                         "Default: localhost 8000 and 8001 (unreachable ones are skipped).")
    ap.add_argument("--base-url", default="http://localhost:8000/v1",
                    help="(legacy single-endpoint; --base-urls takes precedence)")
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--timeout", type=float, default=300.0)
    # per-class K overrides
    for cls, k in DEFAULT_K.items():
        ap.add_argument(f"--k-{cls.lower()}", type=int, default=k)
    # selection
    ap.add_argument("--classes", nargs="*", default=None, help="restrict to these class folders")
    ap.add_argument("--sample-per-class", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--smoke", action="store_true",
                    help="Small batch: 3 Technical_discussion + 3 Eating sessions")
    ap.add_argument("--no-qc", action="store_true")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="Number of sessions processed in parallel (vLLM batches them "
                         "on-GPU). 6-8 is a good range; 1 = sequential.")
    ap.add_argument("--resume", action="store_true",
                    help="Skip sessions already present in the existing _detail.jsonl "
                         "and append, instead of overwriting from scratch.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build digests/segments and print them; no LLM calls")
    args = ap.parse_args()

    args.k_map = {cls: getattr(args, f"k_{cls.lower()}") for cls in DEFAULT_K}

    sessions = discover_sessions(args.data_root)
    by_class = defaultdict(list)
    for s in sessions:
        by_class[s["cls"]].append(s)

    # selection
    if args.smoke:
        picked = by_class["Technical_discussion"][:3] + by_class["Eating"][:3]
    else:
        picked = []
        classes = args.classes or sorted(by_class)
        for c in classes:
            lst = by_class.get(c, [])
            picked += lst[:args.sample_per_class] if args.sample_per_class else lst
    if args.limit:
        picked = picked[:args.limit]

    print(f"Sessions discovered: {len(sessions)} | selected: {len(picked)}")
    print("Selected by class:", dict(Counter(s['cls'] for s in picked)))
    print("Variant multipliers (K):", args.k_map)
    print(f"window={args.window_sec}s max_segments={args.max_segments} "
          f"temps(inc/final)={args.temp_incremental}/{args.temp_final} "
          f"thinking={'on' if args.include_thinking else 'off'} dry_run={args.dry_run}")

    if args.dry_run:
        for rec in picked:
            meta, df, avail = load_session(rec)
            windows = build_windows(df, meta.get("duration", 0), args.window_sec)
            segs = segment_windows(windows, args.max_segments)
            print(f"\n### {rec['cls']}/{rec['session']}  dur={meta.get('duration')}s "
                  f"avg_humans={meta.get('avg_n_human')}  windows={len(windows)} "
                  f"segments={len(segs)}  present={list(avail.keys())}")
            for i, (a, b) in enumerate(segs[:3]):
                print(render_segment_text(summarize_segment(windows[a:b], avail), i, len(segs), avail))
        print("\n[dry-run] no LLM calls made.")
        return

    base_urls = args.base_urls or ["http://localhost:8000/v1", "http://localhost:8001/v1"]
    print("Probing vLLM endpoints:")
    llm = LLM(base_urls, args.model, args.timeout)
    print(f"Using {len(llm.endpoints)} endpoint(s): {llm.endpoints}")

    out_jsonl = os.path.join(args.out_dir, args.out_prefix + ".jsonl")
    detail_jsonl = os.path.join(args.out_dir, args.out_prefix + "_detail.jsonl")

    # --resume: skip sessions already in the detail file, append instead of overwrite
    file_mode = "w"
    if args.resume and os.path.exists(detail_jsonl):
        done_keys = set()
        for line in open(detail_jsonl):
            try:
                d = json.loads(line)
                done_keys.add(f"{d['class']}/{d['session']}")
            except Exception:
                pass
        before = len(picked)
        picked = [r for r in picked if f"{r['cls']}/{r['session']}" not in done_keys]
        file_mode = "a"
        print(f"[resume] {len(done_keys)} sessions already done; "
              f"{len(picked)}/{before} remaining")

    n_rows = 0
    qc_records = []
    per_class_rows = Counter()
    write_lock = threading.Lock()
    counters = {"done": 0, "rows": 0}
    total = len(picked)

    def process_one(rec):
        """Worker: full caption + QC for one session (its own LLM calls are
        sequential; concurrency is ACROSS sessions, which vLLM batches)."""
        sess = caption_session(llm, rec, args)
        qc = None if args.no_qc else qc_unconditioned(llm, sess, sess["label"])
        return rec, sess, qc

    def emit(rec, sess, qc, ftrain, fdet):
        nonlocal n_rows
        feat_ref = {"features_csv": os.path.basename(args.features_csv),
                    "session_key": rec["session"]}
        rows = to_training_rows(sess, qc, feat_ref, args.include_thinking)
        with write_lock:
            counters["done"] += 1
            done = counters["done"]
            if qc:
                qc_records.append({"session": rec["session"], "true": sess["label"], **qc})
            for r in rows:
                ftrain.write(json.dumps(r, ensure_ascii=False) + "\n")
                per_class_rows[rec["cls"]] += 1
                n_rows += 1
            fdet.write(json.dumps(sess, ensure_ascii=False) + "\n")
            ftrain.flush(); fdet.flush()
        qctxt = (f" QC={qc['guess']} correct={qc['correct']} low_ev={qc['low_evidence']}"
                 if qc else "")
        print(f"[{done}/{total}] {rec['cls']}/{rec['session']} "
              f"ev={sess.get('evidence_strength')} variants={len(sess['variants'])}{qctxt}",
              flush=True)

    with open(out_jsonl, file_mode) as ftrain, open(detail_jsonl, file_mode) as fdet:
        if args.concurrency > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            print(f"Running with concurrency={args.concurrency}", flush=True)
            with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
                futs = {ex.submit(process_one, rec): rec for rec in picked}
                for fut in as_completed(futs):
                    rec = futs[fut]
                    try:
                        _, sess, qc = fut.result()
                    except Exception as e:
                        with write_lock:
                            counters["done"] += 1
                            print(f"[{counters['done']}/{total}] "
                                  f"{rec['cls']}/{rec['session']} FAILED: {e}", flush=True)
                        continue
                    emit(rec, sess, qc, ftrain, fdet)
        else:
            for rec in picked:
                try:
                    _, sess, qc = process_one(rec)
                except Exception as e:
                    with write_lock:
                        counters["done"] += 1
                        print(f"[{counters['done']}/{total}] "
                              f"{rec['cls']}/{rec['session']} FAILED: {e}", flush=True)
                    continue
                emit(rec, sess, qc, ftrain, fdet)

    # ---- diversity QC ----
    diversity = run_diversity_check(out_jsonl, args.features_csv)

    summary = {
        "selected_sessions": len(picked),
        "per_class_selected": dict(Counter(s["cls"] for s in picked)),
        "variant_multipliers": args.k_map,
        "training_rows": n_rows,
        "per_class_rows": dict(per_class_rows),
        "qc_unconditioned": summarize_qc(qc_records),
        "diversity_check": diversity,
        "outputs": {"train_jsonl": out_jsonl, "detail_jsonl": detail_jsonl},
    }
    with open(os.path.join(args.out_dir, args.out_prefix + "_run_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print("\n==== RUN SUMMARY ====")
    print(json.dumps(summary, indent=2))


def summarize_qc(qc_records):
    if not qc_records:
        return None
    by_class = defaultdict(lambda: {"n": 0, "correct": 0, "low_evidence": 0})
    for q in qc_records:
        c = by_class[q["true"]]
        c["n"] += 1
        c["correct"] += int(q["correct"])
        c["low_evidence"] += int(q["low_evidence"])
    return {k: v for k, v in by_class.items()}


def run_diversity_check(jsonl_path, features_csv):
    """Within each class, does caption-embedding distance correlate with
    session-feature distance? Skipped gracefully if deps/data insufficient."""
    try:
        from sentence_transformers import SentenceTransformer
        from scipy.spatial.distance import pdist
        from scipy.stats import spearmanr
    except Exception as e:
        return {"status": f"skipped (missing deps: {e})"}
    if not os.path.exists(features_csv):
        return {"status": "skipped (no features csv)"}
    rows = [json.loads(l) for l in open(jsonl_path)]
    # one caption per session (variant 0) for a clean per-session comparison
    per_sess = {}
    for r in rows:
        if r["variant_idx"] == 0:
            per_sess[r["session"]] = r
    feats = pd.read_csv(features_csv).set_index("session")
    by_class = defaultdict(list)
    for s, r in per_sess.items():
        if s in feats.index:
            by_class[r["class"]].append((s, r["messages"][-1]["content"]))
    try:
        model = SentenceTransformer("jinaai/jina-clip-v1", trust_remote_code=True)
    except Exception as e:
        return {"status": f"skipped (embedder load failed: {e})"}
    num_cols = feats.select_dtypes("number").columns
    fmat = feats[num_cols].fillna(feats[num_cols].median())
    result = {}
    for cls, items in by_class.items():
        if len(items) < 5:
            result[cls] = {"n": len(items), "status": "too few for correlation"}
            continue
        sess_ids = [s for s, _ in items]
        caps = [c for _, c in items]
        emb = model.encode(caps, normalize_embeddings=True)
        F = ((fmat.loc[sess_ids] - fmat.loc[sess_ids].mean()) /
             (fmat.loc[sess_ids].std() + 1e-9)).values
        d_cap = pdist(emb, metric="cosine")
        d_feat = pdist(F, metric="euclidean")
        rho, p = spearmanr(d_cap, d_feat)
        result[cls] = {"n": len(items), "spearman_rho": round(float(rho), 3),
                       "p_value": round(float(p), 4)}
    return result


from collections import Counter  # noqa: E402 (placed late to keep header tidy)

if __name__ == "__main__":
    main()
