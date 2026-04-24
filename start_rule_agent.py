"""
Rule Agent - Dedicated service for handling dashboard rule change events and forwarding them to the context rule manager.
"""

import logging
import time
import json
import threading
from typing import Dict, Any, Optional, List

import paho.mqtt.client as mqtt

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lapras_middleware.event import EventFactory, MQTTMessage, TopicManager, Event, EventMetadata, EntityInfo
from lapras_middleware.rules_client import RulesClient

logger = logging.getLogger(__name__)

class RuleAgent:
    """
    Dedicated service that listens for dashboard rule change events and forwards them to ContextRuleManager.
    Acts as a bridge between dashboard and the rule management system.
    """

    def __init__(self, mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        """Initialize the rule agent service."""
        self.service_id = "RuleAgent"
        
        # Track pending commands for response correlation
        self.pending_commands: Dict[str, Dict[str, Any]] = {}  # command_id -> {timestamp, source_info}
        self.pending_lock = threading.Lock()
        
        # Available rule presets (duplicated here for validation)
        self.available_presets = {
            "hue_motion_only", "hue_activity_only", "hue_ir_only", "hue_ir_motion",
            "hue_activity_motion", "hue_ir_activity", "hue_all_sensors",
            "aircon_motion_only", "aircon_activity_only", "aircon_ir_only", "aircon_all_sensors",
            "aircon_door_only", "aircon_door_sensors", 
            "both_devices_motion", "both_devices_all",
            # ClubHouseAgent presets
            "back_read", "front_read", "back_nap", "front_nap", "all_normal", "all_clean"
        }
        
        # Set up MQTT client
        mqtt_client_id = f"{self.service_id}-{int(time.time()*1000)}"
        self.mqtt_client = mqtt.Client(client_id=mqtt_client_id)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        
        # Enable logging for debugging
        self.mqtt_client.enable_logger(logger)
        
        try:
            self.mqtt_client.connect(mqtt_broker, mqtt_port)
        except Exception as e:
            logger.error(f"[{self.service_id}] MQTT connection error: {e}", exc_info=True)
            raise

        # Start MQTT client loop
        self.mqtt_client.loop_start()
        logger.info(f"[{self.service_id}] Rule Agent service started with client ID: {mqtt_client_id}")

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            logger.info(f"[{self.service_id}] Successfully connected to MQTT broker.")
            
            # Subscribe to dashboard rule management requests
            dashboard_rules_topic = TopicManager.dashboard_rules_request()
            result1 = self.mqtt_client.subscribe(dashboard_rules_topic, qos=1)
            logger.info(f"[{self.service_id}] Subscribed to {dashboard_rules_topic} (result: {result1})")
            
            # Subscribe to rule management results to forward back to dashboard
            rules_result_topic = TopicManager.rules_management_result()
            result2 = self.mqtt_client.subscribe(rules_result_topic, qos=1)
            logger.info(f"[{self.service_id}] Subscribed to {rules_result_topic} (result: {result2})")
            
        else:
            logger.error(f"[{self.service_id}] Failed to connect to MQTT broker. Result code: {rc}")

    def _on_message(self, client, userdata, msg):
        """Callback when message is received."""
        logger.debug(f"[{self.service_id}] Message received on topic '{msg.topic}'")
        
        try:
            if msg.topic == TopicManager.dashboard_rules_request():
                self._handle_dashboard_rules_request(msg.payload.decode())
            elif msg.topic == TopicManager.rules_management_result():
                self._handle_rules_result(msg.payload.decode())
            else:
                logger.warning(f"[{self.service_id}] Unexpected topic: {msg.topic}")
                
        except Exception as e:
            logger.error(f"[{self.service_id}] Error processing message: {e}", exc_info=True)

    def _handle_dashboard_rules_request(self, message_payload: str):
        """Handle rule management request from dashboard."""
        try:
            event = MQTTMessage.deserialize(message_payload)
            
            if event.event.type != "dashboardRulesRequest":
                logger.error(f"[{self.service_id}] Expected dashboardRulesRequest, got: {event.event.type}")
                return
            
            logger.info(f"[{self.service_id}] Received dashboard rules request: {event.event.id}")
            
            # Extract request details
            action = event.payload.get('action')  # "switch_preset", "switch", "load", "clear", "list", "list_presets"
            preset_name = event.payload.get('preset_name')
            rule_files = event.payload.get('rule_files', [])
            dashboard_id = event.source.entityId
            
            # Validate the request
            validation_result = self._validate_rules_request(action, preset_name, rule_files)
            if not validation_result["valid"]:
                self._send_dashboard_rules_response(
                    event.event.id, False, validation_result["message"], dashboard_id, action
                )
                return
            
            # Track this command for response correlation
            with self.pending_lock:
                self.pending_commands[event.event.id] = {
                    "timestamp": time.time(),
                    "dashboard_id": dashboard_id,
                    "original_action": action
                }
            
            # Forward to ContextRuleManager
            forwarded_command = self._create_rules_command(action, preset_name, rule_files, event.event.id)
            rules_topic = TopicManager.rules_management_command()
            rules_message = MQTTMessage.serialize(forwarded_command)
            
            result = self.mqtt_client.publish(rules_topic, rules_message, qos=1)
            logger.info(f"[{self.service_id}] Forwarded rules command to ContextRuleManager: {action}")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error handling dashboard rules request: {e}", exc_info=True)
            # Send error response to dashboard
            try:
                event = MQTTMessage.deserialize(message_payload)
                self._send_dashboard_rules_response(
                    event.event.id, False, f"Error: {str(e)}", event.source.entityId, "unknown"
                )
            except:
                pass  # Can't even parse the original message

    def _validate_rules_request(self, action: str, preset_name: Optional[str], rule_files: Optional[List[str]]) -> Dict[str, Any]:
        """Validate incoming rules request."""
        
        valid_actions = {"switch_preset", "switch", "load", "clear", "list", "list_presets", "reload"}
        
        if action not in valid_actions:
            return {"valid": False, "message": f"Invalid action '{action}'. Valid actions: {valid_actions}"}
        
        if action == "switch_preset":
            if not preset_name:
                return {"valid": False, "message": "preset_name is required for switch_preset action"}
            if preset_name not in self.available_presets:
                return {"valid": False, "message": f"Unknown preset '{preset_name}'. Available: {self.available_presets}"}
        
        if action in {"switch", "load", "reload"}:
            if not rule_files or not isinstance(rule_files, list):
                return {"valid": False, "message": f"rule_files list is required for {action} action"}
            
            # Basic validation of file paths
            for rule_file in rule_files:
                if not isinstance(rule_file, str) or not rule_file.endswith('.ttl'):
                    return {"valid": False, "message": f"Invalid rule file: {rule_file}. Must be .ttl file"}
        
        return {"valid": True, "message": "Request is valid"}

    def _create_rules_command(self, action: str, preset_name: Optional[str], rule_files: Optional[List[str]], original_command_id: str) -> Event:
        """Create a rules command event to send to ContextRuleManager."""
        
        return Event(
            event=EventMetadata(
                id=original_command_id,  # Use same ID for correlation
                timestamp="",  # Auto-generated
                type="rulesCommand"
            ),
            source=EntityInfo(
                entityType="ruleAgent",
                entityId=self.service_id
            ),
            payload={
                "action": action,
                "preset_name": preset_name,
                "rule_files": rule_files
            }
        )

    def _handle_rules_result(self, message_payload: str):
        """Handle result from ContextRuleManager and forward to dashboard."""
        try:
            event = MQTTMessage.deserialize(message_payload)
            
            if event.event.type != "rulesCommandResult":
                return  # Not a rules result, ignore
            
            command_id = event.payload.get('command_id')
            
            # Check if this is a response to one of our forwarded commands
            with self.pending_lock:
                pending_info = self.pending_commands.get(command_id)
                if pending_info:
                    del self.pending_commands[command_id]
                else:
                    # Not one of our commands, ignore
                    return
            
            # Forward result back to dashboard
            success = event.payload.get('success', False)
            message = event.payload.get('message', 'No message')
            action = event.payload.get('action', 'unknown')
            
            self._send_dashboard_rules_response(
                command_id, success, message, pending_info['dashboard_id'], action
            )
            
            logger.info(f"[{self.service_id}] Forwarded rules result to dashboard: {command_id} - {success}")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error handling rules result: {e}", exc_info=True)

    def _send_dashboard_rules_response(self, command_id: str, success: bool, message: str, dashboard_id: str, action: str):
        """Send response back to dashboard."""
        try:
            response_event = EventFactory.create_dashboard_rules_response_event(
                command_id=command_id,
                success=success,
                message=message,
                action=action,
                dashboard_id=dashboard_id,
                source_entity_id=self.service_id
            )
            
            response_topic = TopicManager.dashboard_rules_response()
            response_message = MQTTMessage.serialize(response_event)
            self.mqtt_client.publish(response_topic, response_message, qos=1)
            
            logger.info(f"[{self.service_id}] Sent response to dashboard {dashboard_id}: {success}")
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error sending dashboard response: {e}")

    def cleanup_pending_commands(self, max_age_seconds: int = 300):
        """Clean up old pending commands that never received responses."""
        current_time = time.time()
        with self.pending_lock:
            expired_commands = [
                cmd_id for cmd_id, info in self.pending_commands.items()
                if current_time - info['timestamp'] > max_age_seconds
            ]
            
            for cmd_id in expired_commands:
                info = self.pending_commands[cmd_id]
                del self.pending_commands[cmd_id]
                logger.warning(f"[{self.service_id}] Cleaned up expired command: {cmd_id}")
                
                # Send timeout response to dashboard
                self._send_dashboard_rules_response(
                    cmd_id, False, "Command timed out", info['dashboard_id'], info['original_action']
                )

    def get_status(self) -> Dict[str, Any]:
        """Get current status of the rule agent."""
        with self.pending_lock:
            return {
                "service_id": self.service_id,
                "pending_commands": len(self.pending_commands),
                "available_presets": list(self.available_presets),
                "uptime": time.time(),
                "mqtt_connected": self.mqtt_client.is_connected()
            }

    def stop(self):
        """Stop the rule agent service."""
        logger.info(f"[{self.service_id}] Stopping Rule Agent service...")
        
        # Clean up any pending commands
        with self.pending_lock:
            for cmd_id, info in self.pending_commands.items():
                self._send_dashboard_rules_response(
                    cmd_id, False, "Service shutting down", info['dashboard_id'], info['original_action']
                )
            self.pending_commands.clear()
        
        if hasattr(self, 'mqtt_client') and self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            logger.info(f"[{self.service_id}] MQTT client disconnected.")
        
        logger.info(f"[{self.service_id}] Rule Agent service stopped.")


# =============================================================================
# STANDALONE SERVICE RUNNER
# =============================================================================

def main():
    """Run Rule Agent as a standalone service."""
    import signal
    import sys
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create and start the rule agent
    rule_agent = RuleAgent()
    
    # Set up signal handler for graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal, stopping Rule Agent...")
        rule_agent.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("Rule Agent service is running. Press Ctrl+C to stop.")
    
    try:
        # Keep the service running
        while True:
            time.sleep(60)  # Sleep for 1 minute
            rule_agent.cleanup_pending_commands()  # Clean up expired commands
            
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, stopping...")
        rule_agent.stop()

if __name__ == "__main__":
    main() 