from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
from datetime import datetime
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from .component import Component
from .event import Event, EventDispatcher
from .communicator import MqttCommunicator, MqttMessage

if TYPE_CHECKING:
    from .agent import Agent

@dataclass
class Action:
    """Represents an action that can be taken by the system."""
    name: str
    arguments: Optional[List[Any]] = None
    
    def is_still_action(self) -> bool:
        """Check if this is a 'Still' action."""
        return self.name == "Still"
    
    def is_opposite(self, other: 'Action') -> bool:
        """Check if this action is the opposite of another action."""
        n1 = other.name.lower()
        n2 = self.name.lower()
        
        opposites = [
            ('on', 'off'),
            ('off', 'on'),
            ('up', 'down'),
            ('down', 'up'),
            ('increase', 'decrease'),
            ('decrease', 'increase')
        ]
        
        for word1, word2 in opposites:
            if n1.replace(word1, word2) == n2:
                return True
        return False
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Action):
            return False
        return (self.name == other.name and 
                (self.arguments is None and other.arguments is None or
                 self.arguments == other.arguments))
    
    def __hash__(self) -> int:
        return hash(str(self))
    
    def __str__(self) -> str:
        if self.arguments is None:
            return self.name
        return f"{self.name}{self.arguments}"

@dataclass
class ActionInstance:
    """Represents an instance of an action being taken."""
    name: str
    timestamp: int
    publisher: str
    arguments: Optional[List[Any]] = None
    
    @classmethod
    def from_payload(cls, payload: bytes) -> Optional['ActionInstance']:
        """Create an ActionInstance from a payload."""
        try:
            data = json.loads(payload.decode('utf-8'))
            return cls(
                name=data['name'],
                timestamp=data['timestamp'],
                publisher=data['publisher'],
                arguments=data.get('arguments')
            )
        except Exception:
            return None
    
    def to_payload(self) -> bytes:
        """Convert the instance to a payload."""
        data = {
            'name': self.name,
            'timestamp': self.timestamp,
            'publisher': self.publisher
        }
        if self.arguments is not None:
            data['arguments'] = self.arguments
        return json.dumps(data).encode('utf-8')
    
    def get_action(self) -> Action:
        """Get the Action object for this instance."""
        return Action(self.name, self.arguments)
    
    @property
    def message_type(self) -> str:
        """Get the message type."""
        return "ACTION"
    
    @property
    def qos(self) -> int:
        """Get the QoS level."""
        return 2  # EXACTLY_ONCE
    
    @property
    def retained(self) -> bool:
        """Get whether the message should be retained."""
        return False

class ActionManager(Component):
    """Manages actions in the system."""
    
    def __init__(self, event_dispatcher: EventDispatcher, agent: 'Agent'):
        """Initialize the action manager."""
        super().__init__(event_dispatcher, agent)
        
        self.agent_name = agent.agent_config.agent_name
        self.mqtt_communicator = agent.mqtt_communicator
        self.latest_action_map: Dict[str, ActionInstance] = {}
        self.lock = Lock()
        
    def start(self) -> None:
        """Start the action manager."""
        # Subscribe to action topics
        self.mqtt_communicator.subscribe(
            f"action/#",
            self._handle_action_message
        )
        
        # Subscribe to events
        self.event_dispatcher.subscribe(self, "MESSAGE_ARRIVED")
        self.event_dispatcher.subscribe(self, "ACTION_TAKEN")
        
    def handle_event(self, event: Event) -> bool:
        """Handle events."""
        if event.type == "MESSAGE_ARRIVED":
            topic, payload = event.data
            if not topic.startswith("action/"):
                return True
                
            action_instance = ActionInstance.from_payload(payload)
            if action_instance:
                self.event_dispatcher.dispatch(Event(
                    type="ACTION_TAKEN",
                    data=action_instance,
                    timestamp=int(datetime.now().timestamp() * 1000)
                ))
            return True
            
        elif event.type == "ACTION_TAKEN":
            action_instance = event.data
            
            with self.lock:
                latest_action = self.latest_action_map.get(action_instance.name)
                if (latest_action is None or 
                    latest_action.timestamp < action_instance.timestamp):
                    self.latest_action_map[action_instance.name] = action_instance
                    
                    if action_instance.publisher == self.agent_name:
                        self.mqtt_communicator.publish(MqttMessage(
                            topic=f"action/{action_instance.name}",
                            payload=action_instance.to_payload(),
                            qos=action_instance.qos,
                            retain=action_instance.retained
                        ))
            return True
            
        return False
    
    def taken(self, action_name: str) -> None:
        """Record that an action has been taken."""
        action_instance = ActionInstance(
            name=action_name,
            timestamp=int(datetime.now().timestamp() * 1000),
            publisher=self.agent_name
        )
        
        self.event_dispatcher.dispatch(Event(
            type="ACTION_TAKEN",
            data=action_instance,
            timestamp=action_instance.timestamp
        ))

    def _handle_action_message(self, topic: str, payload: bytes) -> None:
        """Handle incoming action messages from MQTT."""
        self.event_dispatcher.dispatch(Event(
            type="MESSAGE_ARRIVED",
            data=(topic, payload),
            timestamp=int(datetime.now().timestamp() * 1000)
        )) 