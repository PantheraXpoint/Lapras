#!/usr/bin/env python3
"""
Simulate a Hue light hardware failure for the demo, with LLM-generated fix.

This script behaves exactly like clicking "switch rule" in the dashboard UI:
it only publishes rulesCommand MQTT events to ContextRuleManager. It does NOT
touch the Hue bridge directly — physical Hue toggles can't beat the automated
rule, which would just turn the "dead" light back on the next moment. Driving
everything through rule switches lets the automation itself produce the
correct visible state.

Flow:
1) Switch the active rule to rules/demo_rules/failure-clean.ttl (static
   5-light rule). The context_rule_manager reconciles the agent down to 5
   lights — light 3 goes dark, demonstrating the "hardware failure" state.
2) Pause briefly so the failure state is visible; this time also coincides
   with the LLM being loaded in step 3.
3) Ask the local qwenlm LLM to generate a new rule adding replacement light
   7. Save to rules/generated/failover-clean.ttl (validated as TTL first).
4) Switch the active rule to the LLM-generated file. The agent reconciles
   back to 6 lights, now with light 7 substituting for light 3.

The dead / replacement light IDs are hardcoded as demo constants; CLI keeps
only operational knobs (MQTT, LLM, pause duration).

Usage:
    python smart_home_gs/simulate_light_failure.py
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
import uuid

import paho.mqtt.client as mqtt

# Match start_3d_visualization_stream.py path setup so `llms` and `lapras_*` resolve.
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
logger = logging.getLogger("simulate_light_failure")

# --- Demo hardcoded values ---
DEAD_LIGHT_ID = "3"
REPLACEMENT_LIGHT_ID = "7"
FAILURE_RULE_FILE = "rules/demo_rules/failure-clean.ttl"
GENERATED_FAILOVER_FILE = "rules/generated/failover-clean.ttl"

RULES_COMMAND_TOPIC = "context/rules/command"
RULES_RESULT_TOPIC = "context/rules/result"


FAILURE_FIX_PROMPT_TEMPLATE = """You are a smart-home rule editor. Edit a TTL rule file.
Only update the light_ids list inside each hasStateUpdate JSON value. Add a
replacement light for one that has previously failed and been removed from
the list. Keep every other line of the rule file exactly as-is, including
prefixes, blank lines, comments, and indentation.

EXAMPLE
-------
Input rule (current state: light "20" already failed and removed from coverage):
@prefix lapras: <http://lapras.org/rule/> .

lapras:DemoRule a lapras:Rule ;
    lapras:hasAgent "demo" ;
    lapras:hasAction lapras:SetDemoOn .

lapras:SetDemoOn lapras:hasStateUpdate '{{"power": "on", "light_ids": ["21","22","23"]}}' .
lapras:SetDemoOff lapras:hasStateUpdate '{{"power": "off", "light_ids": ["21","22","23"]}}' .

Task: Light "20" previously failed and was removed from the light_ids list.
Add light "99" as a replacement to restore the original 4-light coverage.

Output rule:
@prefix lapras: <http://lapras.org/rule/> .

lapras:DemoRule a lapras:Rule ;
    lapras:hasAgent "demo" ;
    lapras:hasAction lapras:SetDemoOn .

lapras:SetDemoOn lapras:hasStateUpdate '{{"power": "on", "light_ids": ["21","22","23","99"]}}' .
lapras:SetDemoOff lapras:hasStateUpdate '{{"power": "off", "light_ids": ["21","22","23","99"]}}' .

NOW DO THIS
-----------
Input rule:
{rule_content}

Task: Light "{dead}" previously failed and was removed from the light_ids list.
Add light "{replacement}" as a replacement to restore the original coverage.
Apply the change to every hasStateUpdate light_ids list in the file.

Output the complete updated TTL file content only, with no surrounding prose,
no markdown fences, and no commentary."""


def build_failure_fix_prompt(rule_content: str, dead: str, replacement: str) -> str:
    return FAILURE_FIX_PROMPT_TEMPLATE.format(
        rule_content=rule_content,
        dead=dead,
        replacement=replacement,
    )


def strip_markdown_fences(text: str) -> str:
    """Some LLMs wrap output in ```ttl ... ``` even when told not to."""
    s = text.strip()
    if s.startswith("```"):
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip() + "\n"


def validate_ttl(content: str) -> tuple:
    """Return (ok, err_msg, triple_count). Parses `content` as Turtle via rdflib."""
    try:
        import rdflib
    except ImportError:
        # Keep demo flow running even when rdflib is unavailable in the runtime image.
        return True, "rdflib not installed; skipped TTL parse validation", 0
    try:
        g = rdflib.Graph()
        g.parse(data=content, format="turtle")
        return True, "", len(g)
    except Exception as e:
        return False, str(e), 0


def publish_rule_switch(
    mqtt_broker: str,
    mqtt_port: int,
    rule_file: str,
    timeout_sec: float = 15.0,
) -> dict:
    """Publish a rulesCommand 'switch' event (same shape the dashboard UI sends)
    and wait for the result on context/rules/result."""
    command_id = f"sim-fail-{uuid.uuid4().hex[:8]}"
    body = {
        "event": {
            "id": command_id,
            "timestamp": list(time.gmtime()),
            "type": "rulesCommand",
            "location": None,
            "contextType": None,
            "priority": "High",
        },
        "source": {"entityType": "dashboard", "entityId": "simulate-light-failure"},
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

    client = mqtt.Client(client_id=f"sim-fail-{uuid.uuid4().hex[:8]}", clean_session=True)
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
    """Publish a rulesCommand 'apply_inline' event with inline TTL content and wait for result."""
    command_id = f"sim-fail-{uuid.uuid4().hex[:8]}"
    body = {
        "event": {
            "id": command_id,
            "timestamp": list(time.gmtime()),
            "type": "rulesCommand",
            "location": None,
            "contextType": None,
            "priority": "High",
        },
        "source": {"entityType": "dashboard", "entityId": "simulate-light-failure"},
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

    client = mqtt.Client(client_id=f"sim-fail-{uuid.uuid4().hex[:8]}", clean_session=True)
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


def main():
    parser = argparse.ArgumentParser(
        description=(
            f"Simulate a Hue light failure (dead={DEAD_LIGHT_ID}, "
            f"replacement={REPLACEMENT_LIGHT_ID}) using only rule switches "
            "(same mechanism as the dashboard UI)."
        )
    )
    parser.add_argument("--mqtt-broker", default="143.248.55.82")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--model", default="qwenlm")
    parser.add_argument("--gpus", type=int, default=1)
    parser.add_argument(
        "--gpu-id",
        default="",
        help="Pin this process to specific CUDA device(s) by setting CUDA_VISIBLE_DEVICES "
             "(e.g. '2' or '2,3'). Empty = inherit from environment.",
    )
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--temperature", type=float, default=0.2)
    args = parser.parse_args()

    # Must set CUDA_VISIBLE_DEVICES before the LLM stack initializes CUDA.
    if args.gpu_id:
        os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu_id
        logger.info("Pinned to CUDA_VISIBLE_DEVICES=%s", args.gpu_id)

    # Import after optional CUDA pinning so backend device discovery respects it.
    try:
        from llms.init_model import init_model
    except Exception:
        init_model = None

    failure_rule_path = os.path.join(lapras_root, FAILURE_RULE_FILE)
    if not os.path.isfile(failure_rule_path):
        logger.error("Failure rule file not found: %s", failure_rule_path)
        sys.exit(1)

    # Stage A — switch to static failure rule so the automation drives the
    # agent down to 5 lights. No direct Hue calls: those get overridden by
    # the running rule the moment the next sensor update arrives.
    logger.info("Stage A — demonstrating failure state: switching rule to %s (5 lights)", FAILURE_RULE_FILE)
    result = publish_rule_switch(args.mqtt_broker, args.mqtt_port, FAILURE_RULE_FILE)
    logger.info("Failure-state rule switch result: %s", json.dumps(result))
    if not result.get("success"):
        sys.exit(4)

    # Stage C — LLM authors the fix.
    if init_model is None:
        logger.error("llms.init_model is not importable in this environment; cannot generate fix rule.")
        sys.exit(2)

    with open(failure_rule_path, "r", encoding="utf-8") as f:
        failure_rule_content = f.read()
    logger.info("Loaded failure rule as LLM input (%d chars)", len(failure_rule_content))

    logger.info("Initializing LLM (%s, gpus=%d)...", args.model, args.gpus)
    llm = init_model(args.model, args.gpus)
    prompt = build_failure_fix_prompt(failure_rule_content, DEAD_LIGHT_ID, REPLACEMENT_LIGHT_ID)
    logger.info(
        "Prompting LLM (max_new_tokens=%d, temperature=%.2f) to add replacement light %s",
        args.max_new_tokens, args.temperature, REPLACEMENT_LIGHT_ID,
    )
    raw = llm.generate_response(
        {"text": prompt},
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
    )
    new_ttl = strip_markdown_fences(raw or "")
    if not new_ttl.strip():
        logger.error("LLM returned empty content. Aborting.")
        sys.exit(3)

    ok, err, n_triples = validate_ttl(new_ttl)
    if not ok:
        logger.error("LLM-generated TTL failed to parse: %s\n--- content ---\n%s", err, new_ttl)
        sys.exit(5)
    if err:
        logger.warning("LLM-generated TTL validation skipped: %s", err)
    else:
        logger.info("LLM-generated TTL parsed OK (%d triples)", n_triples)

    # Stage D — persist and inline-deliver the LLM-authored rule.
    output_path = os.path.join(lapras_root, GENERATED_FAILOVER_FILE)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(new_ttl)
    # This local file is a sim-to-sim handoff artifact read by simulate_light_repair.py.
    # It is NOT consumed by the CM; the CM receives the content inline via MQTT below.
    logger.info("Wrote LLM-generated fix rule to %s (%d chars)", output_path, len(new_ttl))

    logger.info("Stage D — applying LLM fix inline (virtual_path=%s)", GENERATED_FAILOVER_FILE)
    result = publish_rule_apply_inline(
        args.mqtt_broker, args.mqtt_port,
        rule_content=new_ttl,
        virtual_path=GENERATED_FAILOVER_FILE,
    )
    logger.info("Fix rule apply_inline result: %s", json.dumps(result))
    if not result.get("success"):
        sys.exit(4)

    logger.info(
        "Failure demo complete: light %s failed → LLM substituted light %s → 6-light coverage restored.",
        DEAD_LIGHT_ID,
        REPLACEMENT_LIGHT_ID,
    )


if __name__ == "__main__":
    main()
