#!/usr/bin/env python3
"""
Offline sensor-window captioning from dashboard-agent logged stream.

Input: JSON produced by sensor_stream_logger.py
Output: Pretty JSON with one-paragraph caption for each 30s window.
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Ensure repo root is importable so `llms` can be resolved.
# This script lives at: <repo>/Lapras/smart_home_gs/sensor_caption_infer.py
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if REPO_ROOT not in sys.path:
    sys.path.append(REPO_ROOT)

from llms.init_model import init_model  # noqa: E402


DEFAULT_MODEL = "qwenlm"
DEFAULT_BROKER = "143.248.55.82"
DEFAULT_WINDOW_SEC = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_logged_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_sensor_status(sensor: Dict[str, Any]) -> Optional[str]:
    sensor_type = sensor.get("sensor_type")
    metadata = sensor.get("metadata", {}) or {}

    if sensor_type in {"infrared", "distance"}:
        return metadata.get("proximity_status")
    if sensor_type == "motion":
        return metadata.get("motion_status")
    if sensor_type == "activity":
        return metadata.get("activity_status")
    if sensor_type == "temperature":
        return metadata.get("temperature_status")
    if sensor_type == "door":
        return metadata.get("door_status")
    if sensor_type == "light":
        return metadata.get("light_status")
    if sensor_type == "tilt":
        return metadata.get("tilt_status")
    return None


def build_windows(records: List[Dict[str, Any]], window_sec: int) -> List[Tuple[float, float, List[Dict[str, Any]]]]:
    valid = [r for r in records if isinstance(r.get("received_at_unix"), (int, float))]
    if not valid:
        return []

    valid.sort(key=lambda x: x["received_at_unix"])
    start_ts = valid[0]["received_at_unix"]
    end_ts = valid[-1]["received_at_unix"]

    windows = []
    cursor = start_ts
    while cursor <= end_ts:
        w_end = cursor + window_sec
        chunk = [r for r in valid if cursor <= r["received_at_unix"] < w_end]
        windows.append((cursor, w_end, chunk))
        cursor = w_end
    return windows


def summarize_window_records(window_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    latest_sensor: Dict[str, Dict[str, Any]] = {}
    status_change_events: List[Dict[str, Any]] = []
    per_sensor_last_status: Dict[str, Optional[str]] = {}

    for rec in sorted(window_records, key=lambda x: x["received_at_unix"]):
        sensors = rec.get("payload_sensors", {}) or {}
        for sensor_id, sensor_data in sensors.items():
            old_status = per_sensor_last_status.get(sensor_id)
            new_status = get_sensor_status(sensor_data)
            if old_status is not None and new_status is not None and old_status != new_status:
                status_change_events.append(
                    {
                        "time": rec.get("received_at_iso"),
                        "sensor_id": sensor_id,
                        "sensor_type": sensor_data.get("sensor_type"),
                        "from": old_status,
                        "to": new_status,
                    }
                )
            if new_status is not None:
                per_sensor_last_status[sensor_id] = new_status
            latest_sensor[sensor_id] = sensor_data

    status_counts: Dict[str, Dict[str, int]] = {}
    numeric_stats: Dict[str, Dict[str, float]] = {}

    for sensor_id, sensor_data in latest_sensor.items():
        sensor_type = sensor_data.get("sensor_type", "unknown")
        status = get_sensor_status(sensor_data) or "unknown"

        status_counts.setdefault(sensor_type, {})
        status_counts[sensor_type][status] = status_counts[sensor_type].get(status, 0) + 1

        value = sensor_data.get("value")
        if isinstance(value, (int, float)):
            numeric_stats.setdefault(sensor_type, {"count": 0, "min": value, "max": value, "sum": 0.0})
            stat = numeric_stats[sensor_type]
            stat["count"] += 1
            stat["sum"] += float(value)
            stat["min"] = min(stat["min"], float(value))
            stat["max"] = max(stat["max"], float(value))

    for sensor_type, stat in numeric_stats.items():
        if stat["count"] > 0:
            stat["avg"] = round(stat["sum"] / stat["count"], 3)
        stat.pop("sum", None)

    return {
        "latest_sensors": latest_sensor,
        "status_counts": status_counts,
        "numeric_stats": numeric_stats,
        "status_change_events": status_change_events,
        "num_records_in_window": len(window_records),
    }


def _sorted_ids(sensor_ids: List[str]) -> List[str]:
    def key_fn(s: str):
        # Extract trailing digits for stable physical ordering when possible
        digits = "".join(ch for ch in s if ch.isdigit())
        return (int(digits) if digits else 999999, s)
    return sorted(sensor_ids, key=key_fn)


def build_prototype_layout(sensor_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, List[str]] = {}
    for sensor_id, info in sensor_map.items():
        sensor_type = info.get("sensor_type", "unknown")
        by_type.setdefault(sensor_type, []).append(sensor_id)

    for t in by_type:
        by_type[t] = _sorted_ids(by_type[t])

    layout = {
        "notes": "Prototype layout. User will refine later.",
        "zones": {},
    }

    # Door at bottom entrance
    for i, sid in enumerate(by_type.get("door", []), start=1):
        layout["zones"][sid] = {"zone": f"entrance_bottom_{i}", "x": 0.90, "y": 0.98}

    # Infrared right side from entrance
    ir_list = by_type.get("infrared", [])
    for i, sid in enumerate(ir_list):
        y = 0.25 + (0.5 * (i / max(1, len(ir_list) - 1))) if len(ir_list) > 1 else 0.5
        layout["zones"][sid] = {"zone": f"right_ir_{i+1}", "x": 0.87, "y": round(y, 3)}

    # Distance on left side opposite infrared
    dist_list = by_type.get("distance", [])
    for i, sid in enumerate(dist_list):
        y = 0.25 + (0.5 * (i / max(1, len(dist_list) - 1))) if len(dist_list) > 1 else 0.5
        layout["zones"][sid] = {"zone": f"left_distance_{i+1}", "x": 0.12, "y": round(y, 3)}

    # Temperature on top opposite door
    for i, sid in enumerate(by_type.get("temperature", []), start=1):
        layout["zones"][sid] = {"zone": f"top_temperature_{i}", "x": 0.86, "y": 0.08}

    # Light on middle-left near distance side
    for i, sid in enumerate(by_type.get("light", []), start=1):
        layout["zones"][sid] = {"zone": f"left_middle_light_{i}", "x": 0.08, "y": 0.50}

    # Tilt sensors represent the two AC positions in center
    tilt_list = by_type.get("tilt", [])
    tilt_targets = [(0.50, 0.37), (0.50, 0.63)]
    for i, sid in enumerate(tilt_list):
        tx, ty = tilt_targets[i % len(tilt_targets)]
        layout["zones"][sid] = {"zone": f"center_ac_tilt_{i+1}", "x": tx, "y": ty}

    # Motion/activity around center in a ring-like pattern by index order
    center_x, center_y = 0.50, 0.50
    ring_defs = [("motion", 0.36), ("activity", 0.24)]
    for sensor_type, radius in ring_defs:
        items = by_type.get(sensor_type, [])
        n = max(1, len(items))
        for idx, sid in enumerate(items):
            angle = (2 * math.pi * idx) / n
            x = center_x + radius * math.cos(angle)
            y = center_y + radius * math.sin(angle)
            layout["zones"][sid] = {
                "zone": f"center_ring_{sensor_type}_{idx+1}",
                "x": round(max(0.02, min(0.98, x)), 3),
                "y": round(max(0.02, min(0.98, y)), 3),
            }

    # Any remaining unknown sensor types
    for sensor_type, ids in by_type.items():
        for sid in ids:
            if sid not in layout["zones"]:
                layout["zones"][sid] = {"zone": f"unknown_{sensor_type}", "x": 0.5, "y": 0.5}

    return layout


def build_prompt(
    window_start_iso: str,
    window_end_iso: str,
    window_summary: Dict[str, Any],
    layout: Dict[str, Any],
) -> str:
    return (
        "You are an operator assistant for a smart space.\n"
        "Generate one monitoring caption for a 30-second sensor window.\n\n"
        "Rules:\n"
        "1) Output exactly one paragraph.\n"
        "2) Use moderate inference (plausible occupancy/activity interpretation), but do not hallucinate people identities.\n"
        "3) Focus on operational relevance: occupancy/activity pattern, comfort, door/access state, unusual changes.\n"
        "4) Mention spatial context using zone names when useful.\n"
        "5) If evidence is weak, use cautious wording (e.g., 'suggests', 'likely').\n\n"
        "Few-shot examples (style and length reference):\n\n"
        "Example 1 — empty room:\n"
        "Window: 2026-04-02T10:00:00+09:00 to 2026-04-02T10:00:30+09:00\n"
        'status_counts: {"distance": {"far": 2}, "infrared": {"far": 2}, "motion": {"no_motion": 1}, '
        '"activity": {"idle": 1}, "door": {"closed": 1}, "light": {"indoor_light": 1}, "temperature": {"normal": 1}}\n'
        "status_change_count: 0\n"
        "Caption: The room appears empty with no occupancy evidence on any distance, infrared, motion, or activity sensor; "
        "the door is closed, lighting is sufficient, and temperature is normal, so the scene is stable and no HVAC or lighting action is needed.\n\n"
        "Example 2 — single person (study):\n"
        "Window: 2026-04-02T14:30:00+09:00 to 2026-04-02T14:30:30+09:00\n"
        'status_counts: {"distance": {"near": 1, "far": 1}, "infrared": {"near": 1, "far": 1}, "motion": {"no_motion": 1}, '
        '"activity": {"idle": 1}, "door": {"closed": 1}, "light": {"indoor_light": 1}, "temperature": {"normal": 1}}\n'
        "status_change_count: 1\n"
        "Caption: One occupant is likely present and stationary, with a single distance and infrared sensor reporting near while motion and activity remain idle, "
        "consistent with focused desk work such as studying; the door is closed and the scene is mostly stable with comfortable lighting and temperature.\n\n"
        "Example 3 — group (meeting):\n"
        "Window: 2026-04-02T16:00:00+09:00 to 2026-04-02T16:00:30+09:00\n"
        'status_counts: {"distance": {"near": 3, "far": 1}, "infrared": {"near": 3, "far": 1}, "motion": {"motion": 1}, '
        '"activity": {"active": 2}, "door": {"open": 1, "closed": 1}, "light": {"indoor_light": 1}, "temperature": {"normal": 1}}\n'
        "status_change_count: 5\n"
        "Caption: Multiple occupants are likely present, with three distance and three infrared sensors reporting near alongside active motion and two active chairs, "
        "consistent with a group gathering or meeting; a recent door-open event and a highly dynamic scene with frequent state transitions suggest people entering and interacting around the table.\n\n"
        f"Window: {window_start_iso} to {window_end_iso}\n\n"
        f"Prototype room layout (sensor -> zone):\n{json.dumps(layout, ensure_ascii=False, indent=2)}\n\n"
        f"Window sensor summary:\n{json.dumps(window_summary, ensure_ascii=False, indent=2)}\n\n"
        "Now provide the single-paragraph caption:"
    )


def normalize_caption(text: str) -> str:
    one_line = " ".join((text or "").strip().split())
    return one_line


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline sensor window captioning.")
    parser.add_argument("--input", type=str, required=True, help="Input JSON from sensor_stream_logger.py.")
    parser.add_argument("--output", type=str, default=None, help="Output pretty JSON path.")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Model name for init_model.")
    parser.add_argument("--gpus", type=int, default=1, help="Number of GPUs for model init.")
    parser.add_argument("--window-sec", type=int, default=DEFAULT_WINDOW_SEC, help="Fixed window size in seconds.")
    return parser.parse_args()


def resolve_output_path(path_arg: Optional[str], input_path: str) -> str:
    if path_arg:
        return path_arg
    stem = os.path.splitext(os.path.basename(input_path))[0]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join("logs", f"{stem}_captions_{ts}.json")


def main() -> None:
    args = parse_args()
    if args.window_sec <= 0:
        raise ValueError("--window-sec must be > 0")

    data = load_logged_json(args.input)
    records = data.get("records", [])
    if not records:
        raise ValueError("No records found in input log.")

    windows = build_windows(records, args.window_sec)
    if not windows:
        raise ValueError("No valid timestamped records available for windowing.")

    # Build a global latest sensor map for prototype layout generation
    global_latest_sensors: Dict[str, Dict[str, Any]] = {}
    for rec in sorted(records, key=lambda x: x.get("received_at_unix", 0)):
        sensors = rec.get("payload_sensors", {}) or {}
        for sid, sdata in sensors.items():
            global_latest_sensors[sid] = sdata
    room_layout = build_prototype_layout(global_latest_sensors)

    llm = init_model(args.model, args.gpus)

    outputs = []
    for w_start, w_end, w_records in windows:
        w_start_iso = datetime.fromtimestamp(w_start, tz=timezone.utc).isoformat()
        w_end_iso = datetime.fromtimestamp(w_end, tz=timezone.utc).isoformat()

        window_summary = summarize_window_records(w_records)
        prompt = build_prompt(
            window_start_iso=w_start_iso,
            window_end_iso=w_end_iso,
            window_summary=window_summary,
            layout=room_layout,
        )

        response = llm.generate_response({"text": prompt}, max_new_tokens=512, temperature=0.4)
        caption = normalize_caption(response)

        outputs.append(
            {
                "window_start_unix": w_start,
                "window_end_unix": w_end,
                "window_start_iso": w_start_iso,
                "window_end_iso": w_end_iso,
                "num_records": len(w_records),
                "caption": caption,
                "window_summary": {
                    "num_records_in_window": window_summary["num_records_in_window"],
                    "status_counts": window_summary["status_counts"],
                    "numeric_stats": window_summary["numeric_stats"],
                    "num_status_changes": len(window_summary["status_change_events"]),
                },
            }
        )

    output_path = resolve_output_path(args.output, args.input)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    result = {
        "metadata": {
            "created_at_iso": now_iso(),
            "input_file": args.input,
            "model": args.model,
            "gpus": args.gpus,
            "window_sec": args.window_sec,
            "default_broker_context": DEFAULT_BROKER,
            "notes": "Room layout is a prototype generated from sensor IDs and user constraints.",
        },
        "room_layout_prototype": room_layout,
        "windows": outputs,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(outputs)} window captions to: {output_path}")


if __name__ == "__main__":
    main()
