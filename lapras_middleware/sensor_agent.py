import logging
import threading
import time
from typing import Optional, Any, Dict
from abc import ABC, abstractmethod

from .agent import Agent
from .event import EventFactory, MQTTMessage, TopicManager

logger = logging.getLogger(__name__)

class SensorAgent(Agent, ABC):
    """Base class for individual sensor agents."""
    
    def __init__(self, sensor_id: str, sensor_type: str, virtual_agent_id: str, 
                 mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        self.sensor_type = sensor_type
        self.virtual_agent_id = virtual_agent_id
        
        # Sensor state
        self.last_reading_time = 0
        self.reading_interval = 1.0  # Default 1.0s reading interval
        self.reading_count = 0  # Track number of readings
        
        # Initialize the base Agent class
        super().__init__(sensor_id, mqtt_broker, mqtt_port)
        
        logger.info(f"[{self.agent_id}] SensorAgent initialized for virtual agent {self.virtual_agent_id}")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Override Agent's _on_connect to avoid conflicting subscriptions."""
        # DON'T call parent's _on_connect to avoid subscribing to "context_dist"
        # super()._on_connect(client, userdata, flags, rc)  # REMOVED to fix conflicts
        
        logger.info(f"[{self.agent_id}] SensorAgent MQTT connected with result code {rc}")
        # SensorAgents only publish, they don't need to subscribe to anything
    
    def set_reading_interval(self, interval: float):
        """Set the sensor reading interval in seconds."""
        self.reading_interval = interval
        logger.info(f"[{self.agent_id}] Reading interval set to {interval}s")
    
    def perception(self) -> None:
        """Override Agent's perception to handle sensor reading and publishing."""
        current_time = time.time()
        
        # Check if it's time for a new reading
        if current_time - self.last_reading_time >= self.reading_interval:
            self._read_and_publish()
            self.last_reading_time = current_time
    
    def _read_and_publish(self):
        """Read sensor data and publish to MQTT using Event structure."""
        try:
            # Read sensor value
            value, unit, metadata = self.read_sensor()
            
            if value is not None:
                self.reading_count += 1
                
                # Create readSensor event using EventFactory (ASYNCHRONOUS - BROADCAST)
                # No target field needed - routing handled by MQTT topic
                event = EventFactory.create_sensor_event(
                    sensor_id=self.agent_id,
                    sensor_type=self.sensor_type,
                    value=value,
                    unit=unit,
                    metadata=metadata
                )
                
                # Publish to MQTT using the broadcast topic pattern
                topic = TopicManager.sensor_broadcast(self.agent_id)
                message = MQTTMessage.serialize(event)
                self.mqtt_client.publish(topic, message, qos=1)
                logger.info(f"[{self.agent_id}] Published reading #{self.reading_count}: {value} {unit} to broadcast topic: {topic}")
                    
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error reading/publishing sensor data: {e}")
    
    def start_sensor(self):
        """Initialize sensor hardware - called at startup."""
        self.initialize_sensor()
        logger.info(f"[{self.agent_id}] Sensor hardware initialized")
    
    def stop(self) -> None:
        """Override Agent's stop to add sensor cleanup."""
        # Clean up sensor hardware
        self.cleanup_sensor()
        # Call parent's stop
        super().stop()
        logger.info(f"[{self.agent_id}] SensorAgent stopped")
    
    # Abstract methods to be implemented by subclasses
    @abstractmethod
    def initialize_sensor(self):
        """Initialize sensor hardware. Called when sensor agent starts."""
        pass
    
    @abstractmethod
    def cleanup_sensor(self):
        """Clean up sensor hardware. Called when sensor agent stops."""
        pass
    
    @abstractmethod
    def read_sensor(self) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """
        Read sensor value.
        
        Returns:
            tuple: (value, unit, metadata)
                - value: The sensor reading (float, int, str, bool)
                - unit: Optional unit string (e.g., "cm", "celsius", "%")
                - metadata: Optional dictionary with additional sensor-specific data
        """
        pass 