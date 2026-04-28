#!/usr/bin/env python3
"""
Start script for live 3D sensor visualization.

This script:
1) Subscribes to source MQTT stream: virtual/+/to/context/updateContext
2) Parses/normalizes payloads (same flattening behavior as simulate_virtual_agent.py)
3) Serves a tiny HTTP API for the 3D page:
   - GET /api/latest -> latest payload snapshot as JSON
   - GET /api/control/capabilities -> agents seen on stream + which manual-control targets exist
   - POST /api/control/command -> JSON {"agent_id","action_name"} -> MQTT dashboard/control/command (ContextRuleManager)
   - GET / or /visualize_smart_room_aframe.html -> 3D HTML
   - GET /smart_room.xml -> room definition XML
   - GET /<filename> -> any static file in the same directory as the HTML
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

import paho.mqtt.client as mqtt

# Add relevant roots to Python path so local imports resolve.
script_dir = os.path.dirname(os.path.abspath(__file__))
lapras_root = os.path.abspath(os.path.join(script_dir, ".."))
workspace_root = os.path.abspath(os.path.join(lapras_root, ".."))
for path in (lapras_root, workspace_root):
    if path not in sys.path:
        sys.path.insert(0, path)

from lapras_middleware.event import MQTTMessage
try:
    from llms.init_model import init_model
except Exception:  # pragma: no cover - environment-dependent import path
    init_model = None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - [%(module)s] - %(message)s",
)
logger = logging.getLogger("sensor_3d_stream")


# Mapping of file extensions to MIME types for static file serving
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

ROOM_ID_N1_LAB = "n1_lab"
ROOM_ID_LIVING_ROOM = "living_room"
ROOM_IDS = (ROOM_ID_N1_LAB, ROOM_ID_LIVING_ROOM)

# Living room intentionally uses only a subset of the shared sensor feed.
LIVING_ROOM_SENSOR_IDS = {
    "light_1",
    "temperature_1",
    "distance_1",
    "distance_2",
    "infrared_1",
    "infrared_2",
}

_CONTROL_LIGHT_KEYS = ("light_hue_agent", "light_hue", "hue_light", "light", "hue")
_CONTROL_AIRCON_KEYS = ("aircon_agent", "aircon")
_CONTROL_COMBINED_KEYS = ("clubhouse_agent", "clubhouse", "club-house", "all", "back", "front")
_CLUBHOUSE_AGENT_KEYS = ("all", "back", "front")
_ACTIVE_AGENT_TTL_SEC = 20.0

RULE_SET_CATALOG: Dict[str, Dict[str, Any]] = {
    "clubhouse_all_normal": {
        "title": "Clubhouse all-normal",
        "target_agents": ["all"],
        "rule_files": ["rules/demo_rules/all-normal.ttl"],
    },
    "clubhouse_all_clean": {
        "title": "Clubhouse all-clean",
        "target_agents": ["all"],
        "rule_files": ["rules/demo_rules/all-clean.ttl"],
    },
    "clubhouse_repair_clean": {
        "title": "Clubhouse repair-clean",
        "target_agents": ["all"],
        "rule_files": ["rules/demo_rules/repair-clean.ttl"],
    },
    "clubhouse_back_read": {
        "title": "Clubhouse back-read",
        "target_agents": ["back"],
        "rule_files": ["rules/demo_rules/back-read.ttl"],
    },
    "clubhouse_back_nap": {
        "title": "Clubhouse back-nap",
        "target_agents": ["back"],
        "rule_files": ["rules/demo_rules/back-nap.ttl"],
    },
    "clubhouse_front_read": {
        "title": "Clubhouse front-read",
        "target_agents": ["front"],
        "rule_files": ["rules/demo_rules/front-read.ttl"],
    },
    "clubhouse_front_nap": {
        "title": "Clubhouse front-nap",
        "target_agents": ["front"],
        "rule_files": ["rules/demo_rules/front-nap.ttl"],
    },
    "energy_aircon_ir_temperature": {
        "title": "Energy aircon_ir_temperature",
        "target_agents": ["aircon"],
        "rule_files": ["rules/energy_efficient/aircon_ir_temperature.ttl"],
    },
    "energy_hue_ir_light": {
        "title": "Energy hue_ir_light",
        "target_agents": ["hue_light"],
        "rule_files": ["rules/energy_efficient/hue_ir_light.ttl"],
    },
}


def _canonical_control_agent_id(agent_id: str) -> Optional[str]:
    """Map runtime agent IDs/aliases to control-role IDs used by rules/topology logic."""
    if not isinstance(agent_id, str):
        return None
    aid = agent_id.strip().lower()
    if not aid:
        return None
    if aid in {"all", "back", "front"}:
        return aid
    if any(k in aid for k in _CONTROL_AIRCON_KEYS):
        return "aircon"
    if any(k in aid for k in _CONTROL_LIGHT_KEYS):
        return "hue_light"
    return None


def build_control_capabilities(agent_ids: Iterable[str], control_mode: str = "unknown") -> Dict[str, Any]:
    discovered = sorted({a for a in agent_ids if a and a != "unknown"})

    def first_match(keys: Tuple[str, ...]) -> Optional[str]:
        for agent in discovered:
            al = agent.lower()
            if any(k in al for k in keys):
                return agent
        return None

    light_agent = first_match(_CONTROL_LIGHT_KEYS)
    aircon_agent = first_match(_CONTROL_AIRCON_KEYS)
    combined_agent = first_match(_CONTROL_COMBINED_KEYS)
    # If only clubhouse is running, manual Light/AC rows should still target that
    # same clubhouse runtime agent with per-target parameters from the UI.
    if combined_agent and not light_agent:
        light_agent = combined_agent
    if combined_agent and not aircon_agent:
        aircon_agent = combined_agent
    canonical_by_agent = {aid: _canonical_control_agent_id(aid) for aid in discovered}
    split_agents = {a for a in discovered if canonical_by_agent.get(a) in {"aircon", "hue_light"}}
    clubhouse_agents = {a for a in discovered if canonical_by_agent.get(a) in {"all", "back", "front"}}
    topology_error = validate_control_mode(discovered)
    conflict = topology_error is not None
    agent_capabilities: Dict[str, Dict[str, Any]] = {}
    for aid in discovered:
        canonical = canonical_by_agent.get(aid)
        is_clubhouse = canonical in {"all", "back", "front"}
        is_split = canonical in {"aircon", "hue_light"}
        is_dashboard = aid == "dashboard"
        if is_dashboard:
            rules_support = False
            preset_support = False
            threshold_support = False
            threshold_types: List[str] = []
        elif is_clubhouse:
            rules_support = True
            preset_support = True
            threshold_support = True
            threshold_types = ["light", "temperature"]
        elif canonical == "aircon":
            rules_support = True
            preset_support = False
            threshold_support = True
            threshold_types = ["temperature"]
        elif canonical == "hue_light":
            rules_support = True
            preset_support = False
            threshold_support = True
            threshold_types = ["light"]
        else:
            rules_support = False
            preset_support = False
            threshold_support = False
            threshold_types = []
        agent_capabilities[aid] = {
            "manual_control": (is_clubhouse or is_split),
            "preset": preset_support,
            "rules": rules_support,
            "threshold": threshold_support,
            "threshold_types": threshold_types,
            "sensor_config": True,
            "notes": [] if threshold_support else (["unsupported"] if not is_dashboard else ["unsupported_for_dashboard"]),
        }
    return {
        "agents": discovered,
        "light_agent": light_agent,
        "aircon_agent": aircon_agent,
        "combined_agent": combined_agent,
        "can_control_light": light_agent is not None,
        "can_control_aircon": aircon_agent is not None,
        "can_control_combined": combined_agent is not None,
        "agent_capabilities": agent_capabilities,
        "mode_conflict": conflict,
        "mode_conflict_message": topology_error or "",
        "running_mode": (
            "conflict"
            if conflict
            else ("combined" if clubhouse_agents else ("split" if split_agents else "none"))
        ),
        "control_mode": control_mode,
    }


def build_rules_catalog_payload() -> Dict[str, Any]:
    return {
        "rule_sets": [
            {
                "id": rid,
                "title": spec["title"],
                "target_agents": list(spec["target_agents"]),
                "rule_files": list(spec["rule_files"]),
            }
            for rid, spec in RULE_SET_CATALOG.items()
        ]
    }


def resolve_rule_files(selected_agents: Iterable[str], selected_rule_set_ids: Iterable[str]) -> List[str]:
    explicit = [rid for rid in selected_rule_set_ids if rid in RULE_SET_CATALOG]
    effective_set_ids: List[str] = []
    if explicit:
        effective_set_ids = [explicit[0]]
    else:
        agents = {
            c for c in (
                _canonical_control_agent_id(a) for a in selected_agents if isinstance(a, str)
            ) if c
        }
        if "all" in agents:
            effective_set_ids.append("clubhouse_all_normal")
        elif "back" in agents:
            effective_set_ids.append("clubhouse_back_read")
        elif "front" in agents:
            effective_set_ids.append("clubhouse_front_read")
        elif "aircon" in agents:
            effective_set_ids.append("energy_aircon_ir_temperature")
        elif "hue_light" in agents:
            effective_set_ids.append("energy_hue_ir_light")
    files: List[str] = []
    seen = set()
    for set_id in effective_set_ids:
        for path in RULE_SET_CATALOG[set_id]["rule_files"]:
            if path not in seen:
                seen.add(path)
                files.append(path)
    return files


def validate_control_mode(agents: Iterable[str]) -> Optional[str]:
    controls = {
        c for c in (_canonical_control_agent_id(a) for a in agents if isinstance(a, str)) if c
    }
    if not controls:
        return None

    allowed = {
        frozenset({"aircon"}),
        frozenset({"hue_light"}),
        frozenset({"all"}),
        frozenset({"back"}),
        frozenset({"front"}),
        frozenset({"aircon", "hue_light"}),
        frozenset({"back", "front"}),
    }
    if frozenset(controls) in allowed:
        return None

    if "all" in controls and ("back" in controls or "front" in controls):
        return "Invalid running combo: 'all' cannot run with 'back' or 'front'."
    if (controls & {"aircon", "hue_light"}) and (controls & {"all", "back", "front"}):
        return (
            "Invalid running combo: split agents (aircon/hue_light) "
            "cannot run with clubhouse agents (all/back/front)."
        )
    return (
        f"Invalid running combo: {sorted(controls)}. Allowed: aircon, hue_light, "
        "aircon+hue_light, all, back, front, back+front."
    )


class LiveSensorStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream_agent_ids: Set[str] = set()
        self._stream_agent_last_seen: Dict[str, float] = {}
        # Last flattened map per MQTT source agent (virtual/<id>/to/context/updateContext).
        # Device agents often publish partial sensor sets; merging avoids flicker when they
        # overwrite a full dashboard snapshot.
        self._per_agent_flat: Dict[str, Dict[str, Any]] = {}
        self._per_agent_payload: Dict[str, Dict[str, Any]] = {}
        self._latest: Dict[str, Any] = {
            "agent_id": "unknown",
            "topic": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {},
            "payload_by_agent": {},
            "flattened": {},
            "caption": "",
            "caption_by_room": {ROOM_ID_N1_LAB: "", ROOM_ID_LIVING_ROOM: ""},
        }

    def _merge_flattened(self) -> Dict[str, Any]:
        """Merge per-agent flats; non-dashboard agents first, then dashboard (wins on key clashes)."""
        if not self._per_agent_flat:
            return {}
        ordered = sorted(
            self._per_agent_flat.keys(),
            key=lambda a: (0, a) if a != "dashboard" else (1, a),
        )
        merged: Dict[str, Any] = {}
        for aid in ordered:
            part = self._per_agent_flat.get(aid) or {}
            if isinstance(part, dict):
                merged.update(part)
        return merged

    def _payload_for_api(self) -> Dict[str, Any]:
        if "dashboard" in self._per_agent_payload:
            p = self._per_agent_payload["dashboard"]
            return dict(p) if isinstance(p, dict) else {}
        for aid in sorted(self._per_agent_payload.keys()):
            p = self._per_agent_payload[aid]
            if isinstance(p, dict):
                return dict(p)
        return {}

    def _payloads_by_agent_for_api(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for aid in sorted(self._per_agent_payload.keys()):
            payload = self._per_agent_payload.get(aid)
            if isinstance(payload, dict):
                out[aid] = dict(payload)
        return out

    def update_stream_frame(
        self,
        agent_id: str,
        topic: str,
        timestamp_iso: str,
        payload: Dict[str, Any],
        flattened: Dict[str, Any],
    ) -> None:
        """Record one agent's updateContext frame and expose a merged flattened view."""
        with self._lock:
            latest_caption = self._latest.get("caption", "")
            latest_caption_by_room = dict(self._latest.get("caption_by_room", {}))
            if isinstance(flattened, dict):
                self._per_agent_flat[agent_id] = dict(flattened)
            if isinstance(payload, dict):
                self._per_agent_payload[agent_id] = dict(payload)
            merged_flat = self._merge_flattened()
            self._latest = {
                "agent_id": agent_id,
                "topic": topic,
                "timestamp": timestamp_iso,
                "payload": self._payload_for_api(),
                "payload_by_agent": self._payloads_by_agent_for_api(),
                "flattened": merged_flat,
                "merged_agent_count": len(self._per_agent_flat),
                "caption": latest_caption,
                "caption_by_room": latest_caption_by_room,
            }

    def update(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            latest_caption = self._latest.get("caption", "")
            latest_caption_by_room = dict(self._latest.get("caption_by_room", {}))
            self._latest = snapshot
            if "caption" not in self._latest:
                self._latest["caption"] = latest_caption
            if "caption_by_room" not in self._latest:
                self._latest["caption_by_room"] = latest_caption_by_room
            if "payload_by_agent" not in self._latest:
                self._latest["payload_by_agent"] = self._payloads_by_agent_for_api()

    def get(self) -> Dict[str, Any]:
        with self._lock:
            snapshot = dict(self._latest)
            snapshot["caption_by_room"] = dict(self._latest.get("caption_by_room", {}))
            return snapshot

    def set_caption(self, caption_text: str) -> None:
        with self._lock:
            self._latest["caption"] = caption_text

    def set_captions(self, captions_by_room: Dict[str, str], default_room_id: str = ROOM_ID_N1_LAB) -> None:
        with self._lock:
            merged = dict(self._latest.get("caption_by_room", {}))
            for room_id, caption_text in (captions_by_room or {}).items():
                if room_id in ROOM_IDS and isinstance(caption_text, str):
                    merged[room_id] = caption_text
            self._latest["caption_by_room"] = merged
            default_caption = merged.get(default_room_id, "")
            if default_caption:
                self._latest["caption"] = default_caption
            elif merged:
                first_non_empty = next((v for v in merged.values() if v), "")
                self._latest["caption"] = first_non_empty

    def note_stream_agent(self, agent_id: str) -> None:
        if not agent_id or agent_id == "unknown":
            return
        with self._lock:
            self._stream_agent_ids.add(agent_id)
            self._stream_agent_last_seen[agent_id] = time.time()

    def stream_agent_ids(self) -> List[str]:
        with self._lock:
            now = time.time()
            active: Set[str] = set()
            for aid in list(self._stream_agent_ids):
                last_seen = self._stream_agent_last_seen.get(aid, 0.0)
                if (now - last_seen) <= _ACTIVE_AGENT_TTL_SEC:
                    active.add(aid)
            return sorted(active)

    def stream_merge_agent_count(self) -> int:
        with self._lock:
            return len(self._per_agent_flat)


class NotificationStore:
    """Append-only in-memory timeline of UX notifications.

    Drives the dashboard "Notification" tab. Used by the conditional-failover
    simulator to post the demo's three-step status sequence (failure detected,
    notifying maintenance, replacement applied). Append-only by design — the
    UI shows history in order so an operator can review the sequence after
    the fact, not just the latest line. /clear is provided for demo resets.
    """

    DEFAULT_MAX_ENTRIES = 500

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._lock = threading.Lock()
        self._entries: List[Dict[str, Any]] = []
        self._next_id = 1
        self._max_entries = max(1, int(max_entries))

    def append(self, message: str, source: Optional[str] = None,
               level: str = "info", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        text = (message or "").strip()
        if not text:
            raise ValueError("message must be non-empty")
        entry = {
            "id": 0,  # filled under lock
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": text,
            "source": (source or "system").strip() or "system",
            "level": (level or "info").strip().lower() or "info",
            "metadata": metadata if isinstance(metadata, dict) else {},
        }
        with self._lock:
            entry["id"] = self._next_id
            self._next_id += 1
            self._entries.append(entry)
            # Cap memory; oldest entries fall off when sustained traffic exceeds the cap.
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries:]
            return dict(entry)

    def list(self, since_id: int = 0, limit: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            entries = [e for e in self._entries if e["id"] > since_id]
            if isinstance(limit, int) and limit > 0:
                entries = entries[-limit:]
            return {
                "entries": [dict(e) for e in entries],
                "next_id": self._next_id,
                "total": len(self._entries),
            }

    def clear(self) -> Dict[str, Any]:
        with self._lock:
            removed = len(self._entries)
            self._entries = []
            return {"cleared": removed, "next_id": self._next_id}


class DashboardOpsClient:
    """Bridge UI HTTP actions to existing dashboard/context MQTT command topics."""

    CONTROL_COMMAND_TOPIC = "dashboard/control/command"
    CONTROL_RESULT_TOPIC = "dashboard/control/result"
    CONTROL_MODE_COMMAND_TOPIC = "dashboard/control/mode/command"
    CONTROL_MODE_RESULT_TOPIC = "dashboard/control/mode/result"
    DASHBOARD_STATE_TOPIC = "dashboard/context/state"
    RULES_COMMAND_TOPIC = "context/rules/command"
    RULES_RESULT_TOPIC = "context/rules/result"
    THRESHOLD_COMMAND_TOPIC = "dashboard/threshold/command"
    THRESHOLD_RESULT_TOPIC = "dashboard/threshold/result"
    SENSOR_CONFIG_COMMAND_TOPIC = "dashboard/sensor/config/command"
    SENSOR_CONFIG_RESULT_TOPIC = "dashboard/sensor/config/result"

    def __init__(self, mqtt_broker: str, mqtt_port: int) -> None:
        self._mqtt_broker = mqtt_broker
        self._mqtt_port = mqtt_port
        self._pending_lock = threading.Lock()
        self._pending_events: Dict[str, threading.Event] = {}
        self._pending_results: Dict[str, Dict[str, Any]] = {}
        self._control_mode_lock = threading.Lock()
        self._control_mode_cache = "unknown"
        self._client = mqtt.Client(
            client_id=f"sensor-3d-ctrl-{uuid.uuid4().hex[:8]}",
            protocol=mqtt.MQTTv311,
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect(self._mqtt_broker, self._mqtt_port, keepalive=60)
        self._client.loop_start()

    def _on_connect(self, client, userdata, flags, rc, _properties=None):
        if rc == 0:
            for topic in (
                self.CONTROL_RESULT_TOPIC,
                self.CONTROL_MODE_RESULT_TOPIC,
                self.RULES_RESULT_TOPIC,
                self.THRESHOLD_RESULT_TOPIC,
                self.SENSOR_CONFIG_RESULT_TOPIC,
                self.DASHBOARD_STATE_TOPIC,
            ):
                client.subscribe(topic, qos=1)
                logger.info("Ops MQTT subscribed: %s", topic)
        else:
            logger.error("Ops MQTT connect failed rc=%s", rc)

    def _set_cached_control_mode(self, mode: Any) -> None:
        if mode not in {"manual", "automated"}:
            return
        with self._control_mode_lock:
            self._control_mode_cache = str(mode)

    def get_cached_control_mode(self) -> str:
        with self._control_mode_lock:
            return self._control_mode_cache

    def _on_message(self, _client, _userdata, msg):
        try:
            event = json.loads(msg.payload.decode("utf-8"))
            if msg.topic == self.DASHBOARD_STATE_TOPIC:
                summary = ((event.get("payload") or {}).get("summary") or {})
                self._set_cached_control_mode(summary.get("control_mode"))
                return
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                return
            self._set_cached_control_mode(payload.get("mode"))
            cmd_id = payload.get("command_id")
            if not cmd_id:
                return
            with self._pending_lock:
                ev = self._pending_events.get(cmd_id)
                if ev is not None:
                    self._pending_results[cmd_id] = payload
                    ev.set()
        except Exception:
            pass

    def _publish_and_wait(self, topic: str, body: Dict[str, Any], command_id: str, timeout_sec: float) -> Optional[Dict[str, Any]]:
        ev = threading.Event()
        with self._pending_lock:
            self._pending_events[command_id] = ev
            self._pending_results.pop(command_id, None)
        try:
            self._client.publish(topic, json.dumps(body), qos=1)
            ok = ev.wait(timeout=timeout_sec)
            with self._pending_lock:
                result = self._pending_results.pop(command_id, None)
                self._pending_events.pop(command_id, None)
            if not ok:
                return None
            return result
        except Exception:
            with self._pending_lock:
                self._pending_events.pop(command_id, None)
                self._pending_results.pop(command_id, None)
            raise

    def send_control_command(
        self,
        agent_id: str,
        action_name: str,
        *,
        parameters: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 10.0,
        dashboard_entity_id: str = "sensor-3d-ui",
    ) -> Dict[str, Any]:
        command_id = f"ui-{uuid.uuid4().hex[:8]}"
        body = {
            "event": {
                "id": command_id,
                "timestamp": list(time.gmtime()),
                "type": "dashboardCommand",
                "location": None,
                "contextType": None,
                "priority": "High",
            },
            "source": {"entityType": "dashboard", "entityId": dashboard_entity_id},
            "target": {"entityType": "virtualAgent", "entityId": agent_id},
            "payload": {
                "agent_id": agent_id,
                "action_name": action_name,
                "priority": "High",
            },
        }
        if parameters:
            body["payload"]["parameters"] = parameters
        try:
            result = self._publish_and_wait(self.CONTROL_COMMAND_TOPIC, body, command_id, timeout_sec=timeout_sec)
            if result is None:
                return {
                    "success": False,
                    "command_id": command_id,
                    "message": f"No reply on {self.CONTROL_RESULT_TOPIC} within {timeout_sec}s (is ContextRuleManager running?)",
                    "agent_id": agent_id,
                }
            return {
                "success": bool(result.get("success")),
                "command_id": command_id,
                "message": str(result.get("message", "")),
                "agent_id": result.get("agent_id", agent_id),
                "action_event_id": result.get("action_event_id"),
            }
        except Exception as exc:
            with self._pending_lock:
                self._pending_events.pop(command_id, None)
                self._pending_results.pop(command_id, None)
            return {
                "success": False,
                "command_id": command_id,
                "message": str(exc),
                "agent_id": agent_id,
            }

    def send_control_mode_command(
        self,
        *,
        mode: Optional[str] = None,
        action: str = "set",
        timeout_sec: float = 10.0,
        dashboard_entity_id: str = "sensor-3d-ui",
    ) -> Dict[str, Any]:
        command_id = f"mode-{uuid.uuid4().hex[:8]}"
        payload: Dict[str, Any] = {"action": action}
        if mode is not None:
            payload["mode"] = mode
        body = {
            "event": {
                "id": command_id,
                "timestamp": list(time.gmtime()),
                "type": "dashboardControlModeCommand",
                "location": None,
                "contextType": None,
                "priority": "High",
            },
            "source": {"entityType": "dashboard", "entityId": dashboard_entity_id},
            "payload": payload,
        }
        try:
            result = self._publish_and_wait(self.CONTROL_MODE_COMMAND_TOPIC, body, command_id, timeout_sec=timeout_sec)
            if result is None:
                return {
                    "success": False,
                    "command_id": command_id,
                    "message": f"No reply on {self.CONTROL_MODE_RESULT_TOPIC} within {timeout_sec}s",
                    "mode": self.get_cached_control_mode(),
                }
            resolved_mode = str(result.get("mode", self.get_cached_control_mode()))
            self._set_cached_control_mode(resolved_mode)
            return {
                "success": bool(result.get("success")),
                "command_id": command_id,
                "message": str(result.get("message", "")),
                "mode": resolved_mode,
            }
        except Exception as exc:
            return {
                "success": False,
                "command_id": command_id,
                "message": str(exc),
                "mode": self.get_cached_control_mode(),
            }

    def set_control_mode(self, mode: str, *, timeout_sec: float = 10.0) -> Dict[str, Any]:
        return self.send_control_mode_command(mode=mode, action="set", timeout_sec=timeout_sec)

    def get_control_mode(self, *, timeout_sec: float = 5.0) -> Dict[str, Any]:
        return self.send_control_mode_command(action="get", timeout_sec=timeout_sec)

    def send_rules_command(
        self,
        *,
        action: str,
        rule_files: Optional[List[str]] = None,
        preset_name: Optional[str] = None,
        timeout_sec: float = 10.0,
    ) -> Dict[str, Any]:
        command_id = f"rules-{uuid.uuid4().hex[:8]}"
        effective_action = "switch" if action == "load" else action
        effective_rule_files = list(rule_files or [])
        # Demo policy: one active rule file per apply request.
        if len(effective_rule_files) > 1:
            effective_rule_files = effective_rule_files[:1]
        body = {
            "event": {
                "id": command_id,
                "timestamp": list(time.gmtime()),
                "type": "rulesCommand",
                "location": None,
                "contextType": None,
                "priority": "High",
            },
            "source": {"entityType": "dashboard", "entityId": "sensor-3d-ui"},
            "payload": {
                "action": effective_action,
                "rule_files": effective_rule_files,
                "preset_name": preset_name,
            },
        }
        try:
            result = self._publish_and_wait(self.RULES_COMMAND_TOPIC, body, command_id, timeout_sec=timeout_sec)
            if result is None:
                return {
                    "success": False,
                    "command_id": command_id,
                    "message": f"No reply on {self.RULES_RESULT_TOPIC} within {timeout_sec}s",
                }
            return {
                "success": bool(result.get("success")),
                "command_id": command_id,
                "message": str(result.get("message", "")),
                "loaded_files": result.get("loaded_files", []),
                "action": effective_action,
            }
        except Exception as exc:
            return {"success": False, "command_id": command_id, "message": str(exc)}

    def send_threshold_command(
        self,
        *,
        agent_id: str,
        threshold_type: str,
        threshold: float,
        timeout_sec: float = 10.0,
    ) -> Dict[str, Any]:
        command_id = f"thr-{uuid.uuid4().hex[:8]}"
        body = {
            "event": {
                "id": command_id,
                "timestamp": list(time.gmtime()),
                "type": "dashboardThresholdCommand",
                "location": None,
                "contextType": None,
                "priority": "High",
            },
            "source": {"entityType": "dashboard", "entityId": "sensor-3d-ui"},
            "target": {"entityType": "virtualAgent", "entityId": agent_id},
            "payload": {
                "agent_id": agent_id,
                "threshold_type": threshold_type,
                "config": {"threshold": threshold},
            },
        }
        try:
            result = self._publish_and_wait(self.THRESHOLD_COMMAND_TOPIC, body, command_id, timeout_sec=timeout_sec)
            if result is None:
                return {
                    "success": False,
                    "command_id": command_id,
                    "message": f"No reply on {self.THRESHOLD_RESULT_TOPIC} within {timeout_sec}s",
                    "agent_id": agent_id,
                    "threshold_type": threshold_type,
                }
            return {
                "success": bool(result.get("success")),
                "command_id": command_id,
                "message": str(result.get("message", "")),
                "agent_id": result.get("agent_id", agent_id),
                "threshold_type": result.get("threshold_type", threshold_type),
                "current_config": result.get("current_config", {}),
            }
        except Exception as exc:
            return {
                "success": False,
                "command_id": command_id,
                "message": str(exc),
                "agent_id": agent_id,
                "threshold_type": threshold_type,
            }

    def send_sensor_config_command(
        self,
        *,
        target_agent_id: str,
        action: str,
        sensor_config: Optional[Dict[str, List[str]]] = None,
        timeout_sec: float = 10.0,
    ) -> Dict[str, Any]:
        command_id = f"sc-{uuid.uuid4().hex[:8]}"
        body = {
            "event": {
                "id": command_id,
                "timestamp": list(time.gmtime()),
                "type": "sensorConfig",
                "location": None,
                "contextType": None,
                "priority": "High",
            },
            "source": {"entityType": "dashboard", "entityId": "sensor-3d-ui"},
            "payload": {
                "target_agent_id": target_agent_id,
                "action": action,
                "sensor_config": sensor_config or {},
            },
        }
        try:
            result = self._publish_and_wait(self.SENSOR_CONFIG_COMMAND_TOPIC, body, command_id, timeout_sec=timeout_sec)
            if result is None:
                return {
                    "success": False,
                    "command_id": command_id,
                    "message": f"No reply on {self.SENSOR_CONFIG_RESULT_TOPIC} within {timeout_sec}s",
                    "agent_id": target_agent_id,
                }
            return {
                "success": bool(result.get("success")),
                "command_id": command_id,
                "message": str(result.get("message", "")),
                "agent_id": result.get("agent_id", target_agent_id),
                "action": result.get("action", action),
                "current_sensors": result.get("current_sensors", []),
            }
        except Exception as exc:
            return {
                "success": False,
                "command_id": command_id,
                "message": str(exc),
                "agent_id": target_agent_id,
            }

    def close(self) -> None:
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass


class LiveCaptionEngine:
    def __init__(
        self,
        store: LiveSensorStore,
        window_sec: int = 30,
        model_name: str = "qwenlm",
        gpus: int = 1,
        milvus_store=None,
        embedding_model: str = "jinaai/jina-clip-v1",
    ) -> None:
        self.store = store
        self.window_sec = max(1, int(window_sec))
        self.model_name = model_name
        self.gpus = gpus
        self._lock = threading.Lock()
        self._active_window_start_unix: Optional[float] = None
        self._active_records: List[Dict[str, Any]] = []
        self._pending_windows: List[Tuple[float, float, List[Dict[str, Any]]]] = []
        self._stop_event = threading.Event()
        self._llm = None
        self._model_init_failed = False
        self._milvus_store = milvus_store
        self._embedding_model = embedding_model
        self._milvus_queue: Optional[Any] = None
        self._milvus_worker: Optional[threading.Thread] = None
        if milvus_store:
            import queue
            self._milvus_queue = queue.Queue(maxsize=200)
            self._milvus_worker = threading.Thread(
                target=self._milvus_write_loop, daemon=True, name="milvus-writer"
            )
            self._milvus_worker.start()

    def _get_sensor_status(self, sensor: Dict[str, Any]) -> Optional[str]:
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

    def _summarize_window_records(self, window_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        latest_sensor: Dict[str, Dict[str, Any]] = {}
        per_sensor_last_status: Dict[str, Optional[str]] = {}
        status_counts: Dict[str, Dict[str, int]] = {}
        numeric_stats: Dict[str, Dict[str, float]] = {}
        status_change_count = 0

        for rec in sorted(window_records, key=lambda x: x["received_at_unix"]):
            sensors = rec.get("payload_sensors", {}) or {}
            for sensor_id, sensor_data in sensors.items():
                old_status = per_sensor_last_status.get(sensor_id)
                new_status = self._get_sensor_status(sensor_data)
                if old_status is not None and new_status is not None and old_status != new_status:
                    status_change_count += 1
                if new_status is not None:
                    per_sensor_last_status[sensor_id] = new_status
                latest_sensor[sensor_id] = sensor_data

        for sensor_data in latest_sensor.values():
            sensor_type = sensor_data.get("sensor_type", "unknown")
            status = self._get_sensor_status(sensor_data) or "unknown"
            status_counts.setdefault(sensor_type, {})
            status_counts[sensor_type][status] = status_counts[sensor_type].get(status, 0) + 1

            value = sensor_data.get("value")
            if isinstance(value, (int, float)):
                numeric_stats.setdefault(
                    sensor_type,
                    {"count": 0, "min": float(value), "max": float(value), "sum": 0.0},
                )
                stat = numeric_stats[sensor_type]
                stat["count"] += 1
                stat["sum"] += float(value)
                stat["min"] = min(stat["min"], float(value))
                stat["max"] = max(stat["max"], float(value))

        for stat in numeric_stats.values():
            if stat["count"] > 0:
                stat["avg"] = round(stat["sum"] / stat["count"], 3)
            stat.pop("sum", None)

        return {
            "status_counts": status_counts,
            "numeric_stats": numeric_stats,
            "status_change_count": status_change_count,
        }

    def _room_context_block(self, room_id: str) -> str:
        if room_id == ROOM_ID_LIVING_ROOM:
            return (
                "Room context:\n"
                "- Room identity: living_room.\n"
                "- Layout: multi-zone lounge with upper and lower seating areas.\n"
                "- Main objects: two sofas, two lounge tables, four chairs, one TV wall, one staircase zone.\n"
                "- Sensor scope policy: use only the mapped living-room subset (2 distance, 2 infrared, 6 motion, 1 light, 1 temperature).\n"
                "- Caption emphasis: describe zone-level activity and comfort in domestic/living-room language.\n\n"
            )
        return (
            "Room context:\n"
            "- Room identity: n1_lab.\n"
            "- Layout: smart meeting room with one long central table and dense seating around it.\n"
            "- Main objects: twelve chairs, one meeting table, two AC units, one TV/front display area, one access door.\n"
            "- Sensor scope policy: use the full shared stream available in this monitoring window.\n"
            "- Caption emphasis: describe meeting-room occupancy, comfort, and operational state.\n\n"
        )

    def _filter_sensors_for_room(self, payload_sensors: Dict[str, Any], room_id: str) -> Dict[str, Any]:
        sensors = payload_sensors or {}
        if room_id == ROOM_ID_LIVING_ROOM:
            return {sid: sdata for sid, sdata in sensors.items() if sid in LIVING_ROOM_SENSOR_IDS}
        return dict(sensors)

    def _build_prompt(self, start_iso: str, end_iso: str, summary: Dict[str, Any], room_id: str) -> str:
        status_counts = summary.get("status_counts", {}) if isinstance(summary, dict) else {}
        numeric_stats = summary.get("numeric_stats", {}) if isinstance(summary, dict) else {}
        status_change_count = summary.get("status_change_count", 0) if isinstance(summary, dict) else 0
        sensor_count_by_type: Dict[str, int] = {}
        if isinstance(numeric_stats, dict):
            for sensor_type, stat in numeric_stats.items():
                if isinstance(stat, dict):
                    c = stat.get("count")
                    if isinstance(c, (int, float)):
                        sensor_count_by_type[sensor_type] = int(c)
        if not sensor_count_by_type and isinstance(status_counts, dict):
            for sensor_type, statuses in status_counts.items():
                if isinstance(statuses, dict):
                    sensor_count_by_type[sensor_type] = int(
                        sum(v for v in statuses.values() if isinstance(v, (int, float)))
                    )
        total_sensor_count = sum(sensor_count_by_type.values())
        return (
            "You are a smart-room monitoring assistant.\n"
            "Write exactly one concise operational paragraph for this monitoring window.\n\n"
            f"{self._room_context_block(room_id)}"
            "Sensor coverage in this window:\n"
            f"- Sensor inventory in this window (count by type): {json.dumps(sensor_count_by_type, ensure_ascii=False)}.\n"
            f"- Total sensors represented: {total_sensor_count}.\n\n"
            "Sensor-role context:\n"
            "- Occupancy/presence evidence: distance, infrared, motion, activity.\n"
            "- Thermal comfort evidence: temperature.\n"
            "- Lighting condition evidence: light.\n"
            "- Room access evidence: door.\n"
            "- AC controller posture/angle evidence: tilt.\n\n"
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
            "- Suggested wording for dynamic scenes: \"conditions are fluctuating\", "
            "\"state transitions indicate ongoing movement\", or \"activity is dynamic\".\n"
            "- If evidence is weak or mixed, use cautious wording like likely/may.\n\n"
            "In-context examples:\n"
            "Example A input:\n"
            "status_counts={\"distance\":{\"near\":2},\"infrared\":{\"near\":1},\"motion\":{\"motion\":2},"
            "\"activity\":{\"active\":3},\"temperature\":{\"hot\":1},\"light\":{\"no_light\":1},\"door\":{\"open\":1}}\n"
            "status_change_count=4\n"
            "Example A caption:\n"
            "Multiple occupancy signals (near proximity plus active motion/activity) indicate likely multi-person presence; the door is open, temperature trends hot so cooling is recommended, and lighting is low so switching lights on is advisable, with several recent state changes suggesting ongoing movement.\n\n"
            "Example B input:\n"
            "status_counts={\"distance\":{\"far\":4},\"infrared\":{\"far\":2},\"motion\":{\"no_motion\":6},"
            "\"activity\":{\"inactive\":8},\"temperature\":{\"normal\":1},\"light\":{\"indoor_light\":1},\"door\":{\"closed\":1}}\n"
            "status_change_count=0\n"
            "Example B caption:\n"
            "Room appears likely unoccupied with no active proximity or motion/activity signals; door remains closed, temperature is normal so no HVAC adjustment is needed, and indoor lighting appears sufficient with stable conditions.\n\n"
            "Example C input:\n"
            "status_counts={\"activity\":{\"active\":1,\"inactive\":5},\"motion\":{\"no_motion\":4},"
            "\"temperature\":{\"cold\":1},\"light\":{\"sunlight\":1},\"door\":{\"closed\":1}}\n"
            "status_change_count=1\n"
            "Example C caption:\n"
            "There is limited but non-zero occupancy evidence (one active activity signal), so brief presence is possible; the room is cold and may need heating, while strong daylight suggests additional artificial lighting is unnecessary at the moment.\n\n"
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
            f"Window: {start_iso} to {end_iso}\n"
            f"status_counts: {json.dumps(status_counts, ensure_ascii=False)}\n"
            f"status_change_count: {status_change_count}\n\n"
            "Caption:"
        )

    def _ensure_model(self):
        if self._llm is not None:
            return self._llm
        if self._model_init_failed:
            return None
        if init_model is None:
            self._model_init_failed = True
            logger.error("Caption model loader import failed; cannot initialize LLM.")
            return None
        try:
            logger.info("Initializing caption model=%s gpus=%s", self.model_name, self.gpus)
            self._llm = init_model(self.model_name, self.gpus)
            return self._llm
        except Exception as e:
            self._model_init_failed = True
            logger.error("Caption model init failed: %s", e, exc_info=True)
            return None

    def get_llm(self):
        """Return the shared LLM instance (used by RAG query engine)."""
        return self._ensure_model()

    def _milvus_write_loop(self) -> None:
        """Worker thread that drains the queue and writes to Milvus."""
        while not self._stop_event.is_set():
            try:
                item = self._milvus_queue.get(timeout=1.0)
            except Exception:
                continue
            try:
                from rag.embeddings import embed as _embed
                caption = item["caption"]
                vec = _embed(caption, model_name=self._embedding_model)
                item["embedding"] = vec
                self._milvus_store.upsert(item)
                logger.debug("Milvus upsert: room=%s ts=%.1f", item.get("room_id"), item.get("ts_unix"))
            except Exception as e:
                logger.warning("Milvus write failed (dropping): %s", e)

    def _enqueue_milvus_write(
        self, room_id: str, start_unix: float, end_unix: float,
        start_iso: str, caption: str, summary_json: str,
    ) -> None:
        """Enqueue a caption for async Milvus write. Never blocks the caption loop."""
        if not self._milvus_store or not self._milvus_queue:
            return
        record = {
            "room_id": room_id,
            "ts_unix": start_unix,
            "ts_iso": start_iso,
            "window_end_unix": end_unix,
            "caption": caption,
            "summary_json": summary_json[:8192],
        }
        try:
            self._milvus_queue.put_nowait(record)
        except Exception:
            logger.warning("Milvus write queue full, dropping caption for room=%s ts=%.1f", room_id, start_unix)

    def ingest_record(self, received_at_unix: float, payload_sensors: Dict[str, Any]) -> None:
        record = {"received_at_unix": received_at_unix, "payload_sensors": payload_sensors or {}}
        with self._lock:
            if self._active_window_start_unix is None:
                self._active_window_start_unix = received_at_unix
            while received_at_unix >= self._active_window_start_unix + self.window_sec:
                window_end = self._active_window_start_unix + self.window_sec
                self._pending_windows.append(
                    (self._active_window_start_unix, window_end, list(self._active_records))
                )
                self._active_records = []
                self._active_window_start_unix = window_end
            self._active_records.append(record)

    def _next_window(self) -> Optional[Tuple[float, float, List[Dict[str, Any]]]]:
        with self._lock:
            if not self._pending_windows:
                return None
            return self._pending_windows.pop(0)

    def _generate_caption_for_window(
        self, start_unix: float, end_unix: float, records: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        if not records:
            return {}
        start_iso = datetime.fromtimestamp(start_unix, tz=timezone.utc).isoformat()
        end_iso = datetime.fromtimestamp(end_unix, tz=timezone.utc).isoformat()
        llm = self._ensure_model()
        captions_by_room: Dict[str, str] = {}

        for room_id in ROOM_IDS:
            filtered_records: List[Dict[str, Any]] = []
            for rec in records:
                filtered_sensors = self._filter_sensors_for_room(rec.get("payload_sensors", {}), room_id)
                if not filtered_sensors:
                    continue
                filtered_records.append(
                    {
                        "received_at_unix": rec.get("received_at_unix", 0.0),
                        "payload_sensors": filtered_sensors,
                    }
                )

            if not filtered_records:
                captions_by_room[room_id] = (
                    f"No mapped sensors observed from {start_iso} to {end_iso} for room '{room_id}'."
                )
                continue

            summary = self._summarize_window_records(filtered_records)
            if llm is None:
                captions_by_room[room_id] = (
                    f"Events observed from {start_iso} to {end_iso}. "
                    "Caption model is unavailable."
                )
                continue
            prompt = self._build_prompt(start_iso, end_iso, summary, room_id=room_id)
            response = llm.generate_response({"text": prompt}, max_new_tokens=256, temperature=0.4)
            caption_text = " ".join((response or "").strip().split())
            captions_by_room[room_id] = caption_text
            # Enqueue async Milvus write
            self._enqueue_milvus_write(
                room_id=room_id,
                start_unix=start_unix,
                end_unix=end_unix,
                start_iso=start_iso,
                caption=caption_text,
                summary_json=json.dumps(summary, ensure_ascii=False),
            )
        return captions_by_room

    def run_forever(self) -> None:
        while not self._stop_event.is_set():
            item = self._next_window()
            if item is None:
                time.sleep(0.2)
                continue
            start_unix, end_unix, records = item
            try:
                captions_by_room = self._generate_caption_for_window(start_unix, end_unix, records)
                if captions_by_room:
                    self.store.set_captions(captions_by_room, default_room_id=ROOM_ID_N1_LAB)
                    logger.info(
                        "Updated room captions for window %s - %s (records=%d, rooms=%s)",
                        start_unix,
                        end_unix,
                        len(records),
                        ",".join(sorted(captions_by_room.keys())),
                    )
            except Exception as e:
                logger.error("Caption generation failed: %s", e, exc_info=True)

    def stop(self) -> None:
        self._stop_event.set()


class SourcePayloadBridge:
    def __init__(
        self,
        mqtt_broker: str,
        mqtt_port: int,
        store: LiveSensorStore,
        caption_engine: Optional[LiveCaptionEngine] = None,
    ):
        self.store = store
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.caption_engine = caption_engine
        self._connected = False
        self.source_client = mqtt.Client(
            client_id=f"sensor-3d-source-{uuid.uuid4().hex[:8]}",
            clean_session=True,
        )
        self.source_client.on_connect = self._on_source_connect
        self.source_client.on_disconnect = self._on_source_disconnect
        self.source_client.on_message = self._on_source_message
        try:
            self.source_client.connect(self.mqtt_broker, self.mqtt_port)
            self._connected = True
        except Exception as e:
            logger.warning(
                "Initial MQTT connect failed (%s). HTTP server will still start; retrying in background.",
                e,
            )

    def _on_source_connect(self, client, userdata, flags, rc):
        if rc == 0:
            topic = "virtual/+/to/context/updateContext"
            client.subscribe(topic, qos=1)
            self._connected = True
            logger.info("Connected to source MQTT and subscribed: %s", topic)
        else:
            self._connected = False
            logger.error("Failed to connect to source MQTT broker rc=%s", rc)

    def _on_source_disconnect(self, client, userdata, rc):
        self._connected = False
        if rc != 0:
            logger.warning("MQTT disconnected unexpectedly rc=%s", rc)

    def _str_to_num(self, value: Any) -> float:
        if value in ("on", "true", True):
            return 1.0
        if value in ("off", "false", False):
            return 0.0
        if value in ("near", "motion", "active", "open", "bright", "warm"):
            return 1.0
        if value in ("far", "no_motion", "idle", "closed", "dim", "normal", "unknown"):
            return 0.0
        try:
            return float(value)
        except Exception:
            return 0.0

    def _flatten_nested_dict(self, attrs: Dict[str, Any], prefix: str, value: Any) -> None:
        """Flatten nested state metadata like activity_summary.motion_active."""
        if isinstance(value, dict):
            for k, v in value.items():
                next_prefix = f"{prefix}_{k}" if prefix else str(k)
                self._flatten_nested_dict(attrs, next_prefix, v)
            return
        attrs[prefix] = self._str_to_num(value)

    def _flatten_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        state = payload.get("state", {})
        sensors = payload.get("sensors", {})
        attrs: Dict[str, Any] = {}

        # Flatten state fields, including nested dicts like activity_summary.
        for key, value in state.items():
            if isinstance(value, dict):
                self._flatten_nested_dict(attrs, key, value)
            else:
                attrs[key] = self._str_to_num(value)

        for sensor_name, sensor_info in sensors.items():
            value = sensor_info.get("value")
            unit = sensor_info.get("unit", "")
            safe_name = sensor_name.replace("-", "_")
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

            metadata = sensor_info.get("metadata", {})
            if "threshold_cm" in metadata:
                try:
                    attrs[f"{safe_name}_threshold_cm"] = float(metadata["threshold_cm"])
                except Exception:
                    pass
            if "distance_cm" in metadata:
                try:
                    attrs[f"{safe_name}_distance_cm"] = float(metadata["distance_cm"])
                except Exception:
                    pass
            # Flatten scalar metadata for each sensor (status, thresholds, booleans).
            for meta_key, meta_val in metadata.items():
                if isinstance(meta_val, (dict, list)):
                    continue
                mk = f"{safe_name}_{meta_key}".replace("-", "_")
                if mk in attrs:
                    continue
                attrs[mk] = self._str_to_num(meta_val)

        # Compatibility aliases for the current 3D scene mapping.
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

    def _extract_agent_id_from_topic(self, topic: str) -> str:
        parts = topic.split("/")
        if len(parts) >= 2:
            return parts[1]
        return "unknown"

    def _on_source_message(self, client, userdata, msg):
        try:
            received_at_unix = time.time()
            event = MQTTMessage.deserialize(msg.payload.decode())
            payload = event.payload
            agent_id = self._extract_agent_id_from_topic(msg.topic)
            flattened = self._flatten_payload(payload)
            if self.caption_engine is not None:
                self.caption_engine.ingest_record(
                    received_at_unix=received_at_unix,
                    payload_sensors=payload.get("sensors", {}),
                )

            self.store.note_stream_agent(agent_id)
            self.store.update_stream_frame(
                agent_id=agent_id,
                topic=msg.topic,
                timestamp_iso=datetime.now(timezone.utc).isoformat(),
                payload=payload if isinstance(payload, dict) else {},
                flattened=flattened,
            )
            logger.info(
                "Stream frame agent=%s (merged_agents=%d)",
                agent_id,
                self.store.stream_merge_agent_count(),
            )
        except Exception as e:
            logger.error("Error processing MQTT message: %s", e, exc_info=True)

    def run_forever(self):
        while True:
            try:
                if not self._connected:
                    logger.info("Attempting MQTT reconnect to %s:%s", self.mqtt_broker, self.mqtt_port)
                    self.source_client.connect(self.mqtt_broker, self.mqtt_port)
                    self._connected = True
                self.source_client.loop_forever()
            except Exception as e:
                self._connected = False
                logger.warning("MQTT loop error: %s. Retrying in 5s...", e)
                time.sleep(5)


def make_handler(
    store: LiveSensorStore,
    html_path: str,
    ops_client: Optional[DashboardOpsClient] = None,
    caption_engine: Optional[LiveCaptionEngine] = None,
    milvus_store=None,
    rag_enabled: bool = True,
    notifications: Optional[NotificationStore] = None,
):
    # Directory containing the HTML file — static files are served from here
    static_dir = os.path.dirname(os.path.abspath(html_path))
    html_filename = os.path.basename(html_path)
    # Simple token bucket for /api/chat rate limiting: 10 req/min/IP
    _chat_rate: Dict[str, List[float]] = {}
    _CHAT_RATE_LIMIT = 10
    _CHAT_RATE_WINDOW = 60.0

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
            parsed = urlparse(self.path)
            path_only = parsed.path or self.path

            # API endpoint — always takes priority
            if path_only == "/api/latest":
                self._write_json(store.get())
                return
            if path_only == "/api/keys":
                snapshot = store.get()
                flattened = snapshot.get("flattened", {})
                keys = sorted(flattened.keys()) if isinstance(flattened, dict) else []
                self._write_json(
                    {
                        "agent_id": snapshot.get("agent_id", "unknown"),
                        "timestamp": snapshot.get("timestamp", ""),
                        "key_count": len(keys),
                        "keys": keys,
                    }
                )
                return
            if path_only == "/api/control/capabilities":
                control_mode = ops_client.get_cached_control_mode() if ops_client is not None else "unknown"
                caps = build_control_capabilities(store.stream_agent_ids(), control_mode=control_mode)
                caps["control_mqtt_available"] = ops_client is not None
                self._write_json(caps)
                return
            if path_only == "/api/control/mode":
                if ops_client is None:
                    self._write_json(
                        {"success": False, "message": "ops MQTT client unavailable", "mode": "unknown"},
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                timeout_sec = 3.0
                result = ops_client.get_control_mode(timeout_sec=timeout_sec)
                # Keep API stable even when control-mode replies are delayed/missing.
                # UI can still operate with cached/unknown mode instead of surfacing HTTP 503.
                self._write_json(result, status=HTTPStatus.OK)
                return
            if path_only == "/api/notifications":
                # Notification timeline read for the UI panel and simulator scripts.
                # Optional ?since_id=<int> returns only entries newer than that id —
                # lets the UI poll cheaply without resending the full history.
                qs = parsed.query or ""
                since_id = 0
                limit: Optional[int] = None
                for chunk in qs.split("&"):
                    if not chunk:
                        continue
                    k, _, v = chunk.partition("=")
                    if k == "since_id":
                        try:
                            since_id = int(v)
                        except ValueError:
                            since_id = 0
                    elif k == "limit":
                        try:
                            limit = int(v)
                        except ValueError:
                            limit = None
                if notifications is None:
                    self._write_json({"entries": [], "next_id": 1, "total": 0})
                else:
                    self._write_json(notifications.list(since_id=since_id, limit=limit))
                return
            if path_only == "/api/automation/catalog":
                payload = build_rules_catalog_payload()
                payload["agents"] = store.stream_agent_ids()
                control_mode = ops_client.get_cached_control_mode() if ops_client is not None else "unknown"
                payload["control_capabilities"] = build_control_capabilities(
                    store.stream_agent_ids(),
                    control_mode=control_mode,
                )
                snapshot = store.get()
                payload["available_sensor_ids"] = sorted((snapshot.get("payload", {}) or {}).get("sensors", {}).keys())
                self._write_json(payload)
                return

            # Root path — serve the main HTML file
            if path_only == "/" or path_only == "":
                self._write_file(html_path, "text/html; charset=utf-8")
                return

            # Static file serving — serve files from the same directory as the HTML
            # Strip leading slash and prevent directory traversal
            requested_file = path_only.lstrip("/")
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

        def _read_json_body(self) -> Optional[Dict[str, Any]]:
            length_hdr = self.headers.get("Content-Length", "0")
            try:
                length = int(length_hdr)
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return None

        def _handle_chat(self, body: Dict[str, Any]):
            if not rag_enabled or not milvus_store:
                self._write_json(
                    {"error": "RAG disabled"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            # Rate limiting
            client_ip = self.client_address[0]
            now = time.time()
            timestamps = _chat_rate.get(client_ip, [])
            timestamps = [t for t in timestamps if now - t < _CHAT_RATE_WINDOW]
            if len(timestamps) >= _CHAT_RATE_LIMIT:
                self._write_json(
                    {"error": "Rate limit exceeded. Max 10 requests per minute."},
                    status=HTTPStatus.TOO_MANY_REQUESTS,
                )
                return
            timestamps.append(now)
            _chat_rate[client_ip] = timestamps

            message = body.get("message", "").strip()
            room_id = body.get("room_id", "n1_lab")
            if not message:
                self._write_json({"error": "message required"}, status=HTTPStatus.BAD_REQUEST)
                return

            llm = caption_engine.get_llm() if caption_engine else None
            if llm is None:
                self._write_json(
                    {"error": "LLM not available"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return

            try:
                from rag.query_engine import answer as rag_answer
                result = rag_answer(
                    message=message,
                    room_id=room_id,
                    llm=llm,
                    store=milvus_store,
                )
                self._write_json(result)
            except Exception as exc:
                logger.error("Chat endpoint error: %s", exc, exc_info=True)
                self._write_json(
                    {"error": str(exc)},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def do_POST(self):
            parsed = urlparse(self.path)
            path_only = parsed.path or self.path

            # /api/chat is handled separately (doesn't need ops_client)
            if path_only == "/api/chat":
                body = self._read_json_body()
                if body is None:
                    self._write_json({"error": "invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._handle_chat(body)
                return

            # Notification endpoints don't depend on ops_client either; the simulator
            # and dashboard UI both write/read the in-memory timeline.
            if path_only == "/api/notifications":
                body = self._read_json_body()
                if body is None:
                    self._write_json({"error": "invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if notifications is None:
                    self._write_json({"error": "notifications disabled"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                message = body.get("message")
                if not isinstance(message, str) or not message.strip():
                    self._write_json({"error": "message required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                source = body.get("source") if isinstance(body.get("source"), str) else None
                level = body.get("level") if isinstance(body.get("level"), str) else "info"
                metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else None
                try:
                    entry = notifications.append(message, source=source, level=level, metadata=metadata)
                except ValueError as exc:
                    self._write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._write_json({"success": True, "entry": entry})
                return
            if path_only == "/api/notifications/clear":
                if notifications is None:
                    self._write_json({"error": "notifications disabled"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                result = notifications.clear()
                result["success"] = True
                self._write_json(result)
                return

            if path_only not in (
                "/api/control/mode",
                "/api/control/command",
                "/api/automation/rules/apply",
                "/api/automation/threshold/apply",
                "/api/automation/preset/apply",
                "/api/sensor-config/apply",
            ):
                self._write_json({"error": "not found", "path": path_only}, status=HTTPStatus.NOT_FOUND)
                return
            if ops_client is None:
                self._write_json(
                    {"error": "ops MQTT client unavailable (MQTT connect failed)"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            length_hdr = self.headers.get("Content-Length", "0")
            try:
                length = int(length_hdr)
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._write_json({"error": "invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)
                return
            timeout = body.get("timeout_sec", 10.0)
            try:
                timeout_sec = float(timeout)
            except (TypeError, ValueError):
                timeout_sec = 10.0
            timeout_sec = max(1.0, min(timeout_sec, 60.0))
            observed_agents = set(store.stream_agent_ids())
            conflict_message = validate_control_mode(observed_agents)

            def _require_control_mode(expected_mode: str) -> Optional[str]:
                mode_result = ops_client.get_control_mode(timeout_sec=min(timeout_sec, 5.0))
                if not mode_result.get("success"):
                    logger.warning(
                        "Control-mode query unavailable for %s (continuing in compatibility mode): %s",
                        expected_mode,
                        mode_result.get("message") or "unknown error",
                    )
                    return None
                mode_value = mode_result.get("mode")
                if mode_value != expected_mode:
                    return f"control_mode is '{mode_value}'. Switch to '{expected_mode}' first."
                return None

            if path_only == "/api/control/mode":
                requested_mode = body.get("mode")
                if requested_mode not in ("manual", "automated"):
                    self._write_json({"error": "mode must be manual or automated"}, status=HTTPStatus.BAD_REQUEST)
                    return
                result = ops_client.set_control_mode(requested_mode, timeout_sec=timeout_sec)
                # Return 200 with explicit success flag so the UI can render
                # actionable diagnostics without getting stuck on HTTP 503.
                self._write_json(result, status=HTTPStatus.OK)
                return

            if path_only == "/api/control/command":
                if conflict_message:
                    self._write_json({"error": conflict_message}, status=HTTPStatus.BAD_REQUEST)
                    return
                agent_id = body.get("agent_id")
                action_name = body.get("action_name")
                if not isinstance(agent_id, str) or not agent_id.strip():
                    self._write_json({"error": "agent_id required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if action_name not in ("turn_on", "turn_off", "change_mode"):
                    self._write_json({"error": "action_name must be turn_on, turn_off, or change_mode"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if action_name in ("turn_on", "turn_off"):
                    mode_error = _require_control_mode("manual")
                    if mode_error:
                        self._write_json({"error": mode_error}, status=HTTPStatus.BAD_REQUEST)
                        return
                if agent_id not in observed_agents:
                    self._write_json(
                        {"error": f"agent_id {agent_id!r} not in observed stream agents", "agents": sorted(observed_agents)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                parameters = body.get("parameters")
                if action_name == "change_mode":
                    mode = body.get("mode")
                    if not isinstance(mode, str) or not mode.strip():
                        self._write_json({"error": "mode required for change_mode"}, status=HTTPStatus.BAD_REQUEST)
                        return
                    parameters = dict(parameters or {})
                    parameters["mode"] = mode.strip()
                result = ops_client.send_control_command(
                    agent_id.strip(),
                    action_name,
                    parameters=parameters if isinstance(parameters, dict) else None,
                    timeout_sec=timeout_sec,
                )
                self._write_json(result, status=HTTPStatus.OK)
                return

            if path_only == "/api/automation/rules/apply":
                if conflict_message:
                    self._write_json({"error": conflict_message}, status=HTTPStatus.BAD_REQUEST)
                    return
                mode_error = _require_control_mode("automated")
                if mode_error:
                    self._write_json({"error": mode_error}, status=HTTPStatus.BAD_REQUEST)
                    return
                action = "switch"
                selected_agents = body.get("selected_agents", [])
                selected_rule_set_ids = body.get("rule_set_ids", [])
                if not isinstance(selected_agents, list):
                    selected_agents = []
                if not isinstance(selected_rule_set_ids, list):
                    selected_rule_set_ids = []
                if selected_rule_set_ids:
                    selected_rule_set_ids = [selected_rule_set_ids[0]]
                resolved_rule_files = resolve_rule_files(selected_agents, selected_rule_set_ids)
                if not resolved_rule_files:
                    self._write_json(
                        {
                            "error": "No matching fixed rules for selected agents/rule sets",
                            "selected_agents": selected_agents,
                            "rule_set_ids": selected_rule_set_ids,
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                result = ops_client.send_rules_command(
                    action=action,
                    rule_files=resolved_rule_files,
                    timeout_sec=timeout_sec,
                )
                result["resolved_rule_files"] = resolved_rule_files
                self._write_json(result, status=HTTPStatus.OK)
                return

            if path_only == "/api/automation/threshold/apply":
                if conflict_message:
                    self._write_json({"error": conflict_message}, status=HTTPStatus.BAD_REQUEST)
                    return
                mode_error = _require_control_mode("automated")
                if mode_error:
                    self._write_json({"error": mode_error}, status=HTTPStatus.BAD_REQUEST)
                    return
                agent_id = body.get("agent_id")
                threshold_type = body.get("threshold_type")
                threshold = body.get("threshold")
                if not isinstance(agent_id, str) or not agent_id.strip():
                    self._write_json({"error": "agent_id required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if agent_id not in observed_agents:
                    self._write_json(
                        {"error": f"agent_id {agent_id!r} not in observed stream agents", "agents": sorted(observed_agents)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                if threshold_type not in ("temperature", "light"):
                    self._write_json({"error": "threshold_type must be temperature or light"}, status=HTTPStatus.BAD_REQUEST)
                    return
                caps_for_validation = build_control_capabilities(observed_agents, control_mode="automated")
                agent_profile = (caps_for_validation.get("agent_capabilities") or {}).get(agent_id, {})
                supported_types = agent_profile.get("threshold_types") or []
                if not agent_profile.get("threshold"):
                    self._write_json(
                        {"error": f"agent_id {agent_id!r} does not support threshold configuration"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                if threshold_type not in supported_types:
                    self._write_json(
                        {
                            "error": (
                                f"threshold_type {threshold_type!r} not supported by agent {agent_id!r}"
                            ),
                            "supported_threshold_types": supported_types,
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                try:
                    threshold_value = float(threshold)
                except (TypeError, ValueError):
                    self._write_json({"error": "threshold must be numeric"}, status=HTTPStatus.BAD_REQUEST)
                    return
                result = ops_client.send_threshold_command(
                    agent_id=agent_id.strip(),
                    threshold_type=threshold_type,
                    threshold=threshold_value,
                    timeout_sec=timeout_sec,
                )
                self._write_json(result, status=HTTPStatus.OK)
                return

            if path_only == "/api/automation/preset/apply":
                if conflict_message:
                    self._write_json({"error": conflict_message}, status=HTTPStatus.BAD_REQUEST)
                    return
                mode_error = _require_control_mode("automated")
                if mode_error:
                    self._write_json({"error": mode_error}, status=HTTPStatus.BAD_REQUEST)
                    return
                agent_id = body.get("agent_id")
                mode = body.get("mode")
                if not isinstance(agent_id, str) or not agent_id.strip():
                    self._write_json({"error": "agent_id required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if agent_id not in observed_agents:
                    self._write_json(
                        {"error": f"agent_id {agent_id!r} not in observed stream agents", "agents": sorted(observed_agents)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                if not isinstance(mode, str) or not mode.strip():
                    self._write_json({"error": "mode required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if agent_id not in {"all", "back", "front"}:
                    self._write_json(
                        {"error": f"agent_id {agent_id!r} does not support change_mode preset"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                normalized_agent = agent_id.strip()
                normalized_mode = mode.strip().lower()
                clubhouse_rule_map = {
                    ("all", "normal"): "rules/demo_rules/all-normal.ttl",
                    ("all", "clean"): "rules/demo_rules/all-clean.ttl",
                    ("all", "repair"): "rules/demo_rules/repair-clean.ttl",
                    ("back", "read"): "rules/demo_rules/back-read.ttl",
                    ("back", "nap"): "rules/demo_rules/back-nap.ttl",
                    ("front", "read"): "rules/demo_rules/front-read.ttl",
                    ("front", "nap"): "rules/demo_rules/front-nap.ttl",
                }
                rule_file = clubhouse_rule_map.get((normalized_agent, normalized_mode))
                if not rule_file:
                    self._write_json(
                        {
                            "error": (
                                f"Unsupported mode {normalized_mode!r} for clubhouse agent "
                                f"{normalized_agent!r}"
                            )
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return

                # Keep active rules aligned with selected clubhouse mode; otherwise
                # OFF->ON automation keeps following whatever rule file was loaded before.
                rules_result = ops_client.send_rules_command(
                    action="switch",
                    rule_files=[rule_file],
                    timeout_sec=timeout_sec,
                )
                if not rules_result.get("success"):
                    self._write_json(
                        {
                            "success": False,
                            "message": "Failed to switch clubhouse rule before applying mode",
                            "rule_file": rule_file,
                            "rules_result": rules_result,
                        },
                        status=HTTPStatus.OK,
                    )
                    return

                # Do NOT send an extra explicit change_mode for clubhouse here.
                # The manager's rule switch path already runs mode detection from
                # the selected rule filename and dispatches change_mode itself.
                # Sending a second change_mode causes duplicate profile reapply and
                # can produce visible AC command thrashing during demos.
                result = {
                    "success": True,
                    "message": (
                        f"Switched clubhouse rule to {rule_file} "
                        f"(mode {normalized_mode} for agent {normalized_agent})"
                    ),
                    "agent_id": normalized_agent,
                    "mode": normalized_mode,
                    "rule_file": rule_file,
                    "rules_result": rules_result,
                }
                self._write_json(result, status=HTTPStatus.OK)
                return

            if path_only == "/api/sensor-config/apply":
                target_agent_id = body.get("target_agent_id")
                action = body.get("action")
                sensor_config = body.get("sensor_config", {})
                if not isinstance(target_agent_id, str) or not target_agent_id.strip():
                    self._write_json({"error": "target_agent_id required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if target_agent_id not in observed_agents:
                    self._write_json(
                        {"error": f"target_agent_id {target_agent_id!r} not in observed stream agents", "agents": sorted(observed_agents)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                if action not in ("configure", "add", "remove", "list"):
                    self._write_json({"error": "action must be configure, add, remove, or list"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(sensor_config, dict):
                    self._write_json({"error": "sensor_config must be an object map"}, status=HTTPStatus.BAD_REQUEST)
                    return
                normalized: Dict[str, List[str]] = {}
                for stype, ids in sensor_config.items():
                    if not isinstance(stype, str):
                        continue
                    if not isinstance(ids, list):
                        continue
                    normalized[stype] = [x for x in ids if isinstance(x, str) and x.strip()]
                result = ops_client.send_sensor_config_command(
                    target_agent_id=target_agent_id.strip(),
                    action=action,
                    sensor_config=normalized,
                    timeout_sec=timeout_sec,
                )
                self._write_json(result, status=HTTPStatus.OK)
                return

        def log_message(self, fmt, *args):
            logger.info("HTTP %s - %s", self.client_address[0], fmt % args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Start live 3D visualization stream")
    parser.add_argument("--agent-id", default="dashboard", help="Compatibility flag; not used for filtering")
    parser.add_argument("--mqtt-broker", default="143.248.55.82", help="MQTT broker address")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--http-host", default="0.0.0.0", help="HTTP bind host")
    parser.add_argument("--http-port", type=int, default=8765, help="HTTP bind port")
    parser.add_argument("--caption-window-sec", type=int, default=30, help="Caption window length in seconds")
    parser.add_argument("--caption-model", default="qwenlm", help="Caption model name")
    parser.add_argument("--caption-gpus", type=int, default=1, help="GPUs for caption model init")
    parser.add_argument(
        "--caption-gpu-id",
        default="",
        help="Pin this process to specific CUDA device(s) by setting CUDA_VISIBLE_DEVICES "
             "(e.g. '0' or '0,1'). Empty = inherit from environment.",
    )
    parser.add_argument(
        "--visualization-file",
        default="index.html",
        help="Path to the 3D visualization HTML file",
    )
    parser.add_argument("--milvus-db-path", default="./data/captions.db", help="Milvus Lite DB file path")
    parser.add_argument("--embedding-model", default="jinaai/jina-clip-v1", help="Embedding model name")
    parser.add_argument("--disable-rag", action="store_true", help="Disable RAG chat endpoint")
    args = parser.parse_args()

    # Must set CUDA_VISIBLE_DEVICES before the LLM stack initializes CUDA.
    if args.caption_gpu_id:
        os.environ["CUDA_VISIBLE_DEVICES"] = args.caption_gpu_id
        logger.info("[3D] Pinned to CUDA_VISIBLE_DEVICES=%s", args.caption_gpu_id)

    html_path = (
        args.visualization_file
        if os.path.isabs(args.visualization_file)
        else os.path.join(os.path.dirname(os.path.abspath(__file__)), args.visualization_file)
    )

    logger.info("[3D] Starting with source MQTT: %s:%s", args.mqtt_broker, args.mqtt_port)
    logger.info("[3D] Serving file: %s", html_path)
    logger.info("[3D] Static dir: %s", os.path.dirname(os.path.abspath(html_path)))
    logger.info("[3D] HTTP bind: http://%s:%s", args.http_host, args.http_port)

    # Initialize Milvus store (graceful degradation)
    milvus_store_instance = None
    rag_enabled = not args.disable_rag
    if rag_enabled:
        try:
            from rag.milvus_store import create_store
            milvus_store_instance = create_store(db_path=args.milvus_db_path)
            if milvus_store_instance:
                logger.info("[3D] Milvus store initialized at %s", args.milvus_db_path)
            else:
                logger.warning("[3D] Milvus store unavailable — RAG chat will return 503")
                rag_enabled = False
        except Exception as exc:
            logger.warning("[3D] Milvus init failed: %s — RAG chat disabled", exc)
            rag_enabled = False

    store = LiveSensorStore()
    caption_engine = LiveCaptionEngine(
        store=store,
        window_sec=args.caption_window_sec,
        model_name=args.caption_model,
        gpus=args.caption_gpus,
        milvus_store=milvus_store_instance if rag_enabled else None,
        embedding_model=args.embedding_model,
    )
    bridge = SourcePayloadBridge(args.mqtt_broker, args.mqtt_port, store, caption_engine=caption_engine)

    mqtt_thread = threading.Thread(target=bridge.run_forever, daemon=True, name="mqtt-source-loop")
    caption_thread = threading.Thread(target=caption_engine.run_forever, daemon=True, name="caption-loop")
    mqtt_thread.start()
    caption_thread.start()

    ops_client: Optional[DashboardOpsClient] = None
    try:
        ops_client = DashboardOpsClient(args.mqtt_broker, args.mqtt_port)
        logger.info("[3D] Ops MQTT client connected for control/rules/threshold commands")
    except Exception as exc:
        logger.warning("[3D] Ops MQTT client disabled: %s", exc)

    notifications = NotificationStore()

    handler = make_handler(
        store, html_path, ops_client=ops_client,
        caption_engine=caption_engine,
        milvus_store=milvus_store_instance if rag_enabled else None,
        rag_enabled=rag_enabled,
        notifications=notifications,
    )
    httpd = ThreadingHTTPServer((args.http_host, args.http_port), handler)

    print("\n" + "=" * 60)
    print("LIVE 3D SENSOR STREAM STARTED")
    print("=" * 60)
    print(f"MQTT source: {args.mqtt_broker}:{args.mqtt_port}")
    print(f"3D URL:      http://{args.http_host}:{args.http_port}/")
    print(f"API URL:     http://{args.http_host}:{args.http_port}/api/latest")
    print(f"Keys URL:    http://{args.http_host}:{args.http_port}/api/keys")
    print(f"Mode GET:    http://{args.http_host}:{args.http_port}/api/control/mode")
    print(f"Mode POST:   http://{args.http_host}:{args.http_port}/api/control/mode")
    print(f"Control GET: http://{args.http_host}:{args.http_port}/api/control/capabilities")
    print(f"Control POST:http://{args.http_host}:{args.http_port}/api/control/command")
    print(f"Auto GET:    http://{args.http_host}:{args.http_port}/api/automation/catalog")
    print(f"Rules POST:  http://{args.http_host}:{args.http_port}/api/automation/rules/apply")
    print(f"Preset POST: http://{args.http_host}:{args.http_port}/api/automation/preset/apply")
    print(f"Thres POST:  http://{args.http_host}:{args.http_port}/api/automation/threshold/apply")
    print(f"Sensr POST:  http://{args.http_host}:{args.http_port}/api/sensor-config/apply")
    print(f"Notif GET:   http://{args.http_host}:{args.http_port}/api/notifications")
    print(f"Notif POST:  http://{args.http_host}:{args.http_port}/api/notifications")
    print(f"Notif CLEAR: http://{args.http_host}:{args.http_port}/api/notifications/clear")
    print(f"Chat POST:   http://{args.http_host}:{args.http_port}/api/chat")
    print(f"XML URL:     http://{args.http_host}:{args.http_port}/smart_room.xml")
    print(f"Captioning:  enabled (window={args.caption_window_sec}s, model={args.caption_model})")
    print(f"RAG Chat:    {'enabled' if rag_enabled else 'disabled'}")
    print("\nPress Ctrl+C to stop.\n")
    print("=" * 60)

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        logger.info("[3D] Received keyboard interrupt")
        print("\nShutting down 3D stream server...")
    finally:
        caption_engine.stop()
        if ops_client is not None:
            ops_client.close()
        httpd.shutdown()
        httpd.server_close()
        logger.info("[3D] Server stopped")
        time.sleep(0.1)


if __name__ == "__main__":
    main()
