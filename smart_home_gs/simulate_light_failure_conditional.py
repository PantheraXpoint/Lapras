#!/usr/bin/env python3
"""
Simulate a Hue light hardware failure followed by a *conditional* failover,
using the prebuilt rule file and a notification-driven UX timeline.

This variant is intentionally non-generative: the failover rule already lives
on disk at rules/generated/failover-conditional-clean.ttl. The script reads
that file and applies it inline via the dashboard rulesCommand path. No LLM
authoring is involved.

Conditional behavior (encoded in the rule):
    proximity_status=near AND infrared_1_proximity_status=near
        -> 6 lights, replacement "7" INCLUDED  (["1","2","5","7","8","10"])
    proximity_status=near AND infrared_1_proximity_status=far
        -> 5 lights, replacement "7" EXCLUDED  (["1","2","5","8","10"])
    proximity_status=far
        -> lights off

UX flow (visible in the dashboard's Notification tab):
    1) switch active rule to rules/demo_rules/failure-clean.ttl  (5-light state)
    2) post notification: "Light ID 3 is broken."
    3) post notification: "Notifying the Maintenance Team ..."
    4) sleep 5s (gives operators time to see the failure + maintenance message)
    5) apply rules/generated/failover-conditional-clean.ttl inline
    6) post notification: "New rule applied before light fixed."

Notifications are posted to the dashboard backend at
POST /api/notifications. If the backend is unreachable, the rule
switching still proceeds — the notification calls log a warning rather
than aborting the demo.

Usage:
    python smart_home_gs/simulate_light_failure_conditional.py
    python smart_home_gs/simulate_light_failure_conditional.py \
        --notification-base-url http://localhost:8765
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

import paho.mqtt.client as mqtt

# Match start_3d_visualization_stream.py path setup so `lapras_*` and friends resolve.
script_dir = os.path.dirname(os.path.abspath(__file__))
lapras_root = os.path.abspath(os.path.join(script_dir, ".."))
workspace_root = os.path.abspath(os.path.join(lapras_root, ".."))
for path in (lapras_root, workspace_root):
    if path not in sys.path:
        sys.path.insert(0, path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("simulate_light_failure_conditional")

# --- Demo hardcoded values ---
DEAD_LIGHT_ID = "3"
REPLACEMENT_LIGHT_ID = "7"
TRIGGER_SENSOR_ID = "infrared_1"

FAILURE_RULE_FILE = "rules/demo_rules/failure-clean.ttl"
FAILOVER_RULE_FILE = "rules/generated/failover-conditional-clean.ttl"

RULES_COMMAND_TOPIC = "context/rules/command"
RULES_RESULT_TOPIC = "context/rules/result"

NOTIFICATION_SOURCE = "simulate-light-failure-conditional"

# Pause between failure switch and failover apply. Hardcoded to 5s by spec
# so the maintenance-team notification has time to register visually.
MAINTENANCE_PAUSE_SEC = 5.0


def post_notification(base_url: str, message: str, level: str = "info",
                      timeout_sec: float = 3.0) -> bool:
    """POST a notification to the dashboard timeline.

    Returns True on success, False on any failure. Does not raise — a missing
    dashboard backend should not abort the rule-switch demo.
    """
    if not base_url:
        return False
    url = base_url.rstrip("/") + "/api/notifications"
    body = json.dumps({
        "message": message,
        "source": NOTIFICATION_SOURCE,
        "level": level,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            response.read()
        logger.info("Posted notification: %s", message)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        logger.warning("Notification POST failed (%s): %s", url, e)
        return False


def publish_rule_switch(
    mqtt_broker: str,
    mqtt_port: int,
    rule_file: str,
    timeout_sec: float = 15.0,
) -> dict:
    """Publish a rulesCommand 'switch' event and wait for the result."""
    command_id = f"sim-fail-cond-{uuid.uuid4().hex[:8]}"
    body = {
        "event": {
            "id": command_id,
            "timestamp": list(time.gmtime()),
            "type": "rulesCommand",
            "location": None,
            "contextType": None,
            "priority": "High",
        },
        "source": {"entityType": "dashboard", "entityId": NOTIFICATION_SOURCE},
        "payload": {
            "action": "switch",
            "rule_files": [rule_file],
            "preset_name": None,
        },
    }

    result_holder: dict = {"result": None}
    received = threading.Event()

    def _on_message(_client, _userdata, msg):
        try:
            event = json.loads(msg.payload.decode("utf-8"))
            payload = event.get("payload") or {}
            if payload.get("command_id") == command_id:
                result_holder["result"] = payload
                received.set()
        except Exception as e:
            logger.warning("Failed to parse rules result: %s", e)

    client = mqtt.Client(client_id=f"sim-fail-cond-{uuid.uuid4().hex[:8]}", clean_session=True)
    client.on_message = _on_message
    client.connect(mqtt_broker, mqtt_port, keepalive=30)
    client.subscribe(RULES_RESULT_TOPIC, qos=1)
    client.loop_start()
    try:
        client.publish(RULES_COMMAND_TOPIC, json.dumps(body), qos=1)
        logger.info("Published rule switch command_id=%s rule_file=%s", command_id, rule_file)
        if not received.wait(timeout=timeout_sec):
            return {"success": False, "message": f"No reply within {timeout_sec}s", "command_id": command_id}
        return result_holder["result"] or {"success": False, "message": "empty result"}
    finally:
        client.loop_stop()
        client.disconnect()


def publish_rule_apply_inline(
    mqtt_broker: str,
    mqtt_port: int,
    rule_content: str,
    virtual_path: str,
    timeout_sec: float = 15.0,
) -> dict:
    """Publish a rulesCommand 'apply_inline' event with inline TTL content."""
    command_id = f"sim-fail-cond-{uuid.uuid4().hex[:8]}"
    body = {
        "event": {
            "id": command_id,
            "timestamp": list(time.gmtime()),
            "type": "rulesCommand",
            "location": None,
            "contextType": None,
            "priority": "High",
        },
        "source": {"entityType": "dashboard", "entityId": NOTIFICATION_SOURCE},
        "payload": {
            "action": "apply_inline",
            "rule_content": rule_content,
            "virtual_path": virtual_path,
        },
    }

    result_holder: dict = {"result": None}
    received = threading.Event()

    def _on_message(_client, _userdata, msg):
        try:
            event = json.loads(msg.payload.decode("utf-8"))
            payload = event.get("payload") or {}
            if payload.get("command_id") == command_id:
                result_holder["result"] = payload
                received.set()
        except Exception as e:
            logger.warning("Failed to parse rules result: %s", e)

    client = mqtt.Client(client_id=f"sim-fail-cond-{uuid.uuid4().hex[:8]}", clean_session=True)
    client.on_message = _on_message
    client.connect(mqtt_broker, mqtt_port, keepalive=30)
    client.subscribe(RULES_RESULT_TOPIC, qos=1)
    client.loop_start()
    try:
        client.publish(RULES_COMMAND_TOPIC, json.dumps(body), qos=1)
        logger.info("Published apply_inline command_id=%s virtual_path=%s", command_id, virtual_path)
        if not received.wait(timeout=timeout_sec):
            return {"success": False, "message": f"No reply within {timeout_sec}s", "command_id": command_id}
        return result_holder["result"] or {"success": False, "message": "empty result"}
    finally:
        client.loop_stop()
        client.disconnect()


def validate_ttl_optional(content: str) -> None:
    """Best-effort TTL parse. Logs warning on failure but does NOT abort —
    the file is treated as a trusted prebuilt artifact."""
    try:
        import rdflib  # noqa: WPS433
    except ImportError:
        logger.info("rdflib not installed; skipping prebuilt TTL parse check")
        return
    try:
        g = rdflib.Graph()
        g.parse(data=content, format="turtle")
        logger.info("Prebuilt failover TTL parsed OK (%d triples)", len(g))
    except Exception as e:
        logger.warning("Prebuilt failover TTL did not parse cleanly: %s", e)


def main():
    parser = argparse.ArgumentParser(
        description=(
            f"Simulate Hue light failure (dead={DEAD_LIGHT_ID}, "
            f"replacement={REPLACEMENT_LIGHT_ID}) followed by the prebuilt "
            f"conditional failover, with notification timeline events."
        )
    )
    parser.add_argument("--mqtt-broker", default="143.248.55.82")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument(
        "--notification-base-url",
        default="http://localhost:8765",
        help="Base URL of start_3d_visualization_stream.py (where notifications POST). "
             "Set to empty string to skip notification posts.",
    )
    args = parser.parse_args()

    notif_base = (args.notification_base_url or "").strip()

    failover_rule_path = os.path.join(lapras_root, FAILOVER_RULE_FILE)
    failure_rule_path = os.path.join(lapras_root, FAILURE_RULE_FILE)
    if not os.path.isfile(failover_rule_path):
        logger.error("Prebuilt failover rule not found: %s", failover_rule_path)
        sys.exit(1)
    if not os.path.isfile(failure_rule_path):
        logger.error("Failure rule not found: %s", failure_rule_path)
        sys.exit(1)

    with open(failover_rule_path, "r", encoding="utf-8") as f:
        failover_ttl = f.read()
    if not failover_ttl.strip():
        logger.error("Prebuilt failover TTL is empty: %s", failover_rule_path)
        sys.exit(2)
    validate_ttl_optional(failover_ttl)

    # Stage A — switch to failure rule (5-light intermediate state).
    logger.info("Stage A — switching active rule to %s (5-light failure state)", FAILURE_RULE_FILE)
    result = publish_rule_switch(args.mqtt_broker, args.mqtt_port, FAILURE_RULE_FILE)
    logger.info("Failure-state rule switch result: %s", json.dumps(result))
    if not result.get("success"):
        sys.exit(3)

    # Stage B — notification 1 + 2 (warn level for the failure, info for the maintenance line).
    post_notification(notif_base, f"Light ID {DEAD_LIGHT_ID} is broken.", level="warn")
    post_notification(notif_base, "Notifying the Maintenance Team ...", level="info")

    # Stage C — fixed 5s pause so the failure/maintenance messages register visually
    # before the failover rule is applied.
    logger.info("Stage C — pausing %.1fs before applying conditional failover", MAINTENANCE_PAUSE_SEC)
    time.sleep(MAINTENANCE_PAUSE_SEC)

    # Stage D — apply prebuilt conditional failover inline.
    logger.info("Stage D — applying prebuilt conditional failover (virtual_path=%s)", FAILOVER_RULE_FILE)
    apply_result = publish_rule_apply_inline(
        args.mqtt_broker, args.mqtt_port,
        rule_content=failover_ttl,
        virtual_path=FAILOVER_RULE_FILE,
    )
    logger.info("Conditional failover apply_inline result: %s", json.dumps(apply_result))
    if not apply_result.get("success"):
        # Still post a failure notification so the operator sees the demo aborted.
        post_notification(notif_base,
                          f"Failover apply failed: {apply_result.get('message', 'unknown')}",
                          level="error")
        sys.exit(4)

    # Stage E — completion notification.
    post_notification(notif_base, "New rule applied before light fixed.", level="success")

    logger.info(
        "Conditional failover demo complete: light %s failed → conditional rule applied. "
        "Replacement %s now joins the active set only when %s reports near.",
        DEAD_LIGHT_ID,
        REPLACEMENT_LIGHT_ID,
        TRIGGER_SENSOR_ID,
    )


if __name__ == "__main__":
    main()
