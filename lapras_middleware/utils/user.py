from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime
import json
import logging
from threading import Lock

from .component import Component
from .event import Event, EventDispatcher
# from .agent import Agent
from .communicator import MqttCommunicator, MqttMessage

logger = logging.getLogger(__name__)

@dataclass
class UserNotification:
    """Represents a user notification message."""
    name: str
    presence: bool
    timestamp: int
    publisher: str
    
    @classmethod
    def from_payload(cls, payload: bytes) -> Optional['UserNotification']:
        """Create a UserNotification from a payload."""
        try:
            data = json.loads(payload.decode('utf-8'))
            return cls(
                name=data['name'],
                presence=data['presence'],
                timestamp=data['timestamp'],
                publisher=data['publisher']
            )
        except Exception as e:
            logger.error(f"Failed to parse user notification payload: {e}", exc_info=True)
            return None
    
    def to_payload(self) -> bytes:
        """Convert the instance to a payload."""
        data = {
            'name': self.name,
            'presence': self.presence,
            'timestamp': self.timestamp,
            'publisher': self.publisher
        }
        return json.dumps(data).encode('utf-8')
    
    @property
    def message_type(self) -> str:
        """Get the message type."""
        return "USER"
    
    @property
    def qos(self) -> int:
        """Get the QoS level."""
        return 2  # EXACTLY_ONCE
    
    @property
    def retained(self) -> bool:
        """Get whether the message should be retained."""
        return False

class UserManager(Component):
    """Manages user presence and notifications in the system."""
    
    def __init__(self, event_dispatcher, agent):
        """Initialize the user manager."""
        super().__init__(event_dispatcher, agent)
        
        self.mqtt_communicator = agent.mqtt_communicator
        self.user_presence_map: Dict[str, bool] = {}
        self.lock = Lock()
        
    def start(self) -> None:
        """Start the user manager."""
        # Subscribe to user topics
        self.mqtt_communicator.subscribe(
            "user/#",
            self._handle_user_message
        )
        
        # Subscribe to events
        self.event_dispatcher.subscribe(self, "MESSAGE_ARRIVED")
        self.event_dispatcher.subscribe(self, "USER_NOTIFIED")
        
    def handle_event(self, event: Event) -> bool:
        """Handle events."""
        if event.type == "MESSAGE_ARRIVED":
            topic, payload = event.data
            if not topic.startswith("user/"):
                return True
                
            user_notification = UserNotification.from_payload(payload)
            if user_notification:
                self.event_dispatcher.dispatch(Event(
                    type="USER_NOTIFIED",
                    data=user_notification,
                    timestamp=user_notification.timestamp
                ))
            return True
            
        elif event.type == "USER_NOTIFIED":
            user = event.data
            logger.debug("User notified: %s", user.name)
            return True
            
        return False
    
    def publish_user_notification(self, user_name: str, publisher: str) -> None:
        """Publish a user notification."""
        timestamp = int(datetime.now().timestamp() * 1000)
        
        user_presence = self._update_user_presence(user_name)
        
        user_notification = UserNotification(
            name=user_name,
            presence=user_presence,
            timestamp=timestamp,
            publisher=publisher
        )
        
        self.mqtt_communicator.publish(MqttMessage(
            topic=f"user/{user_name}",
            payload=user_notification.to_payload(),
            qos=user_notification.qos,
            retain=user_notification.retained
        ))
    
    def get_user_presence_map(self) -> Dict[str, bool]:
        """Get the map of user presence states."""
        return self.user_presence_map.copy()
    
    def _update_user_presence(self, user_name: str) -> bool:
        """Update the presence state of a user."""
        with self.lock:
            user_presence = self.user_presence_map.get(user_name)
            
            if user_presence is not None:
                user_presence = not user_presence
                self.user_presence_map[user_name] = user_presence
            else:
                self.user_presence_map[user_name] = True
                user_presence = True
            
            logger.debug("User presence map: %s", self.user_presence_map)
            
            return user_presence
    
    def _handle_user_message(self, topic: str, payload: bytes) -> None:
        """Handle user messages."""
        self.event_dispatcher.dispatch(Event(
            type="MESSAGE_ARRIVED",
            data=(topic, payload),
            timestamp=int(datetime.now().timestamp() * 1000)
        )) 