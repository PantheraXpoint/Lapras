#!/usr/bin/env python3
"""
Log dashboard-agent context stream from MQTT for a fixed duration.

Default source topic:
  virtual/dashboard/to/context/updateContext
"""

import argparse
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import paho.mqtt.client as mqtt


DEFAULT_BROKER = "143.248.55.82"
DEFAULT_PORT = 1883
DEFAULT_TOPIC = "virtual/dashboard/to/context/updateContext"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SensorStreamLogger:
    def __init__(self, broker: str, port: int, topic: str) -> None:
        self.broker = broker
        self.port = port
        self.topic = topic
        self.records: List[Dict[str, Any]] = []
        self.total_messages = 0
        self.parse_errors = 0
        self._lock = threading.Lock()

        client_id = f"sensor-stream-logger-{int(time.time() * 1000)}"
        self.client = mqtt.Client(client_id=client_id)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logging.info("Connected to MQTT broker %s:%s", self.broker, self.port)
            result = self.client.subscribe(self.topic, qos=1)
            logging.info("Subscribed to topic %s (result=%s)", self.topic, result)
        else:
            logging.error("Failed to connect. rc=%s", rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logging.warning("Unexpected disconnect from broker. rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        received_unix = time.time()
        received_iso = datetime.fromtimestamp(received_unix, tz=timezone.utc).isoformat()
        payload_text = msg.payload.decode("utf-8", errors="replace")

        with self._lock:
            self.total_messages += 1

        try:
            message_data = json.loads(payload_text)
            event = message_data.get("event", {})
            source = message_data.get("source", {})
            payload = message_data.get("payload", {})

            record = {
                "received_at_unix": received_unix,
                "received_at_iso": received_iso,
                "topic": msg.topic,
                "event_id": event.get("id"),
                "event_timestamp": event.get("timestamp"),
                "event_type": event.get("type"),
                "source_entity_type": source.get("entityType"),
                "source_entity_id": source.get("entityId"),
                "payload_agent_type": payload.get("agent_type"),
                "payload_state": payload.get("state", {}),
                "payload_sensors": payload.get("sensors", {}),
            }

            with self._lock:
                self.records.append(record)

        except Exception as exc:
            with self._lock:
                self.parse_errors += 1
                self.records.append(
                    {
                        "received_at_unix": received_unix,
                        "received_at_iso": received_iso,
                        "topic": msg.topic,
                        "parse_error": str(exc),
                        "raw_payload": payload_text,
                    }
                )

    def run_for_minutes(self, minutes: float) -> None:
        self.client.connect(self.broker, self.port)
        self.client.loop_start()

        stop_at = time.time() + minutes * 60.0
        while time.time() < stop_at:
            time.sleep(0.2)

        self.client.loop_stop()
        self.client.disconnect()

    def build_output(self, started_at_unix: float, ended_at_unix: float) -> Dict[str, Any]:
        with self._lock:
            records_copy = list(self.records)
            total_messages = self.total_messages
            parse_errors = self.parse_errors

        return {
            "metadata": {
                "logger": "sensor_stream_logger.py",
                "started_at_unix": started_at_unix,
                "ended_at_unix": ended_at_unix,
                "started_at_iso": datetime.fromtimestamp(started_at_unix, tz=timezone.utc).isoformat(),
                "ended_at_iso": datetime.fromtimestamp(ended_at_unix, tz=timezone.utc).isoformat(),
                "duration_seconds": round(ended_at_unix - started_at_unix, 3),
                "broker": self.broker,
                "port": self.port,
                "topic": self.topic,
                "total_messages": total_messages,
                "parse_errors": parse_errors,
                "stored_records": len(records_copy),
            },
            "records": records_copy,
        }


def resolve_output_path(path_arg: Optional[str]) -> str:
    if path_arg:
        return path_arg
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join("logs", f"sensor_stream_{ts}.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Log dashboard-agent sensor context stream.")
    parser.add_argument("--minutes", type=float, required=True, help="How many minutes to record.")
    parser.add_argument("--output", type=str, default=None, help="Output pretty JSON file path.")
    parser.add_argument("--broker", type=str, default=DEFAULT_BROKER, help="MQTT broker host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="MQTT broker port.")
    parser.add_argument("--topic", type=str, default=DEFAULT_TOPIC, help="MQTT topic to subscribe.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.minutes <= 0:
        raise ValueError("--minutes must be greater than 0.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    output_path = resolve_output_path(args.output)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    logger = SensorStreamLogger(broker=args.broker, port=args.port, topic=args.topic)
    started_at = time.time()
    logging.info("Start logging for %.2f minute(s). Output: %s", args.minutes, output_path)

    try:
        logger.run_for_minutes(args.minutes)
    finally:
        ended_at = time.time()
        result = logger.build_output(started_at_unix=started_at, ended_at_unix=ended_at)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        logging.info("Saved %d records to %s", len(result["records"]), output_path)
        logging.info("Done. total_messages=%d parse_errors=%d",
                     result["metadata"]["total_messages"],
                     result["metadata"]["parse_errors"])


if __name__ == "__main__":
    main()
