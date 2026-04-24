from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
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
class TaskInstance:
    """Represents an instance of a task in the system."""
    id: int
    task_name: str
    users: Set[str] = field(default_factory=set)
    
    @classmethod
    def get_idle_task(cls) -> 'TaskInstance':
        """Get the idle task instance."""
        return cls(0, "Idle", set())

@dataclass
class TaskInitiation:
    """Represents a task initiation message."""
    name: str
    timestamp: int
    publisher: str
    involved_agents: Set[str] = field(default_factory=set)
    involved_users: Set[str] = field(default_factory=set)
    id: Optional[int] = None
    
    @classmethod
    def from_payload(cls, payload: bytes) -> Optional['TaskInitiation']:
        """Create a TaskInitiation from a payload."""
        try:
            data = json.loads(payload.decode('utf-8'))
            return cls(
                name=data['name'],
                timestamp=data['timestamp'],
                publisher=data['publisher'],
                involved_agents=set(data.get('involved_agents', [])),
                involved_users=set(data.get('involved_users', [])),
                id=data.get('id')
            )
        except Exception as e:
            logger.error(f"Failed to parse task initiation payload: {e}", exc_info=True)
            return None
    
    def to_payload(self) -> bytes:
        """Convert the instance to a payload."""
        data = {
            'name': self.name,
            'timestamp': self.timestamp,
            'publisher': self.publisher,
            'involved_agents': list(self.involved_agents),
            'involved_users': list(self.involved_users)
        }
        if self.id is not None:
            data['id'] = self.id
        return json.dumps(data).encode('utf-8')
    
    @property
    def message_type(self) -> str:
        """Get the message type."""
        return "TASK_INITIATION"
    
    @property
    def qos(self) -> int:
        """Get the QoS level."""
        return 2  # EXACTLY_ONCE
    
    @property
    def retained(self) -> bool:
        """Get whether the message should be retained."""
        return False

@dataclass
class TaskTermination:
    """Represents a task termination message."""
    name: str
    timestamp: int
    publisher: str
    task_id: int
    
    @classmethod
    def from_payload(cls, payload: bytes) -> Optional['TaskTermination']:
        """Create a TaskTermination from a payload."""
        try:
            data = json.loads(payload.decode('utf-8'))
            return cls(
                name=data['name'],
                timestamp=data['timestamp'],
                publisher=data['publisher'],
                task_id=data['task_id']
            )
        except Exception as e:
            logger.error(f"Failed to parse task termination payload: {e}", exc_info=True)
            return None
    
    def to_payload(self) -> bytes:
        """Convert the instance to a payload."""
        data = {
            'name': self.name,
            'timestamp': self.timestamp,
            'publisher': self.publisher,
            'task_id': self.task_id
        }
        return json.dumps(data).encode('utf-8')
    
    @property
    def message_type(self) -> str:
        """Get the message type."""
        return "TASK_TERMINATION"
    
    @property
    def qos(self) -> int:
        """Get the QoS level."""
        return 2  # EXACTLY_ONCE
    
    @property
    def retained(self) -> bool:
        """Get whether the message should be retained."""
        return False

@dataclass
class TaskNotification:
    """Represents a task notification message."""
    name: str
    timestamp: int
    publisher: str
    involved_agents: List[str] = field(default_factory=list)
    
    @classmethod
    def from_payload(cls, payload: bytes) -> Optional['TaskNotification']:
        """Create a TaskNotification from a payload."""
        try:
            data = json.loads(payload.decode('utf-8'))
            return cls(
                name=data['name'],
                timestamp=data['timestamp'],
                publisher=data['publisher'],
                involved_agents=data.get('involved_agents', [])
            )
        except Exception as e:
            logger.error(f"Failed to parse task notification payload: {e}", exc_info=True)
            return None
    
    def to_payload(self) -> bytes:
        """Convert the instance to a payload."""
        data = {
            'name': self.name,
            'timestamp': self.timestamp,
            'publisher': self.publisher,
            'involved_agents': self.involved_agents
        }
        return json.dumps(data).encode('utf-8')
    
    @property
    def message_type(self) -> str:
        """Get the message type."""
        return "TASK"
    
    @property
    def qos(self) -> int:
        """Get the QoS level."""
        return 2  # EXACTLY_ONCE
    
    @property
    def retained(self) -> bool:
        """Get whether the message should be retained."""
        return False

class TaskManager(Component):
    """Manages tasks in the system."""
    
    def __init__(self, event_dispatcher, agent):
        """Initialize the task manager."""
        super().__init__(event_dispatcher, agent)
        
        self.mqtt_communicator = agent.mqtt_communicator
        self.ongoing_tasks: Dict[int, TaskInstance] = {}
        self.lock = Lock()
        
    def start(self) -> None:
        """Start the task manager."""
        # Subscribe to task topics
        self.mqtt_communicator.subscribe(
            "task_initiation/#",
            self._handle_task_initiation
        )
        self.mqtt_communicator.subscribe(
            "task_termination/#",
            self._handle_task_termination
        )
        
        # Subscribe to events
        self.event_dispatcher.subscribe(self, "MESSAGE_ARRIVED")
        self.event_dispatcher.subscribe(self, "TASK_INITIATED")
        self.event_dispatcher.subscribe(self, "TASK_TERMINATED")
        
    def handle_event(self, event: Event) -> bool:
        """Handle events."""
        if event.type == "MESSAGE_ARRIVED":
            topic, payload = event.data
            if topic.startswith("task_initiation/"):
                task_initiation = TaskInitiation.from_payload(payload)
                if task_initiation:
                    task_instance = TaskInstance(
                        id=task_initiation.id,
                        task_name=task_initiation.name,
                        users=task_initiation.involved_users
                    )
                    with self.lock:
                        self.ongoing_tasks[task_instance.id] = task_instance
                    self.event_dispatcher.dispatch(Event(
                        type="TASK_INITIATED",
                        data=task_instance,
                        timestamp=task_initiation.timestamp
                    ))
                    logger.debug(
                        "Task initiated; id: %d, name: %s",
                        task_initiation.id,
                        task_initiation.name
                    )
            elif topic.startswith("task_termination/"):
                logger.debug("Task termination message arrived")
                task_termination = TaskTermination.from_payload(payload)
                if task_termination:
                    logger.debug("Task id: %d", task_termination.task_id)
                    with self.lock:
                        task_instance = self.ongoing_tasks.get(task_termination.task_id)
                        if task_instance:
                            del self.ongoing_tasks[task_termination.task_id]
                            self.event_dispatcher.dispatch(Event(
                                type="TASK_TERMINATED",
                                data=task_instance,
                                timestamp=task_termination.timestamp
                            ))
                            logger.debug("Task terminated: %s", task_instance.task_name)
            return True
            
        elif event.type == "TASK_INITIATED":
            task = event.data
            logger.debug("Task notified: %s", task.task_name)
            return True
            
        elif event.type == "TASK_TERMINATED":
            task = event.data
            logger.debug("Task terminated: %s", task.task_name)
            return True
            
        return False
    
    def list_tasks(self) -> List[TaskInstance]:
        """Get all ongoing tasks."""
        return list(self.ongoing_tasks.values())
    
    def _handle_task_initiation(self, topic: str, payload: bytes) -> None:
        """Handle task initiation messages."""
        self.event_dispatcher.dispatch(Event(
            type="MESSAGE_ARRIVED",
            data=(topic, payload),
            timestamp=int(datetime.now().timestamp() * 1000)
        ))
    
    def _handle_task_termination(self, topic: str, payload: bytes) -> None:
        """Handle task termination messages."""
        self.event_dispatcher.dispatch(Event(
            type="MESSAGE_ARRIVED",
            data=(topic, payload),
            timestamp=int(datetime.now().timestamp() * 1000)
        )) 