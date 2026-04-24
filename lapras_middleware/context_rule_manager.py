import logging
import time
import json
import threading
from typing import Dict, Any, Optional, List, Set
import uuid
import os

import paho.mqtt.client as mqtt
import rdflib
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF, RDFS, XSD

from .event import EventFactory, MQTTMessage, TopicManager, ContextPayload, ActionReportPayload, Event, EventMetadata, EntityInfo

logger = logging.getLogger(__name__)

class ContextRuleManager:
    """Combined context manager and rule executor that processes context states and applies rules."""

    def __init__(self, mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883, rule_files: Optional[List[str]] = None):
        """Initialize the combined context rule manager."""
        self.service_id = "ContextRuleManager"
        
        # Context management attributes
        self.context_map: Dict[str, Dict[str, Any]] = {}
        self.context_lock = threading.Lock()
        
        # Rule execution attributes
        self.rules_graph = Graph()
        
        # Define lapras namespace for SPARQL queries
        self.lapras = Namespace("http://lapras.org/rule/")
        self.rules_graph.bind("lapras", self.lapras)
        
        # Track loaded rule files to avoid duplicates
        self.loaded_rule_files: set = set()
        
        # Define rule presets for easy switching
        self.rule_presets = {
            "hue_motion_only": ["lapras_middleware/rules/hue_motion.ttl"],
            "hue_activity_only": ["lapras_middleware/rules/hue_activity.ttl"],
            "hue_ir_only": ["lapras_middleware/rules/hue_ir.ttl"],
            "hue_ir_motion": ["lapras_middleware/rules/hue_ir_motion.ttl"],
            "hue_activity_motion": ["lapras_middleware/rules/hue_activity_motion.ttl"],
            "hue_ir_activity": ["lapras_middleware/rules/hue_ir_activity.ttl"],
            "hue_all_sensors": ["lapras_middleware/rules/hue_ir_activity_motion.ttl"],
            "aircon_motion_only": ["lapras_middleware/rules/aircon_motion.ttl"],
            "aircon_activity_only": ["lapras_middleware/rules/aircon_activity.ttl"],
            "aircon_ir_only": ["lapras_middleware/rules/aircon_ir.ttl"],
            "aircon_all_sensors": ["lapras_middleware/rules/aircon_ir_acitivity_motion.ttl"],
            "aircon_door_only": ["lapras_middleware/rules/aircon_door.ttl"],
            "aircon_door_sensors": ["lapras_middleware/rules/aircon_door.ttl", "lapras_middleware/rules/aircon_ir.ttl"],
            "both_devices_motion": [
                "lapras_middleware/rules/hue_motion.ttl",
                "lapras_middleware/rules/aircon_motion.ttl"
            ],
            "both_devices_all": [
                "lapras_middleware/rules/hue_ir_activity_motion.ttl",
                "lapras_middleware/rules/aircon_ir_acitivity_motion.ttl"
            ],
            # ClubHouseAgent presets
            "back_read": ["lapras_middleware/rules/back-read.ttl"],
            "front_read": ["lapras_middleware/rules/front-read.ttl"],
            "back_nap": ["lapras_middleware/rules/back-nap.ttl"],
            "front_nap": ["lapras_middleware/rules/front-nap.ttl"],
            "all_normal": ["lapras_middleware/rules/all-normal.ttl"],
            "all_clean": ["lapras_middleware/rules/all-clean.ttl"]
        }
        
        # Track known virtual agents for dynamic topic subscription
        self.known_agents: set = set()
        
        # Track last action commands sent to prevent redundancy
        self.last_action_commands: Dict[str, Dict[str, Any]] = {}  # agent_id -> {action_name: params, timestamp: time}
        self.action_command_lock = threading.Lock()
        
        # Extended window logic for agents - configurable timing
        self.extended_window_agents: Dict[str, Dict[str, Any]] = {}  # agent_id -> {start_time, duration, last_extend_time}
        self.extended_window_lock = threading.Lock()
        # Fast state change: 15.0 for quick OFF response | Stable state: 60.0 for longer device ON time
        self.EXTENDED_WINDOW_DURATION = 60.0  # 60 seconds (change to 15.0 for fast OFF response)

        # Explicit demo control mode. In manual mode, rule-driven actuation is suppressed.
        self._control_mode = "automated"
        self._control_mode_lock = threading.Lock()
        self.CONTROL_AGENT_TTL_SEC = 20.0
        # Short cooldown after mode changes to avoid immediate opposite rule flips.
        self._mode_change_cooldown_until: Dict[str, float] = {}
        self._mode_change_cooldown_lock = threading.Lock()
        
        # Track agents needing mode detection (for startup when agents aren't connected yet)
        self.pending_mode_detection: set = set()  # Set of agent_ids that need mode detection
        self.pending_mode_detection_lock = threading.Lock()
        
        # Set up MQTT client with a unique client ID and clean session
        mqtt_client_id = f"{self.service_id}-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        self.mqtt_client = mqtt.Client(client_id=mqtt_client_id, clean_session=True)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_subscribe = self._on_subscribe
        self.mqtt_client.on_disconnect = self._on_disconnect
        
        # Enable logging for MQTT client (for debugging)
        self.mqtt_client.enable_logger(logger)
        
        try:
            self.mqtt_client.connect(mqtt_broker, mqtt_port)
        except Exception as e:
            logger.error(f"[{self.service_id}] MQTT connection error: {e}", exc_info=True)
            raise

        # Start MQTT client loop
        self.mqtt_client.loop_start()
        logger.info(f"[{self.service_id}] Initialized with client ID: {mqtt_client_id} and connected to MQTT broker.")
        
        # Load initial rule files if provided
        if rule_files:
            for rule_file in rule_files:
                self.load_rules(rule_file)

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            logger.info(f"[{self.service_id}] Successfully connected to MQTT broker.")
            # Subscribe to updateContext events from all virtual agents using wildcard
            # Topic pattern: virtual/+/to/context/updateContext
            context_topic_pattern = "virtual/+/to/context/updateContext"
            result = self.mqtt_client.subscribe(context_topic_pattern, qos=1)  # Use QoS 1 for reliable delivery
            logger.info(f"[{self.service_id}] Subscribed to topic pattern: {context_topic_pattern} (result: {result})")
            
            # Subscribe to actionReport events from all virtual agents using wildcard
            # Topic pattern: virtual/+/to/context/actionReport
            report_topic_pattern = "virtual/+/to/context/actionReport"
            result2 = self.mqtt_client.subscribe(report_topic_pattern, qos=1)  # Use QoS 1 for reliable delivery
            logger.info(f"[{self.service_id}] Subscribed to topic pattern: {report_topic_pattern} (result: {result2})")
            
            # Subscribe to dashboard manual control commands
            dashboard_control_topic = TopicManager.dashboard_control_command()
            result3 = self.mqtt_client.subscribe(dashboard_control_topic, qos=1)
            logger.info(f"[{self.service_id}] Subscribed to dashboard control topic: {dashboard_control_topic} (result: {result3})")
            
            # Subscribe to rules management commands
            rules_management_topic = TopicManager.rules_management_command()
            result4 = self.mqtt_client.subscribe(rules_management_topic, qos=1)
            logger.info(f"[{self.service_id}] Subscribed to rules management topic: {rules_management_topic} (result: {result4})")
            
            # Subscribe to dashboard sensor configuration commands
            # Topic: dashboard/sensor/config/command
            sensor_config_topic = TopicManager.dashboard_sensor_config_command()
            result5 = self.mqtt_client.subscribe(sensor_config_topic, qos=1)
            logger.info(f"[{self.service_id}] Subscribed to sensor config topic: {sensor_config_topic} (result: {result5})")
            
            # Subscribe to threshold configuration commands from dashboard
            # Topic: dashboard/threshold/command
            threshold_config_topic = TopicManager.dashboard_threshold_command()
            result6 = self.mqtt_client.subscribe(threshold_config_topic, qos=1)
            logger.info(f"[{self.service_id}] Subscribed to threshold config topic: {threshold_config_topic} (result: {result6})")
            
            # Subscribe to sensor configuration results from virtual agents
            # Topic pattern: agent/+/sensorConfig/result
            sensor_config_result_pattern = "agent/+/sensorConfig/result"
            result7 = self.mqtt_client.subscribe(sensor_config_result_pattern, qos=1)
            logger.info(f"[{self.service_id}] Subscribed to sensor config result pattern: {sensor_config_result_pattern} (result: {result7})")
            
            # Subscribe to threshold configuration results from virtual agents
            # Topic pattern: agent/+/thresholdConfig/result
            threshold_config_result_pattern = "agent/+/thresholdConfig/result"
            result8 = self.mqtt_client.subscribe(threshold_config_result_pattern, qos=1)
            logger.info(f"[{self.service_id}] Subscribed to threshold config result pattern: {threshold_config_result_pattern} (result: {result8})")

            # Subscribe to explicit control mode commands
            control_mode_topic = TopicManager.dashboard_control_mode_command()
            result9 = self.mqtt_client.subscribe(control_mode_topic, qos=1)
            logger.info(f"[{self.service_id}] Subscribed to control mode topic: {control_mode_topic} (result: {result9})")
        else:
            logger.error(f"[{self.service_id}] Failed to connect to MQTT broker. Result code: {rc}")

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        """Callback when subscription is confirmed."""
        logger.info(f"[{self.service_id}] Subscription confirmed for message ID {mid} with QoS {granted_qos}")

    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker."""
        if rc != 0:
            logger.warning(f"[{self.service_id}] Unexpected MQTT disconnection. Result code: {rc}")
        else:
            logger.info(f"[{self.service_id}] MQTT client disconnected normally")

    def get_control_mode(self) -> str:
        with self._control_mode_lock:
            return self._control_mode

    def _set_control_mode(self, mode: str) -> None:
        with self._control_mode_lock:
            self._control_mode = mode

    def _canonical_control_agent_id(self, agent_id: str) -> Optional[str]:
        """Normalize agent runtime IDs/aliases into control topology IDs."""
        if not isinstance(agent_id, str):
            return None
        aid = agent_id.strip().lower()
        if not aid:
            return None
        if aid in {"all", "back", "front"}:
            return aid
        if "aircon" in aid:
            return "aircon"
        if "hue" in aid or "light_hue" in aid or "hue_light" in aid:
            return "hue_light"
        return None

    def _rule_file_applies_to_agent(self, rule_file: str, agent_id: str) -> bool:
        """
        Heuristic: which loaded rule files should trigger evaluation for this runtime agent.

        Clubhouse demo files embed the agent id in the basename (e.g. all-normal.ttl).
        Energy-efficient Hue rules use hue_ir_light.ttl — the substring 'hue_light' never
        appears in the filename, so a naive basename check skips all automation for hue_light.
        """
        if not isinstance(rule_file, str) or not isinstance(agent_id, str):
            return False
        base = os.path.basename(rule_file).lower()
        raw = agent_id.strip().lower()
        if not base or not raw:
            return False
        if raw in base:
            return True
        canonical = self._canonical_control_agent_id(agent_id) or ""
        if canonical == "hue_light":
            return "hue" in base and "light" in base
        if canonical == "aircon":
            return "aircon" in base
        if canonical == "all":
            if "all" in base:
                return True
            # Demo failure/repair rules (and LLM-generated failover files) target
            # the "all" agent but don't carry "all" in the filename — e.g.
            # repair-clean.ttl, failure-clean.ttl, failover-clean.ttl,
            # repair-clean-restored.ttl. The SPARQL hasAgent filter in
            # evaluate_rules still enforces correctness, so widening the heuristic
            # here only risks redundant evaluation, never misrouted actions.
            return "repair" in base or "failover" in base or "failure" in base
        if canonical in {"back", "front"}:
            return canonical in base
        return False

    def _normalized_control_agents(self) -> Set[str]:
        now = time.time()
        with self.context_lock:
            active_agent_ids = [
                aid
                for aid, data in self.context_map.items()
                if isinstance(aid, str)
                and (now - float((data or {}).get("last_update", 0.0))) <= self.CONTROL_AGENT_TTL_SEC
            ]
        normalized: Set[str] = set()
        for aid in active_agent_ids:
            canonical = self._canonical_control_agent_id(aid)
            if canonical:
                normalized.add(canonical)
        return normalized

    def _validate_control_topology(self, running_agents: Optional[Set[str]] = None) -> Optional[str]:
        running = set(running_agents if running_agents is not None else self._normalized_control_agents())
        if not running:
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
        if frozenset(running) in allowed:
            return None

        if "all" in running and ("back" in running or "front" in running):
            return "Invalid running combo: 'all' cannot run with 'back' or 'front'."
        if (running & {"aircon", "hue_light"}) and (running & {"all", "back", "front"}):
            return (
                "Invalid running combo: split agents (aircon/hue_light) "
                "cannot run with clubhouse agents (all/back/front)."
            )
        return f"Invalid running combo: {sorted(running)} (allowed: aircon, hue_light, aircon+hue_light, all, back, front, back+front)."

    def _on_message(self, client, userdata, msg):
        """Callback when message is received from virtual agents or dashboard."""
        logger.info(f"[{self.service_id}] DEBUG: Message received on topic '{msg.topic}' with QoS {msg.qos}")
        logger.debug(f"[{self.service_id}] Message payload: {msg.payload.decode()[:200]}...")  # First 200 chars
        
        try:
            # Check if it's an explicit control mode command
            if msg.topic == TopicManager.dashboard_control_mode_command():
                logger.info(f"[{self.service_id}] DEBUG: Processing control mode command")
                self._handle_control_mode_command(msg.payload.decode())
                return

            # Check if it's a dashboard control command
            if msg.topic == TopicManager.dashboard_control_command():
                logger.info(f"[{self.service_id}] DEBUG: Processing dashboard control command")
                self._handle_dashboard_control_command(msg.payload.decode())
                return
            
            # Check if it's a rules management command
            if msg.topic == TopicManager.rules_management_command():
                logger.info(f"[{self.service_id}] DEBUG: Processing rules management command")
                logger.debug(f"[{self.service_id}] Full rules command payload: {msg.payload.decode()}")
                try:
                    self._handle_rules_management_command(msg.payload.decode())
                    logger.info(f"[{self.service_id}] DEBUG: Rules management command processed successfully")
                except Exception as rules_error:
                    logger.error(f"[{self.service_id}] ERROR in rules management command: {rules_error}", exc_info=True)
                return
            
            # Check if it's a dashboard sensor configuration command
            if msg.topic == TopicManager.dashboard_sensor_config_command():
                logger.info(f"[{self.service_id}] DEBUG: Processing sensor configuration command")
                try:
                    self._handle_sensor_config_command(msg.payload.decode())
                    logger.info(f"[{self.service_id}] DEBUG: Sensor configuration command processed successfully")
                except Exception as sensor_error:
                    logger.error(f"[{self.service_id}] ERROR in sensor configuration command: {sensor_error}", exc_info=True)
                return
            
            # Check if it's a dashboard threshold configuration command
            if msg.topic == TopicManager.dashboard_threshold_command():
                logger.info(f"[{self.service_id}] DEBUG: Processing threshold configuration command")
                try:
                    self._handle_threshold_config_command(msg.payload.decode())
                    logger.info(f"[{self.service_id}] DEBUG: Threshold configuration command processed successfully")
                except Exception as threshold_error:
                    logger.error(f"[{self.service_id}] ERROR in threshold configuration command: {threshold_error}", exc_info=True)
                return
            
            # Check if it's a sensor configuration result from virtual agent
            if msg.topic.startswith("agent/") and msg.topic.endswith("/sensorConfig/result"):
                logger.info(f"[{self.service_id}] DEBUG: Processing sensor configuration result")
                try:
                    self._handle_sensor_config_result(msg.payload.decode())
                    logger.info(f"[{self.service_id}] DEBUG: Sensor configuration result processed successfully")
                except Exception as result_error:
                    logger.error(f"[{self.service_id}] ERROR in sensor configuration result: {result_error}", exc_info=True)
                return
            
            # Check if it's a threshold configuration result from virtual agent
            if msg.topic.startswith("agent/") and msg.topic.endswith("/thresholdConfig/result"):
                logger.info(f"[{self.service_id}] DEBUG: Processing threshold configuration result")
                try:
                    self._handle_threshold_config_result(msg.payload.decode())
                    logger.info(f"[{self.service_id}] DEBUG: Threshold configuration result processed successfully")
                except Exception as result_error:
                    logger.error(f"[{self.service_id}] ERROR in threshold configuration result: {result_error}", exc_info=True)
                return
            
            # For other message types, try to deserialize as Event
            logger.debug(f"[{self.service_id}] Attempting to deserialize message as Event structure")
            event = MQTTMessage.deserialize(msg.payload.decode())
            logger.info(f"[{self.service_id}] DEBUG: Event type: {event.event.type}, Source: {event.source.entityId}")
            
            if event.event.type == "updateContext":
                # Handle updateContext event from VirtualAgent (ASYNCHRONOUS)
                self._handle_context_update(event)
            elif event.event.type == "actionReport":
                # Handle actionReport event from VirtualAgent (SYNCHRONOUS - Response)
                self._handle_action_report(event)
            else:
                logger.warning(f"[{self.service_id}] Unknown event type: {event.event.type}")
                
        except json.JSONDecodeError as json_error:
            logger.error(f"[{self.service_id}] Message payload on topic '{msg.topic}' was not valid JSON: {msg.payload.decode(errors='ignore')}")
            logger.error(f"[{self.service_id}] JSON decode error: {json_error}")
        except Exception as e:
            logger.error(f"[{self.service_id}] Error processing message on topic '{msg.topic}': {e}", exc_info=True)
            logger.error(f"[{self.service_id}] Message payload that caused error: {msg.payload.decode(errors='ignore')}")

    def _handle_control_mode_command(self, message_payload: str):
        """Handle explicit manual/automated control mode commands from dashboard."""
        command_id = "unknown"
        try:
            event = MQTTMessage.deserialize(message_payload)
            command_id = event.event.id
            payload = event.payload if isinstance(event.payload, dict) else {}

            if event.event.type != "dashboardControlModeCommand":
                self._publish_control_mode_result(
                    command_id=command_id,
                    success=False,
                    mode=self.get_control_mode(),
                    message=f"Expected dashboardControlModeCommand, got: {event.event.type}",
                )
                return

            action = str(payload.get("action", "set")).strip().lower()
            requested_mode = payload.get("mode")

            if action == "get":
                self._publish_control_mode_result(
                    command_id=command_id,
                    success=True,
                    mode=self.get_control_mode(),
                    message="Control mode query success.",
                )
                return

            if not isinstance(requested_mode, str):
                self._publish_control_mode_result(
                    command_id=command_id,
                    success=False,
                    mode=self.get_control_mode(),
                    message="Missing mode; expected 'manual' or 'automated'.",
                )
                return

            mode = requested_mode.strip().lower()
            if mode not in {"manual", "automated"}:
                self._publish_control_mode_result(
                    command_id=command_id,
                    success=False,
                    mode=self.get_control_mode(),
                    message=f"Invalid mode '{requested_mode}'. Expected manual|automated.",
                )
                return

            old_mode = self.get_control_mode()
            self._set_control_mode(mode)
            logger.info(f"[{self.service_id}] CONTROL MODE changed: {old_mode} -> {mode}")
            # Push the new mode into dashboard state payload immediately.
            self._publish_dashboard_state_update()
            self._publish_control_mode_result(
                command_id=command_id,
                success=True,
                mode=mode,
                message=f"Control mode set to {mode}.",
            )
            if mode == "automated":
                self._apply_rules_immediately_for_known_agents("control_mode switched to automated")

        except Exception as e:
            logger.error(f"[{self.service_id}] Error processing control mode command: {e}", exc_info=True)
            self._publish_control_mode_result(
                command_id=command_id,
                success=False,
                mode=self.get_control_mode(),
                message=f"Error: {str(e)}",
            )

    def _publish_control_mode_result(self, command_id: str, success: bool, mode: str, message: str):
        try:
            result_event = EventFactory.create_dashboard_control_mode_result_event(
                command_id=command_id,
                success=success,
                mode=mode,
                message=message,
            )
            topic = TopicManager.dashboard_control_mode_result()
            self.mqtt_client.publish(topic, MQTTMessage.serialize(result_event), qos=1)
        except Exception as e:
            logger.error(f"[{self.service_id}] Error publishing control mode result: {e}", exc_info=True)

    def _handle_dashboard_control_command(self, message_payload: str):
        """Handle manual control command from dashboard via MQTT."""
        try:
            # Parse as Event structure instead of custom JSON
            event = MQTTMessage.deserialize(message_payload)
            
            if event.event.type != "dashboardCommand":
                logger.error(f"[{self.service_id}] Expected dashboardCommand event type, got: {event.event.type}")
                return
            
            logger.info(f"[{self.service_id}] Received dashboard control command: {event.event.id}")
            
            # Extract command details from payload
            agent_id = event.payload.get('agent_id')
            action_name = event.payload.get('action_name') 
            parameters = event.payload.get('parameters')
            priority = event.payload.get('priority', 'High')
            command_id = event.event.id
            
            if not agent_id or not action_name:
                logger.error(f"[{self.service_id}] Dashboard command missing required fields: {event.payload}")
                self._publish_dashboard_command_result(command_id, False, "Missing agent_id or action_name", agent_id)
                return
            
            # Check if agent exists
            if agent_id not in self.known_agents:
                logger.warning(f"[{self.service_id}] Dashboard command for unknown agent: {agent_id}")
                self._publish_dashboard_command_result(command_id, False, f"Agent {agent_id} not found", agent_id)
                return

            topology_error = self._validate_control_topology()
            if topology_error:
                self._publish_dashboard_command_result(command_id, False, topology_error, agent_id)
                return

            current_mode = self.get_control_mode()
            if action_name in {"turn_on", "turn_off"} and current_mode != "manual":
                self._publish_dashboard_command_result(
                    command_id,
                    False,
                    f"Manual command '{action_name}' rejected: control_mode is '{current_mode}'. Switch to manual mode first.",
                    agent_id,
                )
                return
            
            # Create and send action command
            action_event = EventFactory.create_action_event(
                virtual_agent_id=agent_id,
                action_name=action_name,
                parameters={**parameters, "skip_verification": True} if parameters else {"skip_verification": True},
                priority=priority
            )
            
            # Send to virtual agent using direct MQTT publish for manual commands (bypass redundancy check)
            action_topic = TopicManager.context_to_virtual_action(agent_id)
            action_message = MQTTMessage.serialize(action_event)
            publish_result = self.mqtt_client.publish(action_topic, action_message, qos=1)
            
            # Check if publish was successful
            if publish_result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error(f"[{self.service_id}] Failed to publish action command to {agent_id}. Return code: {publish_result.rc}")
                self._publish_dashboard_command_result(command_id, False, f"Failed to send command to {agent_id}", agent_id)
                return
            
            
            # Log the dashboard command for audit trail
            logger.info(f"[{self.service_id}] DASHBOARD COMMAND sent to {agent_id}: {action_name}")
            logger.info(f"[{self.service_id}] Action topic: {action_topic}")
            logger.info(f"[{self.service_id}] Action event ID: {action_event.event.id}")
            logger.info(f"[{self.service_id}] Control mode at dispatch: {current_mode}")
            if parameters:
                logger.info(f"[{self.service_id}] Command parameters: {parameters}")
            logger.info(f"[{self.service_id}] Command ID: {action_event.event.id}, Priority: {priority}")

            # For mode switches, force immediate visible profile application when the
            # target agent is already effectively powered.
            if action_name == "change_mode" and self._is_agent_effectively_powered_on(agent_id):
                logger.info(
                    f"[{self.service_id}] Immediate mode-apply: '{agent_id}' appears powered; sending turn_on after change_mode"
                )
                import time
                time.sleep(0.2)
                self._send_action_command(
                    agent_id=agent_id,
                    action_name="turn_on",
                    parameters=None,
                    is_manual=False,
                )
            
            # Publish success result back to dashboard
            self._publish_dashboard_command_result(
                command_id, True, 
                f"Command '{action_name}' sent to {agent_id} on topic {action_topic}", 
                agent_id, action_event.event.id
            )
            
        except json.JSONDecodeError:
            logger.error(f"[{self.service_id}] Invalid JSON in dashboard command: {message_payload}")
            self._publish_dashboard_command_result("unknown", False, "Invalid JSON format", None)
        except Exception as e:
            logger.error(f"[{self.service_id}] Error processing dashboard command: {e}", exc_info=True)
            self._publish_dashboard_command_result("unknown", False, f"Error: {str(e)}", None)

    def _publish_dashboard_command_result(self, command_id: str, success: bool, message: str, agent_id: Optional[str], action_event_id: Optional[str] = None):
        """Publish command result back to dashboard via MQTT using Event structure."""
        try:
            # Create Event structure for command result
            result_event = EventFactory.create_dashboard_command_result_event(
                command_id=command_id,
                success=success,
                message=message,
                agent_id=agent_id,
                action_event_id=action_event_id
            )
            
            result_topic = TopicManager.dashboard_control_result()
            result_message = MQTTMessage.serialize(result_event)
            self.mqtt_client.publish(result_topic, result_message, qos=1)
            
            logger.info(f"[{self.service_id}] Published dashboard command result: {command_id} - {success}")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error publishing dashboard command result: {e}")

    def _handle_context_update(self, event):
        """Handle updateContext event from VirtualAgent (ASYNCHRONOUS)."""
        try:
            context_payload = MQTTMessage.get_payload_as(event, ContextPayload)
            agent_id = event.source.entityId
            
            logger.info(f"[{self.service_id}] 📨 CONTEXT UPDATE received from '{agent_id}'")
            
            # Check if this is a new agent that needs delayed mode detection
            is_new_agent = agent_id not in self.known_agents
            
            # Update context map
            with self.context_lock:
                new_state = context_payload.state.copy()
                self.context_map[agent_id] = {
                    "state": new_state,
                    "agent_type": context_payload.agent_type,
                    "sensors": context_payload.sensors,
                    "timestamp": event.event.timestamp,
                    "last_update": time.time()
                }
                logger.info(f"[{self.service_id}] 📨 CONTEXT UPDATE: Updated context for agent {agent_id}, context payload {new_state}")
                logger.debug(f"[{self.service_id}] Current context map: {json.dumps(self.context_map, indent=2)}")
            
            # Track this agent for future action commands FIRST (before delayed mode detection)
            self.known_agents.add(agent_id)
            
            # Handle delayed mode detection for new agents
            if is_new_agent:
                with self.pending_mode_detection_lock:
                    if agent_id in self.pending_mode_detection:
                        logger.info(f"[{self.service_id}] 🔄 DELAYED MODE DETECTION: Agent '{agent_id}' connected - applying targeted mode detection")
                        self.pending_mode_detection.remove(agent_id)
                        # Apply mode detection only for rules relevant to this specific agent
                        if self.loaded_rule_files:
                            relevant_rules = [
                                rule_file
                                for rule_file in self.loaded_rule_files
                                if self._rule_file_applies_to_agent(rule_file, agent_id)
                            ]
                            if relevant_rules:
                                logger.info(f"[{self.service_id}] 🔄 DELAYED MODE DETECTION: Processing {len(relevant_rules)} relevant rules for '{agent_id}': {relevant_rules}")
                                self._detect_and_apply_modes_from_rules(relevant_rules)
                            else:
                                logger.debug(f"[{self.service_id}] No relevant rules found for agent '{agent_id}'")
                        else:
                            logger.debug(f"[{self.service_id}] No loaded rules for delayed mode detection")
                    else:
                        logger.debug(f"[{self.service_id}] New agent '{agent_id}' connected but no pending mode detection needed")
            
            # Publish updated dashboard state
            self._publish_dashboard_state_update()
            
            # Only apply rules if there are rules relevant to this agent
            has_relevant_rules = any(
                self._rule_file_applies_to_agent(rule_file, agent_id) for rule_file in self.loaded_rule_files
            )
            if has_relevant_rules:
                logger.info(
                    f"[{self.service_id}] 📨 CONTEXT UPDATE: About to apply rules for '{agent_id}' "
                    f"(control_mode={self.get_control_mode()})"
                )
                self._apply_rules_for_agent(agent_id, new_state, event.event.timestamp)
            else:
                logger.debug(f"[{self.service_id}] 📨 CONTEXT UPDATE: No relevant rules for agent '{agent_id}' - skipping rule evaluation")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error handling context update: {e}", exc_info=True)

    def _publish_dashboard_state_update(self):
        """Publish current system state to dashboard via MQTT using Event structure."""
        try:
            with self.context_lock:
                # Prepare agents data
                agents_data = {}
                for agent_id, agent_data in self.context_map.items():
                    agents_data[agent_id] = {
                        "state": agent_data["state"],
                        "agent_type": agent_data["agent_type"], 
                        "sensors": agent_data["sensors"],
                        "timestamp": agent_data["timestamp"],
                        "last_update": agent_data["last_update"],
                        "is_responsive": (time.time() - agent_data["last_update"]) < 300  # 5 minute timeout
                    }
                
                # Prepare summary data
                now = time.time()
                active_running: Set[str] = set()
                for aid, data in self.context_map.items():
                    if not isinstance(aid, str):
                        continue
                    last_update = float((data or {}).get("last_update", 0.0))
                    if (now - last_update) > self.CONTROL_AGENT_TTL_SEC:
                        continue
                    canonical = self._canonical_control_agent_id(aid)
                    if canonical:
                        active_running.add(canonical)

                # IMPORTANT: pass explicit running_agents here to avoid re-entering
                # self.context_lock inside _normalized_control_agents().
                topology_error = self._validate_control_topology(active_running)
                summary_data = {
                    "total_agents": len(self.context_map),
                    "known_agents": list(self.known_agents),
                    "last_update": time.time(),
                    "control_mode": self.get_control_mode(),
                    "topology_valid": topology_error is None,
                    "topology_error": topology_error or "",
                }
                
                # Create Event structure for dashboard state
                dashboard_state_event = EventFactory.create_dashboard_state_update_event(
                    agents=agents_data,
                    summary=summary_data
                )
            
            # Publish using Event structure
            dashboard_topic = TopicManager.dashboard_context_state()
            dashboard_message = MQTTMessage.serialize(dashboard_state_event)
            self.mqtt_client.publish(dashboard_topic, dashboard_message, qos=1)
            
            logger.debug(f"[{self.service_id}] Published dashboard state update with {len(dashboard_state_event.payload['agents'])} agents")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error publishing dashboard state: {e}", exc_info=True)

    def _handle_action_report(self, event):
        """Handle actionReport event from VirtualAgent (SYNCHRONOUS - Response)."""
        try:
            report_payload = MQTTMessage.get_payload_as(event, ActionReportPayload)
            agent_id = event.source.entityId
            
            logger.info(f"[{self.service_id}] Received action report from {agent_id}: success={report_payload.success}, message='{report_payload.message}'")
            
            if report_payload.new_state:
                # Update our context map with the new state
                with self.context_lock:
                    if agent_id in self.context_map:
                        self.context_map[agent_id]["state"].update(report_payload.new_state)
                        self.context_map[agent_id]["last_update"] = time.time()
                        logger.info(f"[{self.service_id}] Updated context for {agent_id} with new state from action report")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error handling action report: {e}", exc_info=True)

    def _apply_rules_for_agent(self, agent_id: str, current_state: Dict[str, Any], timestamp: Any):
        """Apply rules for a specific agent when control mode/topology permit it."""
        try:
            now_ts = time.time()
            with self._mode_change_cooldown_lock:
                cooldown_until = self._mode_change_cooldown_until.get(agent_id, 0.0)
            if cooldown_until > now_ts:
                logger.info(
                    f"[{self.service_id}] Skipping rule-driven action for '{agent_id}' during "
                    f"post-mode-change cooldown ({cooldown_until - now_ts:.2f}s remaining)"
                )
                return

            control_mode = self.get_control_mode()
            if control_mode != "automated":
                logger.info(
                    f"[{self.service_id}] Skipping rule-driven action for '{agent_id}' "
                    f"because control_mode='{control_mode}'"
                )
                return

            topology_error = self._validate_control_topology()
            if topology_error:
                logger.warning(
                    f"[{self.service_id}] Skipping rule-driven action for '{agent_id}' due to invalid topology: "
                    f"{topology_error}"
                )
                return

            logger.info(f"[{self.service_id}] 🔍 DEBUG: Processing rules for agent '{agent_id}' (state updated)")
            logger.info(f"[{self.service_id}] 🔍 DEBUG: Current state for '{agent_id}': {json.dumps(current_state, indent=2)}")

            # Evaluate rules to get the raw decision
            raw_rule_decision = self.evaluate_rules(agent_id, current_state)

            if raw_rule_decision is None:
                logger.debug(f"[{self.service_id}] No rules triggered for agent '{agent_id}'")
                return

            # Extract power decision from rules
            raw_power_decision = raw_rule_decision.get("power")
            current_power_state = current_state.get("power", "off")

            # Optional per-light targeting carried by the rule's hasStateUpdate JSON.
            # When present, forwarded as action parameters so the virtual agent iterates
            # exactly those Hue lights instead of using its default group.
            rule_light_ids = raw_rule_decision.get("light_ids")
            action_params: Optional[Dict[str, Any]] = None
            if isinstance(rule_light_ids, list) and rule_light_ids:
                action_params = {"light_ids": [str(x) for x in rule_light_ids]}

            logger.info(f"[{self.service_id}] 🔍 DEBUG: Raw rule decision for '{agent_id}': power={raw_power_decision}, light_ids={rule_light_ids}")
            logger.info(f"[{self.service_id}] 🔍 DEBUG: Current power state for '{agent_id}': {current_power_state}")

            # Apply extended window logic to prevent rapid on/off switching
            final_power_decision = self._apply_extended_window_logic(agent_id, current_power_state, raw_power_decision)

            logger.info(f"[{self.service_id}] 🔍 DEBUG: Final decision for '{agent_id}' after extended window logic: {current_power_state} → {final_power_decision}")

            if final_power_decision != current_power_state:
                logger.info(f"[{self.service_id}] 🚨 ACTION NEEDED for '{agent_id}': {current_power_state} → {final_power_decision}")
                if final_power_decision == "on":
                    self._send_action_command(agent_id, "turn_on", parameters=action_params)
                elif final_power_decision == "off":
                    self._send_action_command(agent_id, "turn_off", parameters=action_params)
            elif final_power_decision == "on" and action_params is not None:
                # Power stays 'on' but the rule's light_ids (or other per-light params)
                # may have changed from the last dispatched action — e.g. switching
                # repair-clean → failover-clean while lights are already on. Without
                # this branch the dispatch path sees no power transition and skips,
                # leaving the old light subset active. We re-send only when params
                # actually differ from the last command, so steady-state doesn't spam.
                with self.action_command_lock:
                    last_cmd = self.last_action_commands.get(agent_id) or {}
                last_action_name = last_cmd.get("action_name")
                last_params = last_cmd.get("parameters")
                if last_action_name != "turn_on" or last_params != action_params:
                    logger.info(
                        f"[{self.service_id}] 🔁 PARAMS CHANGED for '{agent_id}': "
                        f"last={last_params} → new={action_params}; re-dispatching turn_on to reconcile"
                    )
                    self._send_action_command(agent_id, "turn_on", parameters=action_params)
                else:
                    logger.debug(f"[{self.service_id}] ✅ NO ACTION: same power and same params for agent '{agent_id}'")
            else:
                logger.debug(f"[{self.service_id}] ✅ NO ACTION: No state change needed for agent '{agent_id}' (already {current_power_state})")

        except Exception as e:
            logger.error(f"[{self.service_id}] Error applying rules for agent '{agent_id}': {e}", exc_info=True)

    def _apply_rules_immediately_for_known_agents(self, reason: str = "rule update") -> None:
        """
        Force a one-shot reconciliation pass using current context state.
        This avoids waiting for the next sensor update after switching rules/modes.
        """
        try:
            with self.context_lock:
                snapshot = {
                    aid: dict((data or {}).get("state") or {})
                    for aid, data in self.context_map.items()
                    if isinstance(aid, str)
                }

            if not snapshot:
                logger.info(f"[{self.service_id}] Immediate reconciliation skipped ({reason}): no known agent state yet")
                return

            logger.info(
                f"[{self.service_id}] Immediate reconciliation start ({reason}) for agents: {sorted(snapshot.keys())}"
            )
            now = time.time()
            for agent_id, state in snapshot.items():
                self._apply_rules_for_agent(agent_id, state, now)
            logger.info(f"[{self.service_id}] Immediate reconciliation complete ({reason})")
        except Exception as e:
            logger.error(f"[{self.service_id}] Error in immediate reconciliation ({reason}): {e}", exc_info=True)

    def _apply_rules_immediately_for_agents(self, agent_ids: Set[str], reason: str = "rule update") -> None:
        """
        Force immediate reconciliation for a selected subset of agents.
        Used by demo rule switches to avoid perturbing unrelated running agents.
        """
        try:
            if not agent_ids:
                logger.info(f"[{self.service_id}] Immediate reconciliation skipped ({reason}): no target agents")
                return

            with self.context_lock:
                snapshot = {
                    aid: dict((self.context_map.get(aid) or {}).get("state") or {})
                    for aid in sorted(agent_ids)
                    if aid in self.context_map
                }
            if not snapshot:
                logger.info(f"[{self.service_id}] Immediate reconciliation skipped ({reason}): no target state snapshot")
                return

            logger.info(
                f"[{self.service_id}] Immediate reconciliation start ({reason}) for target agents: {sorted(snapshot.keys())}"
            )
            now = time.time()
            for agent_id, state in snapshot.items():
                self._apply_rules_for_agent(agent_id, state, now)
            logger.info(f"[{self.service_id}] Immediate reconciliation complete ({reason})")
        except Exception as e:
            logger.error(f"[{self.service_id}] Error in targeted immediate reconciliation ({reason}): {e}", exc_info=True)

    def _apply_extended_window_logic(self, agent_id: str, current_power: str, raw_rule_decision: str) -> str:
        """
        Apply extended window logic to rule decisions.
        
        Logic:
        - OFF → ON: Start extended window, return "on"
        - During normal extended window (60s):
          - Rule says "on": Extend window, return "on" 
          - Rule says "off": Continue countdown, return "on"
        - During fast response window (5s):
          - Rule says "on": Convert to normal 60s window, return "on"
          - Rule says "off": Allow immediate OFF, return "off"
        - After window expires: Allow "off", return "off"
        """
        current_time = time.time()
        
        logger.info(f"[{self.service_id}] 🕰️  EXTENDED WINDOW: Evaluating for '{agent_id}' - current: {current_power}, rule: {raw_rule_decision}")
        
        with self.extended_window_lock:
            window_info = self.extended_window_agents.get(agent_id)
            
            # Case 1: OFF → ON (start new window)
            if current_power == "off" and raw_rule_decision == "on":
                self.extended_window_agents[agent_id] = {
                    "start_time": current_time,
                    "duration": self.EXTENDED_WINDOW_DURATION,
                    "last_extend_time": current_time,
                    "is_fast_response": False
                }
                logger.info(f"[{self.service_id}] 🕰️  EXTENDED WINDOW: Started {self.EXTENDED_WINDOW_DURATION}s window for '{agent_id}'")
                return "on"
            
            # Case 2: No active window, follow raw decision
            if not window_info:
                logger.info(f"[{self.service_id}] 🕰️  EXTENDED WINDOW: No active window for '{agent_id}', following raw decision: {raw_rule_decision}")
                return raw_rule_decision
            
            # Case 3: Active window - check if expired
            window_start = window_info["start_time"]
            last_extend = window_info["last_extend_time"]
            current_duration = window_info.get("duration", self.EXTENDED_WINDOW_DURATION)
            is_fast_response = window_info.get("is_fast_response", False)
            time_since_last_extend = current_time - last_extend
            
            logger.info(f"[{self.service_id}] 🕰️  EXTENDED WINDOW: Active for '{agent_id}' - time since last extend: {time_since_last_extend:.1f}s / {current_duration}s (fast_response: {is_fast_response})")
            
            # Window expired?
            if time_since_last_extend >= current_duration:
                # Window expired - remove it and allow raw decision
                del self.extended_window_agents[agent_id]
                logger.info(f"[{self.service_id}] 🕰️  EXTENDED WINDOW: EXPIRED for '{agent_id}' after {time_since_last_extend:.1f}s - allowing raw decision: {raw_rule_decision}")
                return raw_rule_decision
            
            # Case 4: Fast response window (5s) - allow immediate rule evaluation
            if is_fast_response:
                if raw_rule_decision == "on":
                    # Convert to normal 60s extended window
                    self.extended_window_agents[agent_id] = {
                        "start_time": current_time,
                        "duration": self.EXTENDED_WINDOW_DURATION,
                        "last_extend_time": current_time,
                        "is_fast_response": False
                    }
                    logger.info(f"[{self.service_id}] 🕰️  FAST RESPONSE: Rule says 'on' for '{agent_id}' - converting to {self.EXTENDED_WINDOW_DURATION}s extended window")
                    return "on"
                else:
                    # Allow immediate OFF during fast response
                    del self.extended_window_agents[agent_id]
                    logger.info(f"[{self.service_id}] 🕰️  FAST RESPONSE: Rule says 'off' for '{agent_id}' - allowing immediate OFF")
                    return "off"
            
            # Case 5: Normal extended window, rule says "on" - extend window
            if raw_rule_decision == "on":
                self.extended_window_agents[agent_id]["last_extend_time"] = current_time
                remaining_time = current_duration - time_since_last_extend
                logger.info(f"[{self.service_id}] 🕰️  EXTENDED WINDOW: Extended for '{agent_id}' (reset to {current_duration}s)")
                return "on"
            
            # Case 6: Normal extended window, rule says "off" - continue countdown, stay on
            else:
                remaining_time = current_duration - time_since_last_extend
                logger.info(f"[{self.service_id}] 🕰️  EXTENDED WINDOW: Rule says 'off' for '{agent_id}', but in window ({remaining_time:.1f}s remaining) - staying on")
                return "on"

    def _determine_actions_from_state_change(self, old_state: Dict[str, Any], new_state: Dict[str, Any]) -> list:
        """Determine what action commands to send based on state changes."""
        actions = []
        
        # Check for power state changes
        if old_state.get("power") != new_state.get("power"):
            if new_state.get("power") == "on":
                actions.append({"actionName": "turn_on"})
            elif new_state.get("power") == "off":
                actions.append({"actionName": "turn_off"})
        
        return actions

    def _is_action_redundant(self, agent_id: str, action_name: str, parameters: Optional[Dict[str, Any]] = None, is_manual: bool = False) -> bool:
        """Check if this action command is redundant (same as the last one sent)."""
        with self.action_command_lock:
            if agent_id not in self.last_action_commands:
                return False  # No previous command, so not redundant
            
            last_command = self.last_action_commands[agent_id]
            
            # Check if it's the same action with the same parameters
            if (last_command.get("action_name") == action_name and 
                last_command.get("parameters") == parameters):
                
                # Reduced cooldown time for faster response
                # Manual commands get shorter cooldown than automatic rules  
                cooldown_time = 0.5 if is_manual else 2.0  # 0.5s for manual, 2s for rules (increased for stability)
                time_since_last = time.time() - last_command.get("timestamp", 0)
                if time_since_last < cooldown_time:
                    return True  # Redundant
            
            return False  # Not redundant
    
    def _record_action_command(self, agent_id: str, action_name: str, parameters: Optional[Dict[str, Any]] = None):
        """Record that we sent this action command to prevent future redundancy."""
        with self.action_command_lock:
            self.last_action_commands[agent_id] = {
                "action_name": action_name,
                "parameters": parameters,
                "timestamp": time.time()
            }

    def _send_action_command(self, agent_id: str, action_name: str, parameters: Optional[Dict[str, Any]] = None, is_manual: bool = False):
        """Send an action command to a VirtualAgent (SYNCHRONOUS - Request)."""
        try:
            # Check if this action command is redundant
            if self._is_action_redundant(agent_id, action_name, parameters, is_manual):
                cooldown_time = 0.5 if is_manual else 1.0
                logger.info(f"[{self.service_id}] Skipping redundant action command '{action_name}' to agent '{agent_id}' ({cooldown_time}s cooldown active)")
                return
            
            # Create applyAction event
            action_event = EventFactory.create_action_event(
                virtual_agent_id=agent_id,
                action_name=action_name,
                parameters=parameters
            )
            
            # Publish to the agent's action topic
            action_topic = TopicManager.context_to_virtual_action(agent_id)
            action_message = MQTTMessage.serialize(action_event)
            self.mqtt_client.publish(action_topic, action_message, qos=1)  # Use QoS 1 for reliable delivery
            
            # Record this command to prevent future redundancy
            self._record_action_command(agent_id, action_name, parameters)
            
            logger.info(f"[{self.service_id}] Sent action command '{action_name}' to agent '{agent_id}' on topic '{action_topic}'")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error sending action command to {agent_id}: {e}", exc_info=True)

    def _is_agent_effectively_powered_on(self, agent_id: str) -> bool:
        """
        Return True when an agent appears to be active/on enough that a mode/profile
        change should be re-applied immediately via a follow-up turn_on command.
        """
        try:
            state = self.get_agent_state(agent_id) or {}
            power = str(state.get("power", "")).lower()
            light_power = str(state.get("light_power", "")).lower()
            aircon_power = str(state.get("aircon_power", "")).lower()
            # Treat partial and per-device on as effectively powered for immediate re-apply.
            return (
                power in {"on", "partial", "true", "1"}
                or light_power in {"on", "true", "1"}
                or aircon_power in {"on", "true", "1"}
            )
        except Exception:
            return False

    def set_extended_window_duration(self, duration: float) -> None:
        """Set the extended window duration for fast vs stable behavior."""
        with self.extended_window_lock:
            old_duration = self.EXTENDED_WINDOW_DURATION
            self.EXTENDED_WINDOW_DURATION = duration
            logger.info(f"[{self.service_id}] Extended window duration changed: {old_duration}s → {duration}s")
            
            # Clear existing windows when changing duration to avoid confusion
            if self.extended_window_agents:
                cleared_agents = list(self.extended_window_agents.keys())
                self.extended_window_agents.clear()
                logger.info(f"[{self.service_id}] Cleared extended windows for {len(cleared_agents)} agents due to duration change")

    def _set_fast_response_window(self, agent_id: str, reason: str = "configuration change") -> None:
        """Set a 5-second extended window for faster response to configuration changes."""
        with self.extended_window_lock:
            current_time = time.time()
            if agent_id in self.extended_window_agents:
                # Override existing window - set to 5 seconds
                self.extended_window_agents[agent_id]["duration"] = 5.0
                self.extended_window_agents[agent_id]["last_extend_time"] = current_time
                self.extended_window_agents[agent_id]["is_fast_response"] = True
                logger.info(f"[{self.service_id}] 🕰️ {reason.upper()}: Set window to 5 seconds for '{agent_id}'")
            else:
                # Create new 5-second window
                self.extended_window_agents[agent_id] = {
                    "start_time": current_time,
                    "duration": 5.0,
                    "last_extend_time": current_time,
                    "is_fast_response": True
                }
                logger.info(f"[{self.service_id}] 🕰️ {reason.upper()}: Created 5-second window for '{agent_id}'")

    def _set_fast_response_window_for_all_agents(self, reason: str = "rule change") -> None:
        """Set 5-second extended windows for all known agents after rule changes."""
        with self.extended_window_lock:
            current_time = time.time()
            affected_agents = []
            
            for agent_id in self.known_agents:
                if agent_id in self.extended_window_agents:
                    # Override existing window - set to 5 seconds
                    self.extended_window_agents[agent_id]["duration"] = 5.0
                    self.extended_window_agents[agent_id]["last_extend_time"] = current_time
                    self.extended_window_agents[agent_id]["is_fast_response"] = True
                else:
                    # Create new 5-second window
                    self.extended_window_agents[agent_id] = {
                        "start_time": current_time,
                        "duration": 5.0,
                        "last_extend_time": current_time,
                        "is_fast_response": True
                    }
                affected_agents.append(agent_id)
            
            if affected_agents:
                logger.info(f"[{self.service_id}] 🕰️ {reason.upper()}: Set 5-second windows for {len(affected_agents)} agents: {affected_agents}")

    def get_extended_window_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get the current extended window status for debugging purposes."""
        with self.extended_window_lock:
            window_info = self.extended_window_agents.get(agent_id)
            if not window_info:
                return None
            
            current_time = time.time()
            current_duration = window_info.get("duration", self.EXTENDED_WINDOW_DURATION)
            time_since_last_extend = current_time - window_info["last_extend_time"]
            remaining_time = current_duration - time_since_last_extend
            total_window_time = current_time - window_info["start_time"]
            
            return {
                "agent_id": agent_id,
                "window_start": window_info["start_time"],
                "last_extend": window_info["last_extend_time"],
                "current_time": current_time,
                "time_since_last_extend": time_since_last_extend,
                "remaining_time": remaining_time,
                "total_window_time": total_window_time,
                "duration": current_duration,
                "is_expired": time_since_last_extend >= current_duration
            }

    def evaluate_rules(self, agent_id: str, current_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Evaluate rules for a specific agent based on its current state.
        Handles rules with multiple conditions (all conditions for a rule must be ANDed).
        Returns the new state dictionary if any rule successfully applied a state_update, otherwise None.
        """
        try:
            # Quick check: if there are no loaded rules relevant to this agent, return early
            has_relevant_rules = any(
                self._rule_file_applies_to_agent(rule_file, agent_id) for rule_file in self.loaded_rule_files
            )
            if not has_relevant_rules:
                logger.debug(f"[{self.service_id}] No relevant rules loaded for agent '{agent_id}' - skipping evaluation")
                return None
            
            agent_id_rdf_literal = rdflib.Literal(agent_id)
            agent_specific_rules_query = """
            SELECT DISTINCT ?rule_uri ?action_uri
            WHERE {
                ?rule_uri lapras:hasAgent ?agent_id_param .
                ?rule_uri lapras:hasAction ?action_uri .
            }
            """
            new_state_after_all_rules = current_state.copy()
            any_rule_led_to_actionable_change = False
            logger.info(f"[{self.service_id}] Evaluating rules for agent '{agent_id}'")

            for rule_uri, action_uri in self.rules_graph.query(
                agent_specific_rules_query,
                initBindings={'agent_id_param': agent_id_rdf_literal}
            ):
                rule_name_short = str(rule_uri).split('/')[-1] or str(rule_uri)
                logger.debug(f"[{self.service_id}] Checking rule '{rule_name_short}' for agent '{agent_id}'")

                rule_conditions_query = """
                SELECT ?condition_node
                WHERE { ?current_rule_param lapras:hasCondition ?condition_node . }
                """
                conditions_for_this_rule = list(self.rules_graph.query(
                    rule_conditions_query, initBindings={'current_rule_param': rule_uri}
                ))

                if not conditions_for_this_rule:
                    logger.debug(f"[{self.service_id}] Rule '{rule_name_short}' has no conditions - skipping")
                    continue

                all_conditions_met_for_this_rule = True
                for i, condition_row in enumerate(conditions_for_this_rule):
                    condition_node = condition_row[0]
                    if not self._evaluate_condition(condition_node, current_state):
                        all_conditions_met_for_this_rule = False
                        logger.debug(f"[{self.service_id}] Rule '{rule_name_short}' condition #{i+1} failed")
                        break

                if all_conditions_met_for_this_rule:
                    action_data = self._parse_action(action_uri)
                    logger.info(f"[{self.service_id}] Rule '{rule_name_short}' Action '{action_data}' ALL conditions met - applying action")

                    if action_data and "state_update" in action_data:
                        state_update_payload = action_data["state_update"]
                        if isinstance(state_update_payload, dict):
                            # Check if the state update actually changes anything
                            changed_by_this_action = False
                            for key, value in state_update_payload.items():
                                logger.info(f"[{self.service_id}] Rule '{rule_name_short}' state update changes {key} from {new_state_after_all_rules.get(key)} to {value}")
                                if new_state_after_all_rules.get(key) != value:
                                    changed_by_this_action = True
                                    break
                            
                            if changed_by_this_action:
                                logger.info(f"[{self.service_id}] Applying state update for rule '{rule_name_short}': {json.dumps(state_update_payload)}")
                                new_state_after_all_rules.update(state_update_payload)
                                any_rule_led_to_actionable_change = True
                            else:
                                logger.debug(f"[{self.service_id}] Rule '{rule_name_short}' state update doesn't change current state")
                        else:
                            logger.warning(f"[{self.service_id}] Rule '{rule_name_short}' state_update is not a dictionary")
                    else:
                        logger.warning(f"[{self.service_id}] Rule '{rule_name_short}' missing state_update data")

            if any_rule_led_to_actionable_change:
                logger.info(f"[{self.service_id}] Final state for agent '{agent_id}' after rules: {json.dumps(new_state_after_all_rules)}")
                return new_state_after_all_rules
            else:
                return None
        except Exception as e:
            logger.error(f"[{self.service_id}] Critical error in evaluate_rules for agent '{agent_id}': {e}", exc_info=True)
            return None

    def _evaluate_condition(self, condition_node: rdflib.term.Node, current_agent_state: Dict[str, Any]) -> bool:
        """Evaluate a single condition definition against the agent's current state."""
        try:
            condition_query = """
            SELECT ?sensor_rdf ?operator_rdf ?value_rdf_node
            WHERE {
                ?condition_param lapras:hasSensor ?sensor_rdf .
                ?condition_param lapras:hasOperator ?operator_rdf .
                ?condition_param lapras:hasValue ?value_rdf_node .
            }"""
            
            query_results = list(self.rules_graph.query(
                condition_query, initBindings={'condition_param': condition_node}
            ))

            if not query_results:
                logger.debug(f"[{self.service_id}] No sensor/operator/value found for condition {condition_node}")
                return False
            
            sensor_rdf, operator_rdf, value_rdf_node = query_results[0]
            
            sensor_id_from_rule = str(sensor_rdf)
            operator_str = str(operator_rdf).split('/')[-1]
            
            value_from_agent_state = current_agent_state.get(sensor_id_from_rule)

            if value_from_agent_state is None:
                logger.debug(f"[{self.service_id}] Sensor '{sensor_id_from_rule}' not found in agent state")
                return False

            rule_threshold_value = None
            if isinstance(value_rdf_node, rdflib.Literal):
                rule_threshold_value = value_rdf_node.value
                # Debug logging for RDF value parsing
                if sensor_id_from_rule == "activity_detected":
                    logger.info(f"[{self.service_id}] RDF Parsing for {sensor_id_from_rule}: raw_literal='{value_rdf_node}', parsed_value='{rule_threshold_value}' ({type(rule_threshold_value)})")
            else:
                logger.debug(f"[{self.service_id}] Rule value for '{sensor_id_from_rule}' is not a literal")
                return False
            
            result = False
            try:
                if operator_str in ["greaterThan", "lessThan", "greaterThanOrEqual", "lessThanOrEqual"]:
                    val_state_num = float(value_from_agent_state)
                    rule_val_num = float(rule_threshold_value)
                    if operator_str == "greaterThan": result = val_state_num > rule_val_num
                    elif operator_str == "lessThan": result = val_state_num < rule_val_num
                    elif operator_str == "greaterThanOrEqual": result = val_state_num >= rule_val_num
                    elif operator_str == "lessThanOrEqual": result = val_state_num <= rule_val_num
                
                elif operator_str == "equals":
                    # Handle boolean values explicitly
                    if isinstance(rule_threshold_value, bool):
                        # Convert agent state value to boolean for comparison
                        if isinstance(value_from_agent_state, bool):
                            result = value_from_agent_state == rule_threshold_value
                        elif isinstance(value_from_agent_state, str):
                            # Convert string representation to boolean
                            agent_bool = value_from_agent_state.lower() in ['true', '1', 'yes', 'on']
                            result = agent_bool == rule_threshold_value
                        else:
                            # Try to convert to boolean-like value
                            agent_bool = bool(value_from_agent_state)
                            result = agent_bool == rule_threshold_value
                        logger.debug(f"[{self.service_id}] Boolean comparison: agent_value='{value_from_agent_state}' ({type(value_from_agent_state)}), rule_value='{rule_threshold_value}' ({type(rule_threshold_value)}), result={result}")
                    else:
                        # Handle numeric values
                        try:
                            val_state_num = float(value_from_agent_state)
                            rule_val_num = float(rule_threshold_value)
                            result = (val_state_num == rule_val_num)
                        except (ValueError, TypeError):
                            # Fall back to string comparison
                            result = str(value_from_agent_state) == str(rule_threshold_value)
                            logger.debug(f"[{self.service_id}] String comparison: agent_value='{value_from_agent_state}', rule_value='{rule_threshold_value}', result={result}")
                
                elif operator_str == "notEquals":
                    try:
                        val_state_num = float(value_from_agent_state)
                        rule_val_num = float(rule_threshold_value)
                        result = (val_state_num != rule_val_num)
                    except (ValueError, TypeError):
                        result = str(value_from_agent_state) != str(rule_threshold_value)
                else:
                    logger.debug(f"[{self.service_id}] Unsupported operator '{operator_str}'")
                    return False
            except (ValueError, TypeError) as e:
                logger.debug(f"[{self.service_id}] Type error in condition evaluation: {e}")
                return False
            
            # Log condition details for debugging (especially for boolean activity_detected)
            if sensor_id_from_rule == "activity_detected" or result:
                logger.info(f"[{self.service_id}] Condition {sensor_id_from_rule}: '{value_from_agent_state}' ({type(value_from_agent_state)}) {operator_str} '{rule_threshold_value}' ({type(rule_threshold_value)}) = {result}")
            elif not result:
                logger.debug(f"[{self.service_id}] Condition FALSE: '{value_from_agent_state}' {operator_str} '{rule_threshold_value}'")
            
            return result 
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error evaluating condition: {e}", exc_info=True)
            return False

    def _parse_action(self, action_uri: rdflib.term.Node) -> Optional[Dict[str, Any]]:
        """Parse an action node to find a state_update JSON string."""
        try:
            action_query = """
            SELECT ?state_update_json_str
            WHERE {
                ?action_param lapras:hasStateUpdate ?state_update_json_str .
            }
            """
            query_results = list(self.rules_graph.query(
                action_query, initBindings={'action_param': action_uri}
            ))

            if not query_results:
                logger.debug(f"[{self.service_id}] No 'lapras:hasStateUpdate' found for action URI: {action_uri}")
                return None
            
            state_update_json_str_literal = query_results[0][0]
            if not isinstance(state_update_json_str_literal, rdflib.Literal):
                logger.error(f"[{self.service_id}] Expected RDF Literal for state_update, got {type(state_update_json_str_literal)} for action {action_uri}")
                return None

            state_update_json_str = str(state_update_json_str_literal.value)

            try:
                parsed_state_update = json.loads(state_update_json_str)
                if not isinstance(parsed_state_update, dict):
                    logger.error(f"[{self.service_id}] 'state_update' for action {action_uri} is not a JSON object (dict): {state_update_json_str}")
                    return None
                return {"state_update": parsed_state_update}
            except json.JSONDecodeError as je:
                logger.error(f"[{self.service_id}] Invalid JSON in state_update for action {action_uri}: '{state_update_json_str}'. Error: {je}")
                return None
        
        except Exception as e:
            logger.error(f"[{self.service_id}] Error parsing action {action_uri}: {e}", exc_info=True)
            return None

    def _detect_and_apply_modes_from_rules(self, rule_file_paths: List[str]) -> None:
        """Detect agent modes from rule filenames and send change_mode commands."""
        import os

        mode_commands = {}  # agent_id -> mode

        for rule_file in rule_file_paths:
            try:
                # Extract filename without path and extension
                filename = os.path.basename(rule_file)
                name_without_ext = os.path.splitext(filename)[0]

                # Special-case failover/repair/failure rules for the 'all' agent.
                # These don't follow the strict position-mode naming convention
                # (e.g. repair-clean.ttl, failure-clean.ttl, failover-clean.ttl,
                # repair-clean-restored.ttl) but visually they should use the same
                # 'clean' light profile as all-clean.ttl so the color matches
                # during the demo.
                lower_name = name_without_ext.lower()
                if "clean" in lower_name and (
                    lower_name.startswith("repair-")
                    or lower_name.startswith("failover-")
                    or lower_name.startswith("failure-")
                ):
                    mode_commands["all"] = "clean"
                    logger.info(
                        f"[{self.service_id}] Detected mode change needed: all → clean "
                        f"(from {rule_file}, failover/repair/failure special-case)"
                    )
                    continue

                # Parse filename format: position-mode.ttl (e.g., back-nap.ttl, front-read.ttl)
                if '-' in name_without_ext:
                    parts = name_without_ext.split('-')
                    if len(parts) == 2:
                        position, mode = parts

                        # Map position to agent_id
                        if position in ['back', 'front', 'all']:
                            agent_id = position

                            # Validate mode for position
                            valid_modes = {
                                'back': ['nap', 'read'],
                                'front': ['nap', 'read'],
                                'all': ['normal', 'clean']
                            }

                            if mode in valid_modes.get(position, []):
                                mode_commands[agent_id] = mode
                                logger.info(f"[{self.service_id}] Detected mode change needed: {agent_id} → {mode} (from {rule_file})")
                            else:
                                logger.warning(f"[{self.service_id}] Invalid mode '{mode}' for position '{position}' in {rule_file}")
                        else:
                            logger.debug(f"[{self.service_id}] Unknown position '{position}' in {rule_file}")
                    else:
                        logger.debug(f"[{self.service_id}] Rule file doesn't match position-mode format: {rule_file}")
                else:
                    logger.debug(f"[{self.service_id}] Rule file doesn't contain position-mode separator: {rule_file}")
                    
            except Exception as e:
                logger.error(f"[{self.service_id}] Error parsing rule filename {rule_file}: {e}")
        
        # Send mode change commands
        for agent_id, mode in mode_commands.items():
            try:
                if self.get_control_mode() != "automated":
                    logger.info(
                        f"[{self.service_id}] Skipping auto mode change for '{agent_id}' "
                        f"because control_mode='{self.get_control_mode()}'"
                    )
                    continue

                topology_error = self._validate_control_topology()
                if topology_error:
                    logger.warning(
                        f"[{self.service_id}] Skipping auto mode change for '{agent_id}' due to invalid topology: "
                        f"{topology_error}"
                    )
                    continue

                # Check if agent exists (has sent context updates)
                if agent_id not in self.known_agents:
                    logger.info(f"[{self.service_id}] Agent '{agent_id}' not yet connected - adding to pending mode detection list (mode: {mode})")
                    with self.pending_mode_detection_lock:
                        self.pending_mode_detection.add(agent_id)
                    continue
                
                logger.info(f"[{self.service_id}] 🔄 AUTO MODE CHANGE: Sending change_mode({mode}) to {agent_id}")
                self._send_action_command(
                    agent_id=agent_id,
                    action_name="change_mode",
                    parameters={"mode": mode},
                    is_manual=False
                )
                logger.info(f"[{self.service_id}] ✅ AUTO MODE CHANGE: Command sent to {agent_id}")

                # Avoid immediate opposite power flips right after mode switches.
                with self._mode_change_cooldown_lock:
                    self._mode_change_cooldown_until[agent_id] = time.time() + 2.5
                
                # If agent is effectively powered, optionally send turn_on to apply new mode settings now.
                # Clubhouse agents already re-apply profiles internally in _change_mode,
                # so an extra manager-level turn_on causes duplicate IR/Hue commands.
                if self._is_agent_effectively_powered_on(agent_id) and agent_id not in {"all", "back", "front"}:
                    logger.info(f"[{self.service_id}] 🔧 AUTO MODE APPLY: Agent {agent_id} is powered on - sending turn_on to apply new {mode} mode settings")
                    # Small delay to ensure mode change is processed first
                    import time
                    time.sleep(0.2)
                    self._send_action_command(
                        agent_id=agent_id,
                        action_name="turn_on",
                        parameters=None,
                        is_manual=False
                    )
                    logger.info(f"[{self.service_id}] ✅ AUTO MODE APPLY: turn_on command sent to {agent_id}")
                
            except Exception as e:
                logger.error(f"[{self.service_id}] Error sending mode change command to {agent_id}: {e}")

    def load_rules(self, rdf_file_path: str, detect_mode: bool = True) -> None:
        """Load rules from an RDF file."""
        try:
            # Check if file already loaded to avoid duplicates
            if rdf_file_path in self.loaded_rule_files:
                logger.info(f"[{self.service_id}] Rules file {rdf_file_path} already loaded, skipping")
                return
            
            self.rules_graph.parse(rdf_file_path, format="turtle")
            self.loaded_rule_files.add(rdf_file_path)
            logger.info(f"[{self.service_id}] Successfully loaded rules from {rdf_file_path}")
            
            # Auto mode detection for single file load (optional for bulk flows)
            if detect_mode:
                self._detect_and_apply_modes_from_rules([rdf_file_path])
            
            # Log loaded rule details for verification
            rules_query = """
            SELECT ?rule_uri ?agent_literal (GROUP_CONCAT(?condition_uri; separator=", ") AS ?conditions) ?action_uri
            WHERE {
                ?rule_uri rdf:type lapras:Rule .
                ?rule_uri lapras:hasAgent ?agent_literal .
                OPTIONAL { ?rule_uri lapras:hasCondition ?condition_uri . }
                OPTIONAL { ?rule_uri lapras:hasAction ?action_uri . }
            }
            GROUP BY ?rule_uri ?agent_literal ?action_uri
            """
            logger.debug(f"[{self.service_id}] Verifying loaded rules from {rdf_file_path}:")
            for r_uri, ag_lit, conds_str, act_uri in self.rules_graph.query(rules_query):
                logger.debug(f"[{self.service_id}]   Rule: {r_uri}, Agent: {ag_lit}, Conditions: [{conds_str if conds_str else 'None'}], Action: {act_uri if act_uri else 'None'}")

        except Exception as e:
            logger.error(f"[{self.service_id}] Failed to load rules from {rdf_file_path}: {e}", exc_info=True)

    def load_rules_from_list(self, rule_file_paths: List[str]) -> Dict[str, bool]:
        """Load multiple rule files and return success status for each."""
        results = {}
        successfully_loaded = []
        
        for rule_file in rule_file_paths:
            try:
                # Load rule file (without auto mode detection to avoid duplicates)
                if rule_file not in self.loaded_rule_files:
                    self.rules_graph.parse(rule_file, format="turtle")
                    self.loaded_rule_files.add(rule_file)
                    successfully_loaded.append(rule_file)
                    logger.info(f"[{self.service_id}] Successfully loaded rules from {rule_file}")
                else:
                    logger.info(f"[{self.service_id}] Rules file {rule_file} already loaded, skipping")
                results[rule_file] = True
            except Exception as e:
                logger.error(f"[{self.service_id}] Failed to load rule file {rule_file}: {e}")
                results[rule_file] = False
        
        # NEW: Auto mode detection for all successfully loaded files
        if successfully_loaded:
            self._detect_and_apply_modes_from_rules(successfully_loaded)
        
        return results

    def load_rules_from_content(self, rule_content: str, virtual_path: str) -> None:
        """Load rules from inline TTL content; virtual_path is used only for filename heuristics."""
        self.rules_graph.parse(data=rule_content, format="turtle")
        self.loaded_rule_files.add(virtual_path)
        logger.info(f"[{self.service_id}] Loaded inline rules (virtual_path={virtual_path})")

    def get_loaded_rule_files(self) -> List[str]:
        """Get list of currently loaded rule files."""
        return list(self.loaded_rule_files)

    def clear_rules(self) -> None:
        """Clear all loaded rules."""
        self.rules_graph = Graph()
        self.rules_graph.bind("lapras", self.lapras)
        self.loaded_rule_files.clear()
        logger.info(f"[{self.service_id}] All rules cleared")

    def _handle_rules_management_command(self, message_payload: str):
        """Handle rules management commands via MQTT."""
        try:
            event = MQTTMessage.deserialize(message_payload)
            
            if event.event.type != "rulesCommand":
                logger.error(f"[{self.service_id}] Expected rulesCommand event type, got: {event.event.type}")
                return
            
            logger.info(f"[{self.service_id}] Received rules management command: {event.event.id}")
            
            # Extract command details from payload
            command_action = event.payload.get('action')  # "load", "unload", "reload", "list", "switch", "clear", "list_presets", "switch_preset"
            rule_files = event.payload.get('rule_files', [])  # list of rule file paths
            preset_name = event.payload.get('preset_name')  # preset name for easier management
            command_id = event.event.id
            source_entity_id = event.source.entityId if event.source else ""
            demo_notice = ""
            demo_ui_source = source_entity_id == "sensor-3d-ui"

            # Demo policy: replace-only semantics. map load -> switch for UI callers.
            if demo_ui_source and command_action == "load":
                command_action = "switch"
                demo_notice = "Demo policy: mapped load to switch (replace-only). "
            
            # If preset_name is provided, use preset files
            if preset_name and preset_name in self.rule_presets:
                rule_files = self.rule_presets[preset_name]
                logger.info(f"[{self.service_id}] Using preset '{preset_name}': {rule_files}")

            # Demo UI path applies one rule at a time.
            if demo_ui_source and isinstance(rule_files, list) and len(rule_files) > 1:
                logger.info(
                    f"[{self.service_id}] Demo source provided {len(rule_files)} rules; "
                    f"keeping first file only for replace-only flow."
                )
                rule_files = [rule_files[0]]
                demo_notice += "Only one rule file is allowed per demo apply; using first selection. "
            
            success = False
            message = ""
            
            if command_action == "load":
                try:
                    loaded_files = []
                    for rule_file in rule_files:
                        self.load_rules(rule_file)
                        loaded_files.append(rule_file)
                    
                    # NEW: Set 5-second windows for all agents after loading new rules
                    self._set_fast_response_window_for_all_agents("rule load")
                    
                    # NEW: Auto mode detection for loaded rules
                    self._detect_and_apply_modes_from_rules(loaded_files)
                    self._apply_rules_immediately_for_known_agents("rule load")
                    
                    success = True
                    message = f"Successfully loaded {len(loaded_files)} rule files: {loaded_files}"
                except Exception as e:
                    message = f"Error loading rules: {str(e)}"
                    
            elif command_action == "reload":
                try:
                    # Clear existing rules
                    self.rules_graph = Graph()
                    self.rules_graph.bind("lapras", self.lapras)
                    self.loaded_rule_files.clear()
                    
                    # Reload specified files
                    loaded_files = []
                    for rule_file in rule_files:
                        self.load_rules(rule_file)
                        loaded_files.append(rule_file)
                    
                    # NEW: Set 5-second windows for all agents after reloading rules
                    self._set_fast_response_window_for_all_agents("rule reload")
                    
                    # NEW: Auto mode detection for reloaded rules
                    self._detect_and_apply_modes_from_rules(loaded_files)
                    self._apply_rules_immediately_for_known_agents("rule reload")
                    
                    success = True
                    message = f"Successfully reloaded {len(loaded_files)} rule files: {loaded_files}"
                except Exception as e:
                    message = f"Error reloading rules: {str(e)}"
                    
            elif command_action == "switch":
                try:
                    def _clubhouse_position_from_rule_path(path: str) -> Optional[str]:
                        base = os.path.basename(str(path or "")).lower()
                        if base.startswith("back-"):
                            return "back"
                        if base.startswith("front-"):
                            return "front"
                        if base.startswith("all-"):
                            return "all"
                        return None

                    def _agents_from_rule_paths(paths: List[str]) -> Set[str]:
                        out: Set[str] = set()
                        for p in paths:
                            base = os.path.basename(str(p or "")).lower()
                            if base.startswith("back-"):
                                out.add("back")
                            elif base.startswith("front-"):
                                out.add("front")
                            elif base.startswith("all-"):
                                out.add("all")
                            elif "aircon" in base:
                                out.add("aircon")
                            elif "hue" in base and "light" in base:
                                out.add("hue_light")
                        return out

                    # Clear existing rules and reset extended window state
                    old_files = list(self.loaded_rule_files)
                    explicitly_requested_rule_files = list(rule_files)

                    # Demo UX compatibility: when switching one positional clubhouse rule
                    # (back/front), preserve the opposite positional rule so both agents
                    # can keep running concurrently after sequential applies.
                    effective_rule_files = list(rule_files)
                    if demo_ui_source and effective_rule_files:
                        requested_positions = {
                            _clubhouse_position_from_rule_path(p)
                            for p in effective_rule_files
                        } - {None}
                        if requested_positions <= {"back", "front"} and len(requested_positions) == 1:
                            requested_position = next(iter(requested_positions))
                            opposite_position = "front" if requested_position == "back" else "back"
                            opposite_existing = [
                                p for p in old_files
                                if _clubhouse_position_from_rule_path(p) == opposite_position
                            ]
                            if opposite_existing:
                                keep_file = opposite_existing[0]
                                if keep_file not in effective_rule_files:
                                    effective_rule_files.append(keep_file)
                                    logger.info(
                                        f"[{self.service_id}] Demo clubhouse pair mode: preserving '{opposite_position}' "
                                        f"rule alongside requested '{requested_position}' rule: {keep_file}"
                                    )

                    self.clear_rules()
                    
                    # Clear extended window state when switching rules (will be recreated below)
                    if demo_ui_source:
                        logger.info(
                            f"[{self.service_id}] Demo source rule switch: preserving existing extended windows "
                            f"to avoid cross-agent AC oscillation"
                        )
                    else:
                        with self.extended_window_lock:
                            self.extended_window_agents.clear()
                    
                    # Load new rule files
                    loaded_files = []
                    for rule_file in effective_rule_files:
                        self.load_rules(rule_file, detect_mode=False)
                        loaded_files.append(rule_file)
                    
                    # For dashboard demo applies, avoid globally forcing fast-response windows.
                    # Fast-response allows immediate OFF and can cause visible AC flapping
                    # when back/front rules are updated close together.
                    if demo_ui_source:
                        logger.info(
                            f"[{self.service_id}] Demo source rule switch: keeping normal extended windows "
                            f"(skip fast-response-for-all to reduce AC oscillation)"
                        )
                    else:
                        # NEW: Set 5-second windows for all agents after switching rules
                        self._set_fast_response_window_for_all_agents("rule switch")
                    
                    # NEW: Auto mode detection for switched rules
                    if demo_ui_source:
                        # Only apply mode/reconciliation for explicitly requested files.
                        # Preserved opposite clubhouse files remain active but should not be
                        # force-reconciled during a single-agent demo update.
                        mode_files = explicitly_requested_rule_files or loaded_files
                        self._detect_and_apply_modes_from_rules(mode_files)
                        target_agents = _agents_from_rule_paths(mode_files)
                        self._apply_rules_immediately_for_agents(target_agents, "rule switch (targeted)")
                    else:
                        self._detect_and_apply_modes_from_rules(loaded_files)
                        self._apply_rules_immediately_for_known_agents("rule switch")
                    
                    success = True
                    message = f"Successfully switched from {old_files} to {loaded_files}"
                    logger.info(f"[{self.service_id}] RULE SWITCH: {old_files} → {loaded_files}")
                except Exception as e:
                    message = f"Error switching rules: {str(e)}"
                    
            elif command_action == "clear":
                try:
                    old_files = list(self.loaded_rule_files)
                    self.clear_rules()
                    
                    # Clear extended window state when clearing rules
                    with self.extended_window_lock:
                        self.extended_window_agents.clear()
                    
                    success = True
                    message = f"Successfully cleared {len(old_files)} rule files: {old_files}"
                    logger.info(f"[{self.service_id}] RULES CLEARED: {old_files}")
                except Exception as e:
                    message = f"Error clearing rules: {str(e)}"
                    
            elif command_action == "list":
                success = True
                message = f"Currently loaded rule files: {list(self.loaded_rule_files)}"
                
            elif command_action == "list_presets":
                success = True
                preset_info = []
                for preset, files in self.rule_presets.items():
                    preset_info.append(f"{preset}: {files}")
                message = f"Available presets:\n" + "\n".join(preset_info)
                
            elif command_action == "switch_preset":
                if not preset_name:
                    message = "preset_name is required for switch_preset action"
                elif preset_name not in self.rule_presets:
                    message = f"Unknown preset '{preset_name}'. Available presets: {list(self.rule_presets.keys())}"
                else:
                    try:
                        # Clear existing rules and reset extended window state
                        old_files = list(self.loaded_rule_files)
                        self.clear_rules()
                        
                        # Clear extended window state when switching rules
                        with self.extended_window_lock:
                            self.extended_window_agents.clear()
                        
                        # Load preset rule files
                        preset_files = self.rule_presets[preset_name]
                        loaded_files = []
                        for rule_file in preset_files:
                            self.load_rules(rule_file)
                            loaded_files.append(rule_file)
                        
                        if demo_ui_source:
                            logger.info(
                                f"[{self.service_id}] Demo source preset switch: keeping normal extended windows "
                                f"(skip fast-response-for-all to reduce AC oscillation)"
                            )
                        else:
                            # NEW: Set 5-second windows for all agents after switching presets
                            self._set_fast_response_window_for_all_agents("preset switch")
                        
                        # NEW: Auto mode detection for preset rules
                        self._detect_and_apply_modes_from_rules(loaded_files)
                        self._apply_rules_immediately_for_known_agents("preset switch")
                        
                        success = True
                        message = f"Successfully switched to preset '{preset_name}': {loaded_files}"
                        logger.info(f"[{self.service_id}] PRESET SWITCH: {old_files} → {preset_name} ({loaded_files})")
                    except Exception as e:
                        message = f"Error switching to preset '{preset_name}': {str(e)}"
            elif command_action == "apply_inline":
                rule_content = event.payload.get("rule_content", "")
                virtual_path = event.payload.get("virtual_path", "")
                if not rule_content or not virtual_path:
                    message = "apply_inline requires non-empty rule_content and virtual_path"
                elif event.payload.get("preset_name") or event.payload.get("rule_files"):
                    message = "apply_inline does not accept preset_name or rule_files fields"
                else:
                    try:
                        old_files = list(self.loaded_rule_files)
                        self.clear_rules()

                        # Treat simulator demo sources the same as sensor-3d-ui: preserve
                        # extended windows so we don't force fast-response across all agents.
                        demo_sim_source = source_entity_id in (
                            "simulate-light-failure", "simulate-light-repair"
                        )
                        if not (demo_ui_source or demo_sim_source):
                            with self.extended_window_lock:
                                self.extended_window_agents.clear()
                        else:
                            logger.info(
                                f"[{self.service_id}] Demo source apply_inline: preserving existing extended windows"
                            )

                        self.load_rules_from_content(rule_content, virtual_path)
                        self._detect_and_apply_modes_from_rules([virtual_path])
                        self._apply_rules_immediately_for_known_agents("apply_inline")

                        success = True
                        message = f"Successfully applied inline rule (virtual_path={virtual_path})"
                        logger.info(
                            f"[{self.service_id}] INLINE APPLY: {old_files} → [{virtual_path}]"
                        )
                    except Exception as e:
                        message = f"Error applying inline rule: {str(e)}"

            else:
                message = f"Unknown rules command action: {command_action}"

            if demo_notice:
                message = f"{demo_notice}{message}"
            
            # Publish result back
            self._publish_rules_command_result(command_id, success, message, command_action)
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error processing rules management command: {e}", exc_info=True)
            self._publish_rules_command_result("unknown", False, f"Error: {str(e)}", "unknown")

    def _publish_rules_command_result(self, command_id: str, success: bool, message: str, action: str):
        """Publish rules command result back via MQTT using Event structure."""
        try:
            result_event = EventFactory.create_rules_command_result_event(
                command_id=command_id,
                success=success,
                message=message,
                action=action,
                loaded_files=list(self.loaded_rule_files)
            )
            
            result_topic = TopicManager.rules_management_result()
            result_message = MQTTMessage.serialize(result_event)
            self.mqtt_client.publish(result_topic, result_message, qos=1)
            
            logger.info(f"[{self.service_id}] Published rules command result: {command_id} - {success}")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error publishing rules command result: {e}")

    # Context management methods
    def get_agent_state(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get the current state of an agent."""
        with self.context_lock:
            agent_data = self.context_map.get(agent_id)
            return agent_data["state"] if agent_data else None
    
    def get_all_states(self) -> Dict[str, Dict[str, Any]]:
        """Get the current states of all agents."""
        with self.context_lock:
            return {
                agent_id: data["state"]
                for agent_id, data in self.context_map.items()
            }
    
    def get_agent_last_update(self, agent_id: str) -> Optional[float]:
        """Get the last update time of an agent."""
        with self.context_lock:
            agent_data = self.context_map.get(agent_id)
            return agent_data["last_update"] if agent_data else None

    def stop(self) -> None:
        """Stop the context rule manager."""
        logger.info(f"[{self.service_id}] Stopping context rule manager...")
        if hasattr(self, 'mqtt_client') and self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            logger.info(f"[{self.service_id}] MQTT client disconnected.")
        logger.info(f"[{self.service_id}] Context rule manager stopped.")

    def send_manual_command(self, agent_id: str, action_name: str, 
                           parameters: Optional[Dict[str, Any]] = None,
                           priority: str = "High") -> Dict[str, Any]:
        """
        Send a manual control command from dashboard/API to a virtual agent.
        
        Args:
            agent_id: Target virtual agent ID
            action_name: Action to perform (e.g., "turn_on", "set_temperature")
            parameters: Optional action parameters
            priority: Command priority (Low|Normal|High|Critical)
            
        Returns:
            Dict with command_id, success status, and message
        """
        try:
            # Check if agent exists in our known agents
            if agent_id not in self.known_agents:
                logger.warning(f"[{self.service_id}] Manual command attempted for unknown agent: {agent_id}")
                return {
                    "success": False,
                    "command_id": None,
                    "message": f"Agent {agent_id} not found or not connected",
                    "agent_id": agent_id
                }

            topology_error = self._validate_control_topology()
            if topology_error:
                return {
                    "success": False,
                    "command_id": None,
                    "message": topology_error,
                    "agent_id": agent_id,
                }

            current_mode = self.get_control_mode()
            if action_name in {"turn_on", "turn_off"} and current_mode != "manual":
                return {
                    "success": False,
                    "command_id": None,
                    "message": (
                        f"Manual command '{action_name}' rejected: control_mode is "
                        f"'{current_mode}'. Switch to manual mode first."
                    ),
                    "agent_id": agent_id,
                }
            
            # Create and send action command with specified priority
            action_event = EventFactory.create_action_event(
                virtual_agent_id=agent_id,
                action_name=action_name,
                parameters={**parameters, "skip_verification": True} if parameters else {"skip_verification": True},
                priority=priority
            )
            
            # Send via MQTT to the specific agent
            action_topic = TopicManager.context_to_virtual_action(agent_id)
            action_message = MQTTMessage.serialize(action_event)
            publish_result = self.mqtt_client.publish(action_topic, action_message, qos=1)
            
            # Log the manual command for audit trail
            logger.info(f"[{self.service_id}] MANUAL COMMAND sent to {agent_id}: {action_name}")
            if parameters:
                logger.info(f"[{self.service_id}] Command parameters: {parameters}")
            logger.info(f"[{self.service_id}] Command ID: {action_event.event.id}, Priority: {priority}")
            
            return {
                "success": True,
                "command_id": action_event.event.id,
                "message": f"Manual command '{action_name}' sent to {agent_id}",
                "action_name": action_name,
                "agent_id": agent_id,
                "priority": priority,
                "publish_result": str(publish_result)
            }
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error sending manual command to {agent_id}: {e}", exc_info=True)
            return {
                "success": False,
                "command_id": None,
                "message": f"Error sending command: {str(e)}",
                "agent_id": agent_id
            }

    def get_known_agents(self) -> list:
        """
        Get list of known/connected agents for dashboard display.
        
        Returns:
            List of agent IDs that have sent context updates
        """
        return list(self.known_agents)

    def get_agent_info(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """
        Get complete information about a specific agent including metadata.
        
        Args:
            agent_id: The agent ID to get information for
            
        Returns:
            Dict with agent state, type, sensors, timestamps, etc.
            None if agent not found
        """
        with self.context_lock:
            agent_data = self.context_map.get(agent_id)
            if agent_data:
                return {
                    "agent_id": agent_id,
                    "state": agent_data["state"],
                    "agent_type": agent_data["agent_type"],
                    "sensors": agent_data["sensors"],
                    "timestamp": agent_data["timestamp"],
                    "last_update": agent_data["last_update"],
                    "is_responsive": (time.time() - agent_data["last_update"]) < 300  # 5 minute timeout
                }
            return None

    def _handle_sensor_config_command(self, message_payload: str):
        """Handle sensor configuration command from dashboard and forward to virtual agent."""
        try:
            # Parse as Event structure
            event = MQTTMessage.deserialize(message_payload)
            
            if event.event.type != "sensorConfig":
                logger.error(f"[{self.service_id}] Expected sensorConfig event type, got: {event.event.type}")
                return
            
            logger.info(f"[{self.service_id}] Received sensor configuration command: {event.event.id}")
            
            # Extract command details from payload
            target_agent_id = event.payload.get('target_agent_id')
            action = event.payload.get('action')
            sensor_config = event.payload.get('sensor_config', {})
            command_id = event.event.id
            
            if not target_agent_id or not action:
                logger.error(f"[{self.service_id}] Sensor config command missing required fields: {event.payload}")
                self._publish_sensor_config_command_result(command_id, False, "Missing target_agent_id or action", target_agent_id)
                return
            
            # Check if agent exists
            if target_agent_id not in self.known_agents:
                logger.warning(f"[{self.service_id}] Sensor config command for unknown agent: {target_agent_id}")
                self._publish_sensor_config_command_result(command_id, False, f"Agent {target_agent_id} not found", target_agent_id)
                return
            
            # NEW: Set extended window to 5 seconds for faster sensor config response
            self._set_fast_response_window(target_agent_id, "sensor config")
            
            # Forward the sensor config command to the target virtual agent
            target_topic = TopicManager.sensor_config_command(target_agent_id)
            forward_message = MQTTMessage.serialize(event)
            publish_result = self.mqtt_client.publish(target_topic, forward_message, qos=1)
            
            # Log the forwarded command
            logger.info(f"[{self.service_id}] SENSOR CONFIG COMMAND forwarded to {target_agent_id}: {action}")
            if sensor_config:
                logger.info(f"[{self.service_id}] Sensor config: {sensor_config}")
            logger.info(f"[{self.service_id}] Command ID: {command_id}")
            
            # Note: We don't send immediate result here - we wait for the virtual agent's response
            
        except json.JSONDecodeError:
            logger.error(f"[{self.service_id}] Invalid JSON in sensor config command: {message_payload}")
            self._publish_sensor_config_command_result("unknown", False, "Invalid JSON format", None)
        except Exception as e:
            logger.error(f"[{self.service_id}] Error processing sensor config command: {e}", exc_info=True)
            self._publish_sensor_config_command_result("unknown", False, f"Error: {str(e)}", None)

    def _handle_sensor_config_result(self, message_payload: str):
        """Handle sensor configuration result from virtual agent and forward to dashboard."""
        try:
            # Parse as Event structure
            event = MQTTMessage.deserialize(message_payload)
            
            if event.event.type != "sensorConfigResult":
                logger.warning(f"[{self.service_id}] Unexpected event type in sensor config result: {event.event.type}")
                return
            
            payload = event.payload
            command_id = payload.get("command_id")
            success = payload.get("success")
            message = payload.get("message")
            agent_id = payload.get("agent_id", "")
            action = payload.get("action")
            current_sensors = payload.get("current_sensors", [])
            
            logger.info(f"[{self.service_id}] Received sensor config result from agent '{agent_id}': {success} - {message}")
            
            # Forward the result back to the dashboard
            self._publish_sensor_config_command_result(command_id, success, message, agent_id, action, current_sensors)
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error handling sensor config result: {e}", exc_info=True)

    def _publish_sensor_config_command_result(self, command_id: str, success: bool, message: str, 
                                            agent_id: Optional[str], action: Optional[str] = None, 
                                            current_sensors: Optional[List[str]] = None):
        """Publish sensor configuration command result to dashboard."""
        try:
            result_event = EventFactory.create_sensor_config_command_result_event(
                command_id=command_id,
                success=success,
                message=message,
                agent_id=agent_id,
                action=action,
                current_sensors=current_sensors
            )
            
            result_topic = TopicManager.dashboard_sensor_config_result()
            message_str = MQTTMessage.serialize(result_event)
            self.mqtt_client.publish(result_topic, message_str, qos=1)
            
            logger.info(f"[{self.service_id}] Published sensor config result to dashboard: {result_topic}")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error publishing sensor config result: {e}")

    def _handle_threshold_config_command(self, message_payload: str):
        """Handle threshold configuration command from dashboard."""
        try:
            # Parse the dashboard threshold command
            command_data = json.loads(message_payload)
            logger.debug(f"[{self.service_id}] Threshold config command data: {command_data}")
            
            # Extract command information
            event_info = command_data.get("event", {})
            payload = command_data.get("payload", {})
            command_id = event_info.get("id")
            
            agent_id = payload.get("agent_id")
            threshold_type = payload.get("threshold_type")
            config = payload.get("config", {})
            
            if not all([command_id, agent_id, threshold_type]):
                logger.error(f"[{self.service_id}] Missing required fields in threshold config command")
                self._publish_threshold_config_command_result(
                    command_id or "unknown",
                    False,
                    "Missing required fields: command_id, agent_id, or threshold_type",
                    agent_id,
                    threshold_type,
                    {}
                )
                return
            
            # NEW: Set extended window to 5 seconds for faster threshold response
            self._set_fast_response_window(agent_id, "threshold config")
            
            # Create threshold configuration event for the target agent
            threshold_event = EventFactory.create_threshold_config_event(
                agent_id=agent_id,
                threshold_type=threshold_type,
                config=config,
                source_entity_id="ContextRuleManager"
            )
            
            # Set the command ID from dashboard command
            threshold_event.event.id = command_id
            
            # Send to the specific agent
            agent_threshold_topic = TopicManager.threshold_config_command(agent_id)
            threshold_message = MQTTMessage.serialize(threshold_event)
            
            self.mqtt_client.publish(agent_threshold_topic, threshold_message, qos=1)
            
            logger.info(f"[{self.service_id}] Forwarded threshold config command to agent '{agent_id}' on topic: {agent_threshold_topic}")
            logger.debug(f"[{self.service_id}] Threshold config details: type={threshold_type}, config={config}")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error handling threshold config command: {e}", exc_info=True)
            try:
                # Try to extract command_id for error response
                command_data = json.loads(message_payload)
                command_id = command_data.get("event", {}).get("id", "unknown")
                agent_id = command_data.get("payload", {}).get("agent_id", "unknown")
                threshold_type = command_data.get("payload", {}).get("threshold_type", "unknown")
                
                self._publish_threshold_config_command_result(
                    command_id,
                    False,
                    f"Error processing threshold config command: {str(e)}",
                    agent_id,
                    threshold_type,
                    {}
                )
            except:
                logger.error(f"[{self.service_id}] Could not send error response for threshold config command")

    def _handle_threshold_config_result(self, message_payload: str):
        """Handle threshold configuration result from virtual agent."""
        try:
            # Parse the agent's threshold config result
            event = MQTTMessage.deserialize(message_payload)
            
            if event.event.type != "thresholdConfigResult":
                logger.warning(f"[{self.service_id}] Unexpected event type in threshold config result: {event.event.type}")
                return
            
            payload = event.payload
            command_id = payload.get("command_id")
            success = payload.get("success", False)
            message = payload.get("message", "")
            agent_id = payload.get("agent_id", "")
            threshold_type = payload.get("threshold_type", "")
            current_config = payload.get("current_config", {})
            
            logger.info(f"[{self.service_id}] Received threshold config result from agent '{agent_id}': {success} - {message}")
            
            # Forward the result back to the dashboard
            self._publish_threshold_config_command_result(
                command_id,
                success,
                message,
                agent_id,
                threshold_type,
                current_config
            )
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error handling threshold config result: {e}", exc_info=True)

    def _publish_threshold_config_command_result(self, command_id: str, success: bool, message: str, 
                                               agent_id: str, threshold_type: str, current_config: Dict[str, Any]):
        """Publish threshold configuration command result to dashboard."""
        try:
            result_event = EventFactory.create_dashboard_threshold_command_result_event(
                command_id=command_id,
                success=success,
                message=message,
                agent_id=agent_id,
                threshold_type=threshold_type,
                current_config=current_config
            )
            
            result_topic = TopicManager.dashboard_threshold_result()
            message_str = MQTTMessage.serialize(result_event)
            self.mqtt_client.publish(result_topic, message_str, qos=1)
            
            logger.info(f"[{self.service_id}] Published threshold config result to dashboard: {result_topic}")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error publishing threshold config result: {e}")

    def test_extended_window_timing(self, agent_id: str = "test_agent") -> None:
        """Manual test method to verify extended window timing logic."""
        import time
        
        logger.info(f"[{self.service_id}] TESTING: Starting extended window timing test for '{agent_id}'")
        logger.info(f"[{self.service_id}] TESTING: EXTENDED_WINDOW_DURATION = {self.EXTENDED_WINDOW_DURATION}s")
        
        # Simulate OFF → ON transition
        result1 = self._apply_extended_window_logic(agent_id, "off", "on")
        logger.info(f"[{self.service_id}] TESTING: OFF → ON result: {result1}")
        
        # Check window status immediately
        status1 = self.get_extended_window_status(agent_id)
        logger.info(f"[{self.service_id}] TESTING: Immediate window status: {status1}")
        
        # Wait 5 seconds and test "off" decision
        time.sleep(5)
        result2 = self._apply_extended_window_logic(agent_id, "on", "off")
        status2 = self.get_extended_window_status(agent_id)
        logger.info(f"[{self.service_id}] TESTING: After 5s, rule says OFF, result: {result2}")
        logger.info(f"[{self.service_id}] TESTING: After 5s window status: {status2}")
        
        # Wait another 30 seconds and test again
        time.sleep(30)
        result3 = self._apply_extended_window_logic(agent_id, "on", "off")
        status3 = self.get_extended_window_status(agent_id)
        logger.info(f"[{self.service_id}] TESTING: After 35s total, rule says OFF, result: {result3}")
        logger.info(f"[{self.service_id}] TESTING: After 35s window status: {status3}")
        
        # Wait another 30 seconds (65s total) and test expiration
        time.sleep(30)
        result4 = self._apply_extended_window_logic(agent_id, "on", "off")
        status4 = self.get_extended_window_status(agent_id)
        logger.info(f"[{self.service_id}] TESTING: After 65s total, rule says OFF, result: {result4}")
        logger.info(f"[{self.service_id}] TESTING: After 65s window status: {status4}")
        
        # Clean up test
        with self.extended_window_lock:
            if agent_id in self.extended_window_agents:
                del self.extended_window_agents[agent_id]
        
        logger.info(f"[{self.service_id}] TESTING: Extended window timing test completed") 