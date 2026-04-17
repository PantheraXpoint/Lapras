#!/usr/bin/env python3
"""
Generate 3 deterministic caption cases with fixed sensor inputs.

This script:
1) builds fixed sensor inputs for 3 scenarios,
2) computes status_counts from those sensor inputs,
3) injects them into the prompt format,
4) calls LLM and prints generated captions.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if REPO_ROOT not in sys.path:
    sys.path.append(REPO_ROOT)

from llms.init_model import init_model  # noqa: E402


def _get_sensor_status(sensor: Dict[str, Any]) -> str:
    sensor_type = sensor.get("sensor_type")
    metadata = sensor.get("metadata", {}) or {}
    if sensor_type in {"infrared", "distance"}:
        return metadata.get("proximity_status", "unknown")
    if sensor_type == "motion":
        return metadata.get("motion_status", "unknown")
    if sensor_type == "activity":
        return metadata.get("activity_status", "unknown")
    if sensor_type == "temperature":
        return metadata.get("temperature_status", "unknown")
    if sensor_type == "door":
        return metadata.get("door_status", "unknown")
    if sensor_type == "light":
        return metadata.get("light_status", "unknown")
    if sensor_type == "tilt":
        return metadata.get("tilt_status", "unknown")
    return "unknown"


def summarize_window_records(window_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    latest_sensor: Dict[str, Dict[str, Any]] = {}
    per_sensor_last_status: Dict[str, str] = {}
    status_counts: Dict[str, Dict[str, int]] = {}
    status_change_count = 0

    for rec in sorted(window_records, key=lambda x: x["received_at_unix"]):
        sensors = rec.get("payload_sensors", {}) or {}
        for sensor_id, sensor_data in sensors.items():
            old_status = per_sensor_last_status.get(sensor_id)
            new_status = _get_sensor_status(sensor_data)
            if old_status is not None and new_status != old_status:
                status_change_count += 1
            per_sensor_last_status[sensor_id] = new_status
            latest_sensor[sensor_id] = sensor_data

    for sensor_data in latest_sensor.values():
        sensor_type = sensor_data.get("sensor_type", "unknown")
        status = _get_sensor_status(sensor_data)
        status_counts.setdefault(sensor_type, {})
        status_counts[sensor_type][status] = status_counts[sensor_type].get(status, 0) + 1

    return {
        "status_counts": status_counts,
        "status_change_count": status_change_count,
    }


def build_prompt(start_iso: str, end_iso: str, summary: Dict[str, Any]) -> str:
    status_counts = summary.get("status_counts", {})
    status_change_count = summary.get("status_change_count", 0)
    return (
        "You are a smart-room monitoring assistant.\n"
        "Write exactly one concise operational paragraph for this monitoring window.\n\n"
        "Room context:\n"
        "- This is a smart room with multi-sensor monitoring.\n\n"
        "Interpretation policy:\n"
        "- Use status_counts only; do not invent metrics.\n"
        "- Occupancy evidence sources (OR logic):\n"
        "  distance.near, infrared.near, motion.motion, activity.active.\n"
        "- Occupancy level heuristic:\n"
        "  * no evidence -> likely empty.\n"
        "  * evidence count = 1 -> likely one person.\n"
        "  * evidence count > 1 -> likely multiple people.\n"
        "- Door/access: door.open means access event/open door.\n"
        "- Comfort from temperature:\n"
        "  * hot -> suggest cooling/AC.\n"
        "  * cold -> suggest warming/heating.\n"
        "  * normal -> no HVAC change needed.\n"
        "- Lighting guidance:\n"
        "  * no_light/dim/dark -> suggest turning lights on.\n"
        "  * indoor_light/normal -> lighting generally sufficient.\n"
        "  * bright/sunlight -> avoid adding more light unless task-specific.\n"
        "- Change dynamics from status_change_count:\n"
        "  * <= 1: mostly stable scene.\n"
        "  * > 1: dynamic/fluctuating scene; mention repeated state transitions.\n"
        "  * >= 4: highly dynamic; describe as active fluctuations.\n"
        "- If evidence is weak or mixed, use cautious wording like likely/may.\n\n"
        f"Window: {start_iso} to {end_iso}\n"
        f"status_counts: {json.dumps(status_counts, ensure_ascii=False)}\n"
        f"status_change_count: {status_change_count}\n\n"
        "Caption:"
    )


def s(sensor_type: str, status_key: str, status_val: str) -> Dict[str, Any]:
    return {"sensor_type": sensor_type, "metadata": {status_key: status_val}}


@dataclass
class CaptionCase:
    name: str
    sensors: Dict[str, Dict[str, Any]]


def build_cases() -> List[CaptionCase]:
    case_a_sensors: Dict[str, Dict[str, Any]] = {
        "distance_1": s("distance", "proximity_status", "near"),
        "distance_2": s("distance", "proximity_status", "near"),
        "infrared_1": s("infrared", "proximity_status", "near"),
        "motion_1": s("motion", "motion_status", "motion"),
        "motion_2": s("motion", "motion_status", "motion"),
        "activity_1": s("activity", "activity_status", "active"),
        "activity_2": s("activity", "activity_status", "active"),
        "activity_3": s("activity", "activity_status", "active"),
        "temperature_1": s("temperature", "temperature_status", "hot"),
        "light_1": s("light", "light_status", "no_light"),
        "door_1": s("door", "door_status", "open"),
    }

    case_b_sensors: Dict[str, Dict[str, Any]] = {
        "distance_1": s("distance", "proximity_status", "far"),
        "distance_2": s("distance", "proximity_status", "far"),
        "distance_3": s("distance", "proximity_status", "far"),
        "distance_4": s("distance", "proximity_status", "far"),
        "infrared_1": s("infrared", "proximity_status", "far"),
        "infrared_2": s("infrared", "proximity_status", "far"),
        "motion_1": s("motion", "motion_status", "no_motion"),
        "motion_2": s("motion", "motion_status", "no_motion"),
        "motion_3": s("motion", "motion_status", "no_motion"),
        "motion_4": s("motion", "motion_status", "no_motion"),
        "motion_5": s("motion", "motion_status", "no_motion"),
        "motion_6": s("motion", "motion_status", "no_motion"),
        "activity_1": s("activity", "activity_status", "inactive"),
        "activity_2": s("activity", "activity_status", "inactive"),
        "activity_3": s("activity", "activity_status", "inactive"),
        "activity_4": s("activity", "activity_status", "inactive"),
        "activity_5": s("activity", "activity_status", "inactive"),
        "activity_6": s("activity", "activity_status", "inactive"),
        "activity_7": s("activity", "activity_status", "inactive"),
        "activity_8": s("activity", "activity_status", "inactive"),
        "temperature_1": s("temperature", "temperature_status", "normal"),
        "light_1": s("light", "light_status", "indoor_light"),
        "door_1": s("door", "door_status", "closed"),
    }

    case_c_sensors: Dict[str, Dict[str, Any]] = {
        "activity_1": s("activity", "activity_status", "active"),
        "activity_2": s("activity", "activity_status", "inactive"),
        "activity_3": s("activity", "activity_status", "inactive"),
        "activity_4": s("activity", "activity_status", "inactive"),
        "activity_5": s("activity", "activity_status", "inactive"),
        "activity_6": s("activity", "activity_status", "inactive"),
        "motion_1": s("motion", "motion_status", "no_motion"),
        "motion_2": s("motion", "motion_status", "no_motion"),
        "motion_3": s("motion", "motion_status", "no_motion"),
        "motion_4": s("motion", "motion_status", "no_motion"),
        "temperature_1": s("temperature", "temperature_status", "cold"),
        "light_1": s("light", "light_status", "sunlight"),
        "door_1": s("door", "door_status", "closed"),
    }

    return [
        CaptionCase(
            name="Example A",
            sensors=case_a_sensors,
        ),
        CaptionCase(
            name="Example B",
            sensors=case_b_sensors,
        ),
        CaptionCase(
            name="Example C",
            sensors=case_c_sensors,
        ),
    ]


def normalize_caption(text: str) -> str:
    return " ".join((text or "").strip().split())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate 3 fixed-input cases and call LLM.")
    parser.add_argument("--model", type=str, default="qwenlm", help="Model name for init_model")
    parser.add_argument("--gpus", type=int, default=1, help="Number of GPUs for model init")
    parser.add_argument("--max-new-tokens", type=int, default=256, help="Max new tokens")
    parser.add_argument("--temperature", type=float, default=0.4, help="Sampling temperature")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base = datetime.now(timezone.utc)
    cases = build_cases()
    llm = init_model(args.model, args.gpus)

    for i, case in enumerate(cases):
        start = base + timedelta(seconds=i * 10)
        end = start + timedelta(seconds=8)
        # one record is enough because we want controlled status_counts output
        records = [{"received_at_unix": end.timestamp(), "payload_sensors": case.sensors}]
        summary = summarize_window_records(records)
        prompt = build_prompt(start.isoformat(), end.isoformat(), summary)
        response = llm.generate_response(
            {"text": prompt},
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
        )
        caption = normalize_caption(response)

        print("\n" + "=" * 90)
        print(f"{case.name}")
        print("-" * 90)
        print(prompt)
        print(caption)
        print("=" * 90)


if __name__ == "__main__":
    main()
