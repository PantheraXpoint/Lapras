import logging
import time
import json
import threading
from typing import Dict, Any
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

class Agent:
    """Simplified agent class with local state and MQTT communication."""
    
    def __init__(self, agent_id: str, mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        """Initialize the agent with an ID and MQTT connection."""
        self.agent_id = agent_id
        self.local_state: Dict[str, Any] = {}
        self.state_lock = threading.Lock()  # Lock for thread-safe state access
        
        # Set up MQTT client with explicit client ID to avoid conflicts
        client_id = f"{self.agent_id}_{int(time.time()*1000)}"  # Unique client ID
        self.mqtt_client = mqtt.Client(client_id=client_id)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        
        # Add logging callback to debug MQTT issues
        self.mqtt_client.on_log = self._on_log
        
        # Start MQTT client loop BEFORE connecting
        self.mqtt_client.loop_start()
        
        # Now connect
        logger.info(f"[{self.agent_id}] Connecting to MQTT broker {mqtt_broker}:{mqtt_port} with client ID: {client_id}")
        self.mqtt_client.connect(mqtt_broker, mqtt_port)
        
        # Wait a moment for connection to establish
        time.sleep(0.5)
        
        # Start threads
        self.running = True
        self.perception_thread = threading.Thread(target=self._perception_loop)
        self.publish_thread = threading.Thread(target=self._publish_loop)
        
        # Start all threads
        self.perception_thread.start()
        self.publish_thread.start()
    
    def _on_log(self, client, userdata, level, buf):
        """MQTT logging callback for debugging."""
        logger.debug(f"[{self.agent_id}] MQTT Log: {buf}")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        logger.info(f"[{self.agent_id}] Connected to MQTT broker with result code {rc}")
        # Subscribe to context distribution channel
        self.mqtt_client.subscribe("context_dist")
    
    def _on_message(self, client, userdata, msg):
        """Callback when message is received from MQTT broker."""
        try:
            logger.debug(f"[{self.agent_id}] Base Agent received MQTT message on topic: {msg.topic}")
            data = json.loads(msg.payload.decode())
            if data.get("agent_id") == self.agent_id:
                # Update local state with received data
                with self.state_lock:
                    self.local_state.update(data.get("state", {}))
                logger.info(f"[{self.agent_id}] Updated local state from message: {data}")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error processing message: {e}")
    
    def perception(self) -> None:
        """Update local state based on sensor readings. Override this method."""
        pass
    
    def _perception_loop(self) -> None:
        """Loop that regularly updates local state through perception."""
        while self.running:
            try:
                self.perception()
                time.sleep(0.1)  # Update every 100ms for responsive sensor reading
            except Exception as e:
                logger.error(f"Error in perception loop: {e}")
    
    def _publish_loop(self) -> None:
        """Loop that regularly publishes local state to context center."""
        while self.running:
            try:
                with self.state_lock:
                    message = {
                        "agent_id": self.agent_id,
                        "state": self.local_state.copy(),  # Create a copy to avoid holding lock during publish
                        "timestamp": time.time()
                    }
                self.mqtt_client.publish("context_center", json.dumps(message))
                time.sleep(2)  # Publish every 2 seconds
            except Exception as e:
                logger.error(f"Error in publish loop: {e}")
    
    def stop(self) -> None:
        """Stop all threads and MQTT client."""
        self.running = False
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        
        # Wait for threads to finish
        self.perception_thread.join()
        self.publish_thread.join() 