#!/usr/bin/env python3
"""
Start script for simulated live 3D sensor visualization.

This script:
1) Generates synthetic sensor payloads (no MQTT required)
2) Cycles infinitely through 3 scenarios matching prompt examples A/B/C
3) Serves a tiny HTTP API for the 3D page:
   - GET /api/latest -> latest payload snapshot as JSON
   - GET / or /visualize_smart_room_aframe.html -> 3D HTML
   - GET /<filename> -> any static file in the same directory as the HTML
"""

import argparse
import json
import logging
import os
import threading
import time
import random
import re
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - [%(module)s] - %(message)s",
)
logger = logging.getLogger("sensor_3d_sim_stream")


MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
}


class LiveSensorStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Dict[str, Any] = {
            "agent_id": "simulator",
            "topic": "simulated/stream",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {},
            "flattened": {},
            "scenario": "init",
        }

    def update(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            self._latest = snapshot

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._latest)


def str_to_num(value: Any) -> float:
    if value in ("on", "true", True):
        return 1.0
    if value in ("off", "false", False):
        return 0.0
    if value in ("near", "motion", "active", "open", "bright", "warm", "hot"):
        return 1.0
    if value in ("far", "no_motion", "idle", "inactive", "closed", "dim", "normal", "cold", "unknown"):
        return 0.0
    try:
        return float(value)
    except Exception:
        return 0.0


def flatten_nested_dict(attrs: Dict[str, Any], prefix: str, value: Any) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            next_prefix = f"{prefix}_{k}" if prefix else str(k)
            flatten_nested_dict(attrs, next_prefix, v)
        return
    attrs[prefix] = str_to_num(value)


def flatten_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = payload.get("state", {})
    sensors = payload.get("sensors", {})
    attrs: Dict[str, Any] = {}

    for key, value in state.items():
        if isinstance(value, dict):
            flatten_nested_dict(attrs, key, value)
        else:
            attrs[key] = str_to_num(value)

    for sensor_name, sensor_info in sensors.items():
        safe_name = sensor_name.replace("-", "_")
        value = sensor_info.get("value")
        unit = sensor_info.get("unit", "")
        metadata = sensor_info.get("metadata", {}) or {}

        if unit == "cm":
            try:
                attrs[f"{safe_name}_cm"] = float(value)
            except Exception:
                pass
        else:
            try:
                attrs[safe_name] = float(value)
            except Exception:
                pass

        for meta_key, meta_val in metadata.items():
            if isinstance(meta_val, (dict, list)):
                continue
            mk = f"{safe_name}_{meta_key}".replace("-", "_")
            if mk in attrs:
                continue
            attrs[mk] = str_to_num(meta_val)

    # Alias simulated sensor keys like distance_s1_* to distance_1_* so they match
    # smart_room.xml bindings (distance_1_cm, infrared_1_cm, ...).
    alias_patterns = (
        r"^(distance|infrared|motion)_s(\d+)($|_.*)",
        r"^(activity)_s(\d+)([ab]$|[ab]_.*|$|_.*)",
    )
    generated_aliases: Dict[str, Any] = {}
    for key, value in attrs.items():
        for pattern in alias_patterns:
            m = re.match(pattern, key)
            if not m:
                continue
            sensor_type = m.group(1)
            idx = m.group(2)
            suffix = m.group(3) if len(m.groups()) >= 3 else ""
            alias_key = f"{sensor_type}_{idx}{suffix}"
            if alias_key not in attrs:
                generated_aliases[alias_key] = value
            break
    attrs.update(generated_aliases)

    # Ensure XML-bound keys always exist so visual markers do not show "missing".
    # This keeps scenario payloads sparse while still satisfying UI bindings.
    for idx in range(1, 5):
        attrs.setdefault(f"distance_{idx}_cm", 0.0)
        attrs.setdefault(f"distance_{idx}_threshold_cm", 0.0)
        attrs.setdefault(f"infrared_{idx}_cm", 0.0)
        attrs.setdefault(f"infrared_{idx}_threshold_cm", 0.0)

    # Compatibility aliases for current 3D visualization mapping.
    if "motion_status" not in attrs and "activity_summary_motion_active" in attrs:
        attrs["motion_status"] = attrs["activity_summary_motion_active"]
    if "activity_detected" not in attrs and "activity_summary_activity_detected" in attrs:
        attrs["activity_detected"] = attrs["activity_summary_activity_detected"]
    if "door_status" not in attrs:
        if "door_01_door_status" in attrs:
            attrs["door_status"] = attrs["door_01_door_status"]
        elif "activity_summary_doors_open" in attrs:
            attrs["door_status"] = 1.0 if attrs["activity_summary_doors_open"] > 0 else 0.0
    if "light_status" not in attrs and "light_1_light_status" in attrs:
        attrs["light_status"] = attrs["light_1_light_status"]
    if "temperature_status" not in attrs:
        temp_status_keys = [k for k in attrs.keys() if k.startswith("temperature_") and k.endswith("_temperature_status")]
        if temp_status_keys:
            attrs["temperature_status"] = max(float(attrs[k]) for k in temp_status_keys)
    if "proximity_status" not in attrs:
        prox_keys = [k for k in attrs.keys() if k.endswith("_proximity_status")]
        if prox_keys:
            attrs["proximity_status"] = max(float(attrs[k]) for k in prox_keys)

    return attrs


def _now_comm_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _mk_sensor(
    sensor_type: str,
    value: Any,
    unit: str = "",
    sensor_name: str = "",
    sensor_id: int = 0,
    raw_reading: str = "",
    **metadata: Any,
) -> Dict[str, Any]:
    md = {
        "sensor_name": sensor_name or f"{sensor_type.upper()}|Simulated|{sensor_id}",
        "sensor_id": sensor_id,
        "raw_reading": raw_reading or str(value),
        "battery_level": metadata.pop("battery_level", random.randint(75, 100)),
        "signal_strength": metadata.pop("signal_strength", 100),
        "status": metadata.pop("status", 0),
        "last_communication": metadata.pop("last_communication", _now_comm_time()),
    }
    md.update(metadata)

    # Keep all thresholds constant across simulated payloads.
    if sensor_type in {"distance", "infrared"}:
        md["threshold_cm"] = 0
    if sensor_type == "light":
        md["indoor_threshold"] = 0
        md["sunlight_threshold"] = 0
    if sensor_type == "temperature":
        md["hot_threshold"] = 0
        md["cold_threshold"] = 0
    if sensor_type == "tilt":
        md["tilt_threshold"] = 0

    return {
        "sensor_type": sensor_type,
        "value": value,
        "unit": unit,
        "metadata": md,
    }


def _scenario_example_a() -> Dict[str, Any]:
    sensors: Dict[str, Any] = {}
    # status_counts target:
    # distance.near=2, infrared.near=1, motion.motion=2, activity.active=3,
    # temperature.hot=1, light.no_light=1, door.open=1
    for i, sid in enumerate((601101, 601102, 601103, 601104), start=1):
        key = f"distance_s{i}"
        sensors[key] = _mk_sensor(
            "distance",
            28 + i,
            "cm",
            sensor_name=f"S{i}|DistanceSensor|{sid}",
            sensor_id=sid,
            raw_reading=f"{28 + i} cm",
            proximity_status="near",
            distance_cm=28 + i,
        )
    sensors["infrared_s1"] = _mk_sensor(
        "infrared",
        1,
        "boolean",
        sensor_name="S1|InfraredSensor|602101",
        sensor_id=602101,
        raw_reading="Human Near",
        proximity_status="near",
    )
    for i, sid in enumerate((603101, 603102), start=1):
        sensors[f"motion_s{i}"] = _mk_sensor(
            "motion",
            True,
            "boolean",
            sensor_name=f"S{i}|MotionSensor|{sid}",
            sensor_id=sid,
            raw_reading="Motion Detected",
            motion_status="motion",
        )
    for suffix, sid in (("s1b", 505717), ("s2a", 505766), ("s4a", 505786)):
        sensors[f"activity_{suffix}"] = _mk_sensor(
            "activity",
            True,
            "boolean",
            sensor_name=f"{suffix.upper()}|MonnitActivitySensorAgent|{sid}",
            sensor_id=sid,
            raw_reading="Motion Detected",
            activity_status="active",
            activity_detected=True,
        )
    sensors["temperature_01"] = _mk_sensor(
        "temperature",
        30.5,
        "C",
        sensor_name="Room|TemperatureSensor|604101",
        sensor_id=604101,
        raw_reading="30.5C",
        temperature_status="hot",
    )
    sensors["light_1"] = _mk_sensor(
        "light",
        20.0,
        "lux",
        sensor_name="Room|LightSensor|605101",
        sensor_id=605101,
        raw_reading="very dark",
        light_status="no_light",
    )
    sensors["door_01"] = _mk_sensor(
        "door",
        True,
        "boolean",
        sensor_name="Room|DoorSensor|606101",
        sensor_id=606101,
        raw_reading="Open",
        door_status="open",
    )
    state = {
        "activity_summary": {
            "motion_active": True,
            "activity_detected": True,
            "doors_open": 1,
        }
    }
    return {"state": state, "sensors": sensors}


def _scenario_example_b() -> Dict[str, Any]:
    sensors: Dict[str, Any] = {}
    # status_counts target:
    # distance.far=4, infrared.far=2, motion.no_motion=6, activity.inactive=8,
    # temperature.normal=1, light.indoor_light=1, door.closed=1
    for i in range(4):
        sid = 611100 + i
        sensors[f"distance_s{i+1}"] = _mk_sensor(
            "distance",
            180 + i,
            "cm",
            sensor_name=f"S{i+1}|DistanceSensor|{sid}",
            sensor_id=sid,
            raw_reading=f"{180 + i} cm",
            proximity_status="far",
            distance_cm=180 + i,
        )
    for i in range(2):
        sid = 612100 + i
        sensors[f"infrared_s{i+1}"] = _mk_sensor(
            "infrared",
            False,
            "boolean",
            sensor_name=f"S{i+1}|InfraredSensor|{sid}",
            sensor_id=sid,
            raw_reading="No Presence",
            proximity_status="far",
        )
    for i in range(6):
        sid = 613100 + i
        sensors[f"motion_s{i+1}"] = _mk_sensor(
            "motion",
            False,
            "boolean",
            sensor_name=f"S{i+1}|MotionSensor|{sid}",
            sensor_id=sid,
            raw_reading="No Motion",
            motion_status="no_motion",
        )
    activity_keys = ("s1b", "s2a", "s2b", "s3a", "s3b", "s4a", "s4b", "s5a")
    for i, suffix in enumerate(activity_keys):
        sid = 505700 + i
        sensors[f"activity_{suffix}"] = _mk_sensor(
            "activity",
            False,
            "boolean",
            sensor_name=f"{suffix.upper()}|MonnitActivitySensorAgent|{sid}",
            sensor_id=sid,
            raw_reading="No Motion Detected",
            activity_status="inactive",
            activity_detected=False,
        )
    sensors["temperature_01"] = _mk_sensor(
        "temperature",
        22.5,
        "C",
        sensor_name="Room|TemperatureSensor|614101",
        sensor_id=614101,
        raw_reading="22.5C",
        temperature_status="normal",
    )
    sensors["light_1"] = _mk_sensor(
        "light",
        320.0,
        "lux",
        sensor_name="Room|LightSensor|615101",
        sensor_id=615101,
        raw_reading="Indoor Light",
        light_status="indoor_light",
    )
    sensors["door_01"] = _mk_sensor(
        "door",
        False,
        "boolean",
        sensor_name="Room|DoorSensor|616101",
        sensor_id=616101,
        raw_reading="Closed",
        door_status="closed",
    )
    state = {
        "activity_summary": {
            "motion_active": False,
            "activity_detected": False,
            "doors_open": 0,
        }
    }
    return {"state": state, "sensors": sensors}


def _scenario_example_c() -> Dict[str, Any]:
    sensors: Dict[str, Any] = {}
    # status_counts target:
    # activity.active=1,inactive=5, motion.no_motion=4,
    # temperature.cold=1, light.sunlight=1, door.closed=1
    sensors["activity_s4a"] = _mk_sensor(
        "activity",
        True,
        "boolean",
        sensor_name="S4A|MonnitActivitySensorAgent|505786",
        sensor_id=505786,
        raw_reading="Motion Detected",
        activity_status="active",
        activity_detected=True,
    )
    for i in range(5):
        sid = 625710 + i
        sensors[f"activity_s{i+1}b"] = _mk_sensor(
            "activity",
            False,
            "boolean",
            sensor_name=f"S{i+1}B|MonnitActivitySensorAgent|{sid}",
            sensor_id=sid,
            raw_reading="No Motion Detected",
            activity_status="inactive",
            activity_detected=False,
        )
    for i in range(4):
        sid = 623100 + i
        sensors[f"motion_s{i+1}"] = _mk_sensor(
            "motion",
            False,
            "boolean",
            sensor_name=f"S{i+1}|MotionSensor|{sid}",
            sensor_id=sid,
            raw_reading="No Motion",
            motion_status="no_motion",
        )
    # Keep all distance sensors present every cycle for stable visualization bindings.
    for i in range(4):
        sid = 621100 + i
        sensors[f"distance_s{i+1}"] = _mk_sensor(
            "distance",
            165 + i,
            "cm",
            sensor_name=f"S{i+1}|DistanceSensor|{sid}",
            sensor_id=sid,
            raw_reading=f"{165 + i} cm",
            proximity_status="far",
            distance_cm=165 + i,
        )
    sensors["temperature_01"] = _mk_sensor(
        "temperature",
        17.5,
        "C",
        sensor_name="Room|TemperatureSensor|624101",
        sensor_id=624101,
        raw_reading="17.5C",
        temperature_status="cold",
    )
    sensors["light_1"] = _mk_sensor(
        "light",
        1200.0,
        "lux",
        sensor_name="Room|LightSensor|625101",
        sensor_id=625101,
        raw_reading="Sunlight",
        light_status="sunlight",
    )
    sensors["door_01"] = _mk_sensor(
        "door",
        False,
        "boolean",
        sensor_name="Room|DoorSensor|626101",
        sensor_id=626101,
        raw_reading="Closed",
        door_status="closed",
    )
    state = {
        "activity_summary": {
            "motion_active": False,
            "activity_detected": True,
            "doors_open": 0,
        }
    }
    return {"state": state, "sensors": sensors}


class SimulatedPayloadBridge:
    def __init__(self, store: LiveSensorStore, interval_sec: float = 1.0):
        self.store = store
        self.interval_sec = max(0.1, float(interval_sec))
        self._stop_event = threading.Event()
        self._scenario_builders = [
            ("example_a", _scenario_example_a),
            ("example_b", _scenario_example_b),
            ("example_c", _scenario_example_c),
        ]

    def stop(self) -> None:
        self._stop_event.set()

    def run_forever(self) -> None:
        idx = 0
        while not self._stop_event.is_set():
            scenario_name, builder = self._scenario_builders[idx % len(self._scenario_builders)]
            payload = builder()
            flattened = flatten_payload(payload)
            self.store.update(
                {
                    "agent_id": "simulator",
                    "topic": f"simulated/stream/{scenario_name}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": payload,
                    "flattened": flattened,
                    "scenario": scenario_name,
                }
            )
            logger.info("Pushed simulated payload: %s", scenario_name)
            idx += 1
            time.sleep(self.interval_sec)


def make_handler(store: LiveSensorStore, html_path: str):
    static_dir = os.path.dirname(os.path.abspath(html_path))

    class Handler(BaseHTTPRequestHandler):
        def _write_json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_file(self, path: str, content_type: str):
            if not os.path.exists(path):
                self._write_json(
                    {"error": "file not found", "path": path},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/latest":
                self._write_json(store.get())
                return
            if self.path == "/api/keys":
                snapshot = store.get()
                flattened = snapshot.get("flattened", {})
                keys = sorted(flattened.keys()) if isinstance(flattened, dict) else []
                self._write_json(
                    {
                        "agent_id": snapshot.get("agent_id", "simulator"),
                        "timestamp": snapshot.get("timestamp", ""),
                        "key_count": len(keys),
                        "keys": keys,
                        "scenario": snapshot.get("scenario", ""),
                    }
                )
                return
            if self.path == "/":
                self._write_file(html_path, "text/html; charset=utf-8")
                return

            requested_file = self.path.lstrip("/")
            if ".." in requested_file or requested_file.startswith("/"):
                self._write_json({"error": "forbidden"}, status=HTTPStatus.FORBIDDEN)
                return

            file_path = os.path.join(static_dir, requested_file)
            if os.path.isfile(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                content_type = MIME_TYPES.get(ext, "application/octet-stream")
                self._write_file(file_path, content_type)
                return

            self._write_json(
                {"error": "not found", "path": self.path},
                status=HTTPStatus.NOT_FOUND,
            )

        def log_message(self, fmt, *args):
            logger.info("HTTP %s - %s", self.client_address[0], fmt % args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Start simulated 3D visualization stream")
    parser.add_argument("--http-host", default="0.0.0.0", help="HTTP bind host")
    parser.add_argument("--http-port", type=int, default=8765, help="HTTP bind port")
    parser.add_argument(
        "--visualization-file",
        default="visualize_smart_room_aframe.html",
        help="Path to the 3D visualization HTML file",
    )
    parser.add_argument(
        "--emit-interval-sec",
        type=float,
        default=1.0,
        help="Seconds between simulated payload emissions",
    )
    args = parser.parse_args()

    html_path = (
        args.visualization_file
        if os.path.isabs(args.visualization_file)
        else os.path.join(os.path.dirname(os.path.abspath(__file__)), args.visualization_file)
    )

    logger.info("[3D-SIM] Serving file: %s", html_path)
    logger.info("[3D-SIM] Static dir: %s", os.path.dirname(os.path.abspath(html_path)))
    logger.info("[3D-SIM] HTTP bind: http://%s:%s", args.http_host, args.http_port)
    logger.info("[3D-SIM] Emit interval: %.2fs", args.emit_interval_sec)

    store = LiveSensorStore()
    simulator = SimulatedPayloadBridge(store=store, interval_sec=args.emit_interval_sec)
    sim_thread = threading.Thread(target=simulator.run_forever, daemon=True, name="simulated-source-loop")
    sim_thread.start()

    handler = make_handler(store, html_path)
    httpd = ThreadingHTTPServer((args.http_host, args.http_port), handler)

    print("\n" + "=" * 60)
    print("SIMULATED 3D SENSOR STREAM STARTED")
    print("=" * 60)
    print("Source:      synthetic example loop (A -> B -> C -> ...)")
    print(f"3D URL:      http://{args.http_host}:{args.http_port}/")
    print(f"API URL:     http://{args.http_host}:{args.http_port}/api/latest")
    print(f"Keys URL:    http://{args.http_host}:{args.http_port}/api/keys")
    print(f"Interval:    {args.emit_interval_sec}s per payload")
    print("\nPress Ctrl+C to stop.\n")
    print("=" * 60)

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        logger.info("[3D-SIM] Received keyboard interrupt")
        print("\nShutting down simulated 3D stream server...")
    finally:
        simulator.stop()
        httpd.shutdown()
        httpd.server_close()
        logger.info("[3D-SIM] Server stopped")
        time.sleep(0.1)


if __name__ == "__main__":
    main()
