from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Type, Union
from datetime import datetime
import json
import logging
import re
import tempfile
import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import inspect
from functools import wraps

from .component import Component
from .event import Event, EventDispatcher
# from .agent import Agent
from .communicator import MqttCommunicator, MqttMessage

logger = logging.getLogger(__name__)

def functionality_method(name: str = "", description: str = ""):
    """Decorator for marking methods as functionality methods."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        wrapper.functionality_method = {
            'name': name or func.__name__.title(),
            'description': description
        }
        return wrapper
    return decorator

@dataclass
class ParameterSignature:
    """Represents a parameter signature for a functionality."""
    name: str
    type: Type

@dataclass
class FunctionalitySignature:
    """Represents a functionality signature."""
    name: str
    parameters: List[ParameterSignature] = field(default_factory=list)
    description: str = ""
    
    @classmethod
    def from_method(cls, method: Callable) -> 'FunctionalitySignature':
        """Create a FunctionalitySignature from a method."""
        sig = inspect.signature(method)
        parameters = []
        
        for name, param in sig.parameters.items():
            param_type = param.annotation if param.annotation != inspect.Parameter.empty else Any
            parameters.append(ParameterSignature(name, param_type))
        
        # Get functionality method info from decorator
        functionality_info = getattr(method, 'functionality_method', {})
        name = functionality_info.get('name', method.__name__.title())
        description = functionality_info.get('description', '')
        
        return cls(name=name, parameters=parameters, description=description)

@dataclass
class FunctionalityInvocation:
    """Represents a functionality invocation."""
    name: str
    arguments: Optional[List[Any]] = None
    timestamp: int = field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    publisher: str = ""
    by_user: bool = False
    
    @classmethod
    def from_payload(cls, payload: bytes) -> Optional['FunctionalityInvocation']:
        """Create a FunctionalityInvocation from a payload."""
        try:
            data = json.loads(payload.decode('utf-8'))
            return cls(
                name=data['name'],
                arguments=data.get('arguments'),
                timestamp=data['timestamp'],
                publisher=data['publisher'],
                by_user=data.get('by_user', False)
            )
        except Exception as e:
            logger.error(f"Failed to parse functionality invocation payload: {e}", exc_info=True)
            return None
    
    def to_payload(self) -> bytes:
        """Convert the instance to a payload."""
        data = {
            'name': self.name,
            'timestamp': self.timestamp,
            'publisher': self.publisher,
            'by_user': self.by_user
        }
        if self.arguments is not None:
            data['arguments'] = self.arguments
        return json.dumps(data).encode('utf-8')
    
    @classmethod
    def from_string(cls, string: str, timestamp: Optional[int] = None, publisher: Optional[str] = None) -> 'FunctionalityInvocation':
        """Create a FunctionalityInvocation from a string."""
        pattern = r"([a-zA-Z_]\w*)\(((?:\s*[^ \t\n\x0b\r\f,]+\s*)?|(?:\s*[^ \t\n\x0b\r\f,]+\s*)(?:,(?:\s*[^ \t\n\x0b\r\f,]+\s*))*)\)"
        match = re.match(pattern, string)
        if not match:
            raise ValueError("Failed to parse functionality")
        
        functionality_name = match.group(1)
        args_str = match.group(2)
        
        # Parse arguments
        arguments = []
        if args_str.strip():
            for arg in args_str.split(','):
                arg = arg.strip()
                if arg.lower() == 'true':
                    arguments.append(True)
                elif arg.lower() == 'false':
                    arguments.append(False)
                elif arg.startswith('"') and arg.endswith('"'):
                    arguments.append(arg[1:-1])
                elif arg.isdigit():
                    arguments.append(int(arg))
                elif re.match(r'\d*\.\d+', arg):
                    arguments.append(float(arg))
                else:
                    raise ValueError("Failed to parse argument")
        
        return cls(
            name=functionality_name,
            arguments=arguments,
            timestamp=timestamp or int(datetime.now().timestamp() * 1000),
            publisher=publisher or ""
        )
    
    def __str__(self) -> str:
        """Get string representation."""
        if not self.arguments:
            return f"{self.name}()"
        
        args_str = []
        for arg in self.arguments:
            if isinstance(arg, (bool, int, float)):
                args_str.append(str(arg))
            elif isinstance(arg, str):
                args_str.append(f'"{arg}"')
            else:
                args_str.append(str(arg))
        
        return f"{self.name}({', '.join(args_str)})"
    
    @property
    def message_type(self) -> str:
        """Get the message type."""
        return "FUNCTIONALITY"
    
    @property
    def qos(self) -> int:
        """Get the QoS level."""
        return 2  # EXACTLY_ONCE
    
    @property
    def retained(self) -> bool:
        """Get whether the message should be retained."""
        return False

class FunctionalityExecutor(Component):
    """Executes functionalities in the system."""
    
    def __init__(self, event_dispatcher, agent):
        """Initialize the functionality executor."""
        super().__init__(event_dispatcher, agent)
        
        self.mqtt_communicator = agent.mqtt_communicator
        self.agent_name = agent.agent_config.agent_name
        
        # Initialize executor and maps
        self.functionality_executor = ThreadPoolExecutor(max_workers=1)
        self.functionality_function_map: Dict[str, Callable] = {}
        self.functionality_signature_map: Dict[str, FunctionalitySignature] = {}
        self.lock = Lock()
        
    def start(self) -> None:
        """Start the functionality executor."""
        # Subscribe to functionality topics
        self.mqtt_communicator.subscribe(
            "functionality/#",
            self._handle_functionality_message
        )
        
        # Subscribe to events
        self.event_dispatcher.subscribe(self, "MESSAGE_ARRIVED")
        
    def handle_event(self, event: Event) -> bool:
        """Handle events."""
        if event.type == "MESSAGE_ARRIVED":
            topic, payload = event.data
            if not topic.startswith("functionality/"):
                return True
                
            functionality_invocation = FunctionalityInvocation.from_payload(payload)
            if functionality_invocation:
                self._invoke_functionality(functionality_invocation)
            return True
            
        return False
    
    def get_functionality_signature(self, functionality_name: str) -> Optional[FunctionalitySignature]:
        """Get the signature of a functionality."""
        return self.functionality_signature_map.get(functionality_name)
    
    def list_functionality_signatures(self) -> List[FunctionalitySignature]:
        """Get all functionality signatures."""
        return list(self.functionality_signature_map.values())
    
    def register_functionality(self, functionality_name: str, function: Callable) -> None:
        """Register a functionality with a function."""
        with self.lock:
            self.functionality_function_map[functionality_name] = function
            
            # Create signature from function
            signature = FunctionalitySignature.from_method(function)
            self.functionality_signature_map[functionality_name] = signature
            
            logger.debug("Functionality %s has been registered", functionality_name)
            
            # Subscribe to functionality topic
            self.mqtt_communicator.subscribe(
                f"functionality/{functionality_name}",
                self._handle_functionality_message
            )
    
    def invoke_functionality(self, functionality_name: str, arguments: Optional[List[Any]] = None) -> None:
        """Invoke a functionality locally."""
        functionality_invocation = FunctionalityInvocation(
            name=functionality_name,
            arguments=arguments,
            timestamp=int(datetime.now().timestamp() * 1000),
            publisher=self.agent_name
        )
        self._invoke_functionality(functionality_invocation)
    
    def invoke_remote_functionality(self, functionality_name: str, arguments: Optional[List[Any]] = None) -> None:
        """Invoke a functionality remotely."""
        functionality_invocation = FunctionalityInvocation(
            name=functionality_name,
            arguments=arguments,
            timestamp=int(datetime.now().timestamp() * 1000),
            publisher=self.agent_name
        )
        
        self.mqtt_communicator.publish(MqttMessage(
            topic=f"functionality/{functionality_name}",
            payload=functionality_invocation.to_payload(),
            qos=functionality_invocation.qos,
            retain=functionality_invocation.retained
        ))
    
    def _invoke_functionality(self, functionality_invocation: FunctionalityInvocation) -> None:
        """Invoke a functionality."""
        functionality_name = functionality_invocation.name
        function = self.functionality_function_map.get(functionality_name)
        
        if function is None:
            logger.error("Functionality %s not found", functionality_name)
            return
        
        signature = self.functionality_signature_map.get(functionality_name)
        if signature is None:
            logger.error("Signature for functionality %s not found", functionality_name)
            return
        
        try:
            arguments = self._process_arguments(functionality_invocation.arguments, signature)
        except ValueError as e:
            logger.error("Failed to process arguments for functionality %s: %s", functionality_name, e)
            return
        
        def execute():
            logger.debug("Invoking functionality %s", functionality_name)
            try:
                function(*arguments)
            except Exception as e:
                logger.error("Error executing functionality %s: %s", functionality_name, e)
        
        self.functionality_executor.submit(execute)
    
    def _process_arguments(self, arguments: Optional[List[Any]], signature: FunctionalitySignature) -> List[Any]:
        """Process arguments according to the signature."""
        if arguments is None:
            if len(signature.parameters) != 0:
                raise ValueError("Number of arguments don't agree")
            return []
        
        if len(arguments) != len(signature.parameters):
            raise ValueError("Number of arguments don't agree")
        
        result = []
        for i, param_sig in enumerate(signature.parameters):
            arg = arguments[i]
            
            # Handle file type parameters
            if param_sig.type == bytes:
                if isinstance(arg, str):
                    # Decode base64 string
                    try:
                        file_data = base64.b64decode(arg.replace('\n', ''))
                        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                            temp_file.write(file_data)
                            result.append(temp_file.name)
                    except Exception as e:
                        logger.error("Error processing file argument: %s", e)
                        result.append(None)
                else:
                    result.append(arg)
            else:
                result.append(arg)
        
        return result
    
    def _handle_functionality_message(self, topic: str, payload: bytes) -> None:
        """Handle functionality messages."""
        self.event_dispatcher.dispatch(Event(
            type="MESSAGE_ARRIVED",
            data=(topic, payload),
            timestamp=int(datetime.now().timestamp() * 1000)
        )) 