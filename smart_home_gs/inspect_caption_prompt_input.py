#!/usr/bin/env python3
"""
Inspect what prompt input the live caption LLM receives.

This script listens to the same MQTT stream used by
start_3d_visualization_stream.py, then builds the exact same summary and
prompt text used for caption generation.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import paho.mqtt.client as mqtt


# Match local import behavior used by start_3d_visualization_stream.py
script_dir = os.path.dirname(os.path.abspath(__file__))
lapras_root = os.path.abspath(os.path.join(script_dir, ".."))
workspace_root = os.path.abspath(os.path.join(lapras_root, ".."))
for path in (lapras_root, workspace_root):
    if path not in sys.path:
        sys.path.insert(0, path)

from lapras_middleware.event import MQTTMessage
from start_3d_visualization_stream import LiveCaptionEngine, LiveSensorStore


def _compact_sensor_preview(sensors: Dict[str, Any], max_items: int = 6) -> List[Dict[str, Any]]:
    preview: List[Dict[str, Any]] = []
    for sensor_id in sorted(sensors.keys())[:max_items]:
        sensor = sensors.get(sensor_id, {}) or {}
        metadata = sensor.get("metadata", {}) or {}
        preview.append(
            {
                "sensor_id": sensor_id,
                "sensor_type": sensor.get("sensor_type"),
                "value": sensor.get("value"),
                "unit": sensor.get("unit"),
                "metadata": metadata,
            }
        )
    return preview


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect live caption prompt input")
    parser.add_argument("--mqtt-broker", default="143.248.55.82", help="MQTT broker host")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument(
        "--topic",
        default="virtual/+/to/context/updateContext",
        help="MQTT topic filter to subscribe",
    )
    parser.add_argument(
        "--collect-sec",
        type=int,
        default=12,
        help="How long to collect messages before printing prompt",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=100,
        help="Max messages to consume before stopping",
    )
    args = parser.parse_args()

    store = LiveSensorStore()
    caption_engine = LiveCaptionEngine(store=store, window_sec=max(1, args.collect_sec))

    records: List[Dict[str, Any]] = []
    seen_topics: Dict[str, int] = {}
    latest_agent_id = "unknown"
    latest_payload_sensors: Dict[str, Any] = {}
    started_at = time.time()

    def _on_connect(client, userdata, flags, rc):
        if rc != 0:
            raise RuntimeError(f"Failed to connect to MQTT rc={rc}")
        client.subscribe(args.topic, qos=1)
        print(f"Subscribed: {args.topic}")

    def _on_message(client, userdata, msg):
        nonlocal latest_agent_id, latest_payload_sensors
        try:
            event = MQTTMessage.deserialize(msg.payload.decode())
            payload = event.payload or {}
            sensors = payload.get("sensors", {}) or {}
            if not isinstance(sensors, dict):
                sensors = {}
            now_unix = time.time()
            records.append({"received_at_unix": now_unix, "payload_sensors": sensors})
            seen_topics[msg.topic] = seen_topics.get(msg.topic, 0) + 1
            latest_payload_sensors = sensors
            parts = msg.topic.split("/")
            if len(parts) > 1:
                latest_agent_id = parts[1]
        except Exception as e:
            print(f"Warning: failed to parse message on {msg.topic}: {e}")

    client = mqtt.Client(client_id=f"inspect-caption-{int(time.time())}", clean_session=True)
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(args.mqtt_broker, args.mqtt_port)
    client.loop_start()

    try:
        while True:
            elapsed = time.time() - started_at
            if elapsed >= args.collect_sec:
                break
            if len(records) >= args.max_messages:
                break
            time.sleep(0.1)
    finally:
        client.loop_stop()
        client.disconnect()

    if not records:
        print("No messages captured. Try increasing --collect-sec.")
        return

    start_unix = records[0]["received_at_unix"]
    end_unix = records[-1]["received_at_unix"]
    start_iso = datetime.fromtimestamp(start_unix, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(end_unix, tz=timezone.utc).isoformat()
    summary = caption_engine._summarize_window_records(records)
    prompt = caption_engine._build_prompt(start_iso, end_iso, summary)

    print("\n=== CAPTION INPUT INSPECTION ===")
    print(f"broker: {args.mqtt_broker}:{args.mqtt_port}")
    print(f"captured_messages: {len(records)}")
    print(f"topics_seen: {json.dumps(seen_topics, indent=2)}")
    print(f"latest_agent_id: {latest_agent_id}")
    print(f"window: {start_iso} -> {end_iso}")
    print("\n--- Example sensors from latest payload.sensors ---")
    print(json.dumps(_compact_sensor_preview(latest_payload_sensors), indent=2, ensure_ascii=False))
    print("\n--- Summary object passed into prompt ---")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n--- Prompt text sent to LLM.generate_response(...) ---")
    print(prompt)
    print("=== END ===")


if __name__ == "__main__":
    main()
