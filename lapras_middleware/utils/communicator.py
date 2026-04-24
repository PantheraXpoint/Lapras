import json
import logging
from typing import Any, Callable, Dict, Optional
import paho.mqtt.client as mqtt
from dataclasses import dataclass
from datetime import datetime

from .component import Component
from .event import Event, EventDispatcher
# from .agent import Agent

logger = logging.getLogger(__name__)

@dataclass
class MqttMessage:
    """Represents an MQTT message."""
    topic: str
    payload: bytes
    qos: int = 0
    retain: bool = False

class MqttCommunicator(Component):
    """Handles MQTT communication for the agent."""
    
    def __init__(self, event_dispatcher: EventDispatcher, agent=None, **kwargs):
        """Initialize the MQTT communicator.
        
        Args:
            event_dispatcher: The event dispatcher instance
            agent: Optional agent instance. If None, kwargs must contain MQTT configuration
            **kwargs: MQTT configuration when agent is None:
                - broker_host: MQTT broker host (default: 'localhost')
                - broker_port: MQTT broker port (default: 1883)
                - client_id: MQTT client ID
        """
        super().__init__(event_dispatcher, agent)
        
        if agent is not None:
            # Get MQTT configuration from agent config
            self.broker_host = agent.agent_config.get_option('mqtt_broker_host', 'localhost')
            self.broker_port = int(agent.agent_config.get_option('mqtt_broker_port', '1883'))
            self.client_id = f"{agent.agent_config.agent_name}_{datetime.now().timestamp()}"
        else:
            # Get MQTT configuration from kwargs
            self.broker_host = kwargs.get('broker_host', 'localhost')
            self.broker_port = int(kwargs.get('broker_port', 1883))
            self.client_id = kwargs.get('client_id', f"mqtt_client_{datetime.now().timestamp()}")
        
        # Initialize MQTT client
        self.client = mqtt.Client(client_id=self.client_id)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        
        # Message handlers
        self.message_handlers: Dict[str, Callable] = {}
        
        logger.info(f"[MQTT] Initialized MQTT communicator with client ID: {self.client_id}")
        
    def start(self) -> None:
        """Start the MQTT communicator."""
        try:
            logger.info(f"[MQTT] Connecting to broker {self.broker_host}:{self.broker_port}")
            self.client.connect(self.broker_host, self.broker_port)
            self.client.loop_start()
            logger.info("[MQTT] MQTT communicator started")
        except Exception as e:
            logger.error(f"[MQTT] Failed to start MQTT communicator: {e}")
            raise
            
    def stop(self) -> None:
        """Stop the MQTT communicator."""
        try:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("[MQTT] MQTT communicator stopped")
        except Exception as e:
            logger.error(f"[MQTT] Error stopping MQTT communicator: {e}")
        
    def subscribe(self, topic: str, handler: Callable) -> None:
        """Subscribe to an MQTT topic with a message handler."""
        self.message_handlers[topic] = handler
        if self.client.is_connected:
            self.client.subscribe(topic)
            logger.info(f"[MQTT] Subscribed to topic: {topic}")
        else:
            logger.warning(f"[MQTT] Client not connected, subscription to {topic} will be made after connection")
        
    def publish(self, message: MqttMessage) -> None:
        """Publish a message to an MQTT topic."""
        if not self.client.is_connected:
            logger.warning("[MQTT] Not connected to broker, message not published")
            return
            
        try:
            logger.info(f"[MQTT] Publishing message to topic: {message.topic}")
            logger.info(f"[MQTT] Message payload: {message.payload.decode('utf-8')}")
            
            self.client.publish(
                topic=message.topic,
                payload=message.payload,
                qos=message.qos,
                retain=message.retain
            )
            logger.info(f"[MQTT] Successfully published message to {message.topic}")
        except Exception as e:
            logger.error(f"[MQTT] Error publishing message: {e}", exc_info=True)
            
    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict, rc: int) -> None:
        """Handle MQTT client connection."""
        if rc == 0:
            logger.info("[MQTT] Connected to MQTT broker")
            # Resubscribe to topics
            for topic in list(self.message_handlers.keys()):
                self.client.subscribe(topic)
                logger.info(f"[MQTT] Resubscribed to topic: {topic}")
        else:
            logger.error(f"[MQTT] Failed to connect to MQTT broker with code: {rc}")
            
    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        """Handle incoming MQTT messages."""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            logger.info(f"[MQTT] Received message on topic: {topic}")
            logger.info(f"[MQTT] Message payload: {payload}")
            
            # Try to parse JSON payload
            try:
                payload = json.loads(payload)
                logger.info(f"[MQTT] Parsed JSON payload: {json.dumps(payload, indent=2)}")
            except json.JSONDecodeError:
                logger.info("[MQTT] Payload is not JSON")
                pass
                
            # Create event
            event = Event(
                type="MQTT_MESSAGE",
                data={
                    'topic': topic,
                    'payload': payload
                },
                timestamp=int(datetime.now().timestamp() * 1000)
            )
            
            # Dispatch event
            self.event_dispatcher.dispatch(event)
            logger.info(f"[MQTT] Dispatched MQTT_MESSAGE event for topic: {topic}")
            
            # Call message handler if exists
            if topic in self.message_handlers:
                logger.info(f"[MQTT] Calling handler for topic: {topic}")
                self.message_handlers[topic](topic, msg.payload)
            else:
                logger.warning(f"[MQTT] No handler registered for topic: {topic}")
                
        except Exception as e:
            logger.error(f"[MQTT] Error handling message: {e}", exc_info=True)
            
    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: int) -> None:
        """Handle MQTT client disconnection."""
        if rc != 0:
            logger.warning(f"[MQTT] Unexpected disconnection from MQTT broker with code: {rc}")
        else:
            logger.info("[MQTT] Disconnected from MQTT broker") 