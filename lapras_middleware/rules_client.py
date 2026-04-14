"""
Rules Client - Utility for sending rule management commands to ContextRuleManager
"""

import json
import paho.mqtt.client as mqtt
from typing import List, Optional, Dict, Any
import logging

from .event import EventFactory, MQTTMessage, TopicManager

logger = logging.getLogger(__name__)

class RulesClient:
    """Client for managing rule configurations at runtime via MQTT."""

    def __init__(self, mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883, client_id: str = "RulesClient"):
        """Initialize the rules client."""
        self.client_id = client_id
        self.mqtt_client = mqtt.Client(client_id=client_id)
        self.mqtt_client.connect(mqtt_broker, mqtt_port)
        self.mqtt_client.loop_start()
        logger.info(f"[{self.client_id}] Connected to MQTT broker")

    def switch_to_preset(self, preset_name: str) -> str:
        """Switch to a predefined rule preset."""
        return self._send_rules_command("switch_preset", preset_name=preset_name)

    def switch_to_rules(self, rule_files: List[str]) -> str:
        """Switch to specific rule files."""
        return self._send_rules_command("switch", rule_files=rule_files)

    def load_rules(self, rule_files: List[str]) -> str:
        """Load additional rule files (keeps existing ones)."""
        return self._send_rules_command("load", rule_files=rule_files)

    def clear_all_rules(self) -> str:
        """Clear all loaded rules."""
        return self._send_rules_command("clear")

    def list_loaded_rules(self) -> str:
        """List currently loaded rule files."""
        return self._send_rules_command("list")

    def list_available_presets(self) -> str:
        """List all available rule presets."""
        return self._send_rules_command("list_presets")

    def _send_rules_command(self, action: str, rule_files: Optional[List[str]] = None, preset_name: Optional[str] = None) -> str:
        """Send a rules management command via MQTT."""
        try:
            # Create the rules command event
            event = EventFactory.create_rules_command_event(
                action=action,
                rule_files=rule_files,
                preset_name=preset_name,
                source_entity_id=self.client_id
            )
            
            # Serialize and send via MQTT
            message = MQTTMessage.serialize(event)
            topic = TopicManager.rules_management_command()
            result = self.mqtt_client.publish(topic, message, qos=1)
            
            logger.info(f"[{self.client_id}] Sent rules command '{action}' - Event ID: {event.event.id}")
            return event.event.id  # Return event ID for tracking
            
        except Exception as e:
            logger.error(f"[{self.client_id}] Error sending rules command: {e}")
            return f"Error: {str(e)}"

    def stop(self):
        """Stop the rules client."""
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        logger.info(f"[{self.client_id}] Disconnected from MQTT broker")


# =============================================================================
# USAGE EXAMPLES
# =============================================================================

def demo_usage():
    """Demonstrate how to use the RulesClient."""
    
    # Initialize the client
    client = RulesClient(client_id="Dashboard-RulesManager")
    
    # Example 1: Switch to a preset
    print("Switching to hue_motion_only preset...")
    event_id = client.switch_to_preset("hue_motion_only")
    print(f"Command sent with ID: {event_id}")
    
    # Example 2: Switch to custom rule files
    print("Switching to custom rules...")
    custom_rules = [
        "lapras_middleware/rules/hue_ir_motion.ttl",
        "lapras_middleware/rules/aircon_activity.ttl"
    ]
    event_id = client.switch_to_rules(custom_rules)
    print(f"Command sent with ID: {event_id}")
    
    # Example 3: List available presets
    print("Listing available presets...")
    event_id = client.list_available_presets()
    print(f"Command sent with ID: {event_id}")
    
    # Example 4: List currently loaded rules
    print("Listing loaded rules...")
    event_id = client.list_loaded_rules()
    print(f"Command sent with ID: {event_id}")
    
    # Example 5: Clear all rules
    print("Clearing all rules...")
    event_id = client.clear_all_rules()
    print(f"Command sent with ID: {event_id}")
    
    # Clean up
    client.stop()

if __name__ == "__main__":
    demo_usage() 