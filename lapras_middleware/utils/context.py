from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
import json
import logging
import threading
import time
import queue
import paho.mqtt.client as mqtt

from .event import Event
from .communicator import MqttMessage

logger = logging.getLogger(__name__)

class ContextManager:
    """Manages context information for all agents in the system."""
    
    def __init__(self, mqtt_broker: str = "localhost", mqtt_port: int = 1883):
        """Initialize the context manager."""
        # Thread-safe dictionary to store agent states
        self.context_map: Dict[str, Dict[str, Any]] = {}
        self.context_lock = threading.Lock()
        
        # Set up MQTT client
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.connect(mqtt_broker, mqtt_port)
        
        # Start MQTT client loop
        self.mqtt_client.loop_start()
        
        # Start rule executor publisher thread
        self.running = True
        self.rule_executor_thread = threading.Thread(target=self._rule_executor_publish_loop)
        self.rule_executor_thread.start()
        
        logger.info("[CONTEXT_MANAGER] Context manager initialized")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        logger.info(f"[CONTEXT_MANAGER] Connected to MQTT broker with result code {rc}")
        # Subscribe to context center channel
        self.mqtt_client.subscribe("context_center")
    
    def _on_message(self, client, userdata, msg):
        """Callback when message is received from MQTT broker."""
        try:
            data = json.loads(msg.payload.decode())
            agent_id = data.get("agent_id")
            state = data.get("state", {})
            timestamp = data.get("timestamp")
            
            if agent_id and state is not None:
                with self.context_lock:
                    self.context_map[agent_id] = {
                        "state": state,
                        "timestamp": timestamp,
                        "last_update": time.time()
                    }
                    # Log the updated context map
                    logger.info(f"[CONTEXT_MANAGER] Updated context for agent {agent_id}")
                    logger.info(f"[CONTEXT_MANAGER] Current context map: {json.dumps(self.context_map, indent=2)}")
        except Exception as e:
            logger.error(f"[CONTEXT_MANAGER] Error processing message: {e}")
    
    def _rule_executor_publish_loop(self) -> None:
        """Loop that regularly publishes the context map to the rule executor."""
        while self.running:
            try:
                with self.context_lock:
                    message = {
                        "timestamp": time.time(),
                        "context_map": {
                            agent_id: data["state"]
                            for agent_id, data in self.context_map.items()
                        }
                    }
                self.mqtt_client.publish("to_rule_executor", json.dumps(message))
                logger.info("[CONTEXT_MANAGER] Published context map to rule executor:")
                logger.info(f"[CONTEXT_MANAGER] Context map: {json.dumps(message, indent=2)}")
                time.sleep(2)  # Publish every 2 second
            except Exception as e:
                logger.error(f"[CONTEXT_MANAGER] Error publishing to rule executor: {e}")
    
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
        """Stop the context manager."""
        logger.info("[CONTEXT_MANAGER] Stopping context manager...")
        self.running = False
        self.rule_executor_thread.join()
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        logger.info("[CONTEXT_MANAGER] Context manager stopped") 