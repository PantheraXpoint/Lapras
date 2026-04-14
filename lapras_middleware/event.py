from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Union, List
import logging
import threading
from queue import Queue, Empty
import os
import json
import time
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

# Create a dedicated logger for events
event_logger = logging.getLogger('event_logger')
event_logger.setLevel(logging.INFO)

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Create file handler for events
event_file_handler = logging.FileHandler('logs/events.log')
event_file_handler.setLevel(logging.INFO)

# Create formatter for event logs
event_formatter = logging.Formatter('%(asctime)s - %(message)s')
event_file_handler.setFormatter(event_formatter)

# Add handler to event logger
event_logger.addHandler(event_file_handler)

@dataclass
class EventMetadata:
    """Event metadata containing type and context information."""
    id: str
    timestamp: str
    type: str  # "readSensor", "updateContext", "applyAction", "actionReport"
    location: Optional[str] = None
    contextType: Optional[str] = None
    priority: str = "Normal"  # "Low", "Normal", "High", "Critical"
    
    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.timestamp:
            self.timestamp = list(time.gmtime())

@dataclass
class EntityInfo:
    """Source or target entity information."""
    entityType: str  # "sensorAgent", "virtualAgent", "contextManager"
    entityId: str

@dataclass
class Event:
    """Unified event structure for all system communications."""
    event: EventMetadata
    source: EntityInfo
    payload: Dict[str, Any]

# =============================================================================
# PAYLOAD STRUCTURES FOR DIFFERENT EVENT TYPES
# =============================================================================

# ASYNCHRONOUS/CONTINUOUS EVENTS (Stream-based communication)
@dataclass
class SensorPayload:
    """Payload for readSensor events (ASYNCHRONOUS - continuous stream)."""
    sensor_type: str  # "infrared", "temperature", "humidity"
    value: Union[float, int, str, bool]
    unit: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

@dataclass
class ContextPayload:
    """Payload for updateContext events (ASYNCHRONOUS - continuous stream)."""
    agent_type: str  # "aircon", "light", "microwave"
    state: Dict[str, Any]
    sensors: Optional[Dict[str, Any]] = None

# SYNCHRONOUS/ONE-TIME EVENTS (Request-Response communication)
@dataclass
class ActionPayload:
    """Payload for applyAction events (SYNCHRONOUS - one-time request)."""
    actionName: str  # "turn_on", "turn_off", "set_temperature"
    parameters: Optional[Dict[str, Any]] = None

@dataclass
class ActionReportPayload:
    """Payload for actionReport events (SYNCHRONOUS - one-time response)."""
    command_id: str
    success: bool
    message: Optional[str] = None
    new_state: Optional[Dict[str, Any]] = None

# Add new payload classes for threshold configuration
@dataclass
class ThresholdConfigPayload:
    """Payload for thresholdConfig events (threshold management)."""
    threshold_type: str  # "light", "temperature"
    agent_id: str
    config: Dict[str, Any]  # threshold values and parameters

@dataclass
class ThresholdConfigResultPayload:
    """Payload for thresholdConfigResult events (threshold configuration response)."""
    command_id: str
    success: bool
    message: str
    agent_id: str
    threshold_type: str
    current_config: Dict[str, Any]

class EventFactory:
    """Factory class to create standardized events."""
    
    # ASYNCHRONOUS EVENT CREATORS
    @staticmethod
    def create_sensor_event(
        sensor_id: str,
        sensor_type: str,
        value: Union[float, int, str, bool],
        unit: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        location: Optional[str] = None
    ) -> Event:
        """Create a readSensor event from SensorAgent (ASYNCHRONOUS - broadcast to VirtualAgents)."""
        return Event(
            event=EventMetadata(
                id="",  # Will be auto-generated
                timestamp="",  # Will be auto-generated
                type="readSensor",
                location=location,
                contextType=sensor_type
            ),
            source=EntityInfo(
                entityType="sensorAgent",
                entityId=sensor_id
            ),
            payload=asdict(SensorPayload(
                sensor_type=sensor_type,
                value=value,
                unit=unit,
                metadata=metadata
            ))
        )
    
    @staticmethod
    def create_context_event(
        virtual_agent_id: str,
        agent_type: str,
        state: Dict[str, Any],
        sensors: Optional[Dict[str, Any]] = None,
        location: Optional[str] = None
    ) -> Event:
        """Create an updateContext event from VirtualAgent (ASYNCHRONOUS - routed via MQTT topic)."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="updateContext",
                location=location,
                contextType=agent_type
            ),
            source=EntityInfo(
                entityType="virtualAgent",
                entityId=virtual_agent_id
            ),
            payload=asdict(ContextPayload(
                agent_type=agent_type,
                state=state,
                sensors=sensors
            ))
        )
    
    # SYNCHRONOUS EVENT CREATORS
    @staticmethod
    def create_action_event(
        virtual_agent_id: str,
        action_name: str,
        parameters: Optional[Dict[str, Any]] = None,
        location: Optional[str] = None,
        priority: str = "Normal"
    ) -> Event:
        """Create an applyAction event from ContextManager (SYNCHRONOUS Request - routed via MQTT topic)."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="applyAction",
                location=location,
                priority=priority
            ),
            source=EntityInfo(
                entityType="contextManager",
                entityId="CM-MainController"
            ),
            payload=asdict(ActionPayload(
                actionName=action_name,
                parameters=parameters
            ))
        )
    
    @staticmethod
    def create_action_report_event(
        virtual_agent_id: str,
        command_id: str,
        success: bool,
        message: Optional[str] = None,
        new_state: Optional[Dict[str, Any]] = None
    ) -> Event:
        """Create an actionReport event from VirtualAgent (SYNCHRONOUS Response - routed via MQTT topic)."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="actionReport"
            ),
            source=EntityInfo(
                entityType="virtualAgent",
                entityId=virtual_agent_id
            ),
            payload=asdict(ActionReportPayload(
                command_id=command_id,
                success=success,
                message=message,
                new_state=new_state
            ))
        )

    @staticmethod
    def create_rules_command_event(
        action: str,  # "load", "reload", "list", "clear", "switch"
        rule_files: Optional[List[str]] = None,
        preset_name: Optional[str] = None,
        source_entity_id: str = "Dashboard"
    ) -> Event:
        """Create a rulesCommand event for managing rules dynamically."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="rulesCommand",
                priority="High"  # Rule changes are high priority
            ),
            source=EntityInfo(
                entityType="dashboard",
                entityId=source_entity_id
            ),
            payload={
                "action": action,
                "rule_files": rule_files or [],
                "preset_name": preset_name  # Optional preset name for easier management
            }
        )

    @staticmethod
    def create_sensor_config_event(
        target_agent_id: str,
        action: str,  # "configure", "add", "remove", "list"
        sensor_config: Optional[Dict[str, List[str]]] = None,
        source_entity_id: str = "Dashboard"
    ) -> Event:
        """Create a sensorConfig event for dynamic sensor management."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="sensorConfig"
            ),
            source=EntityInfo(
                entityType="dashboard",
                entityId=source_entity_id
            ),
            payload={
                "target_agent_id": target_agent_id,
                "action": action,
                "sensor_config": sensor_config or {}
            }
        )

    @staticmethod
    def create_sensor_config_result_event(
        agent_id: str,
        command_id: str,
        success: bool,
        message: str,
        action: str,
        current_sensors: List[str]
    ) -> Event:
        """Create a sensorConfigResult event from virtual agent back to context manager."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="sensorConfigResult"
            ),
            source=EntityInfo(
                entityType="virtualAgent",
                entityId=agent_id
            ),
            payload={
                "command_id": command_id,
                "success": success,
                "message": message,
                "action": action,
                "current_sensors": current_sensors
            }
        )

    @staticmethod
    def create_dashboard_rules_request_event(
        action: str,  # "switch_preset", "switch", "load", "clear", "list", "list_presets"
        preset_name: Optional[str] = None,
        rule_files: Optional[List[str]] = None,
        source_entity_id: str = "Dashboard"
    ) -> Event:
        """Create a dashboardRulesRequest event for rule management via dashboard."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="dashboardRulesRequest"
            ),
            source=EntityInfo(
                entityType="dashboard",
                entityId=source_entity_id
            ),
            payload={
                "action": action,
                "preset_name": preset_name,
                "rule_files": rule_files or []
            }
        )

    @staticmethod
    def create_dashboard_rules_response_event(
        command_id: str,
        success: bool,
        message: str,
        action: str,
        dashboard_id: str,
        source_entity_id: str = "RuleAgent"
    ) -> Event:
        """Create a dashboardRulesResponse event from rule agent back to dashboard."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="dashboardRulesResponse"
            ),
            source=EntityInfo(
                entityType="ruleAgent",
                entityId=source_entity_id
            ),
            payload={
                "command_id": command_id,
                "success": success,
                "message": message,
                "action": action,
                "dashboard_id": dashboard_id
            }
        )

    @staticmethod
    def create_dashboard_command_result_event(
        command_id: str,
        success: bool,
        message: str,
        agent_id: Optional[str],
        action_event_id: Optional[str] = None
    ) -> Event:
        """Create a dashboardCommandResult event for manual control command results."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="dashboardCommandResult"
            ),
            source=EntityInfo(
                entityType="contextManager",
                entityId="CM-MainController"
            ),
            payload={
                "command_id": command_id,
                "success": success,
                "message": message,
                "agent_id": agent_id,
                "action_event_id": action_event_id
            }
        )

    @staticmethod
    def create_dashboard_control_mode_command_event(
        mode: Optional[str] = None,
        action: str = "set",
        source_entity_id: str = "Dashboard",
    ) -> Event:
        """Create a dashboardControlModeCommand event for manual/automated mode switching."""
        payload: Dict[str, Any] = {"action": action}
        if mode is not None:
            payload["mode"] = mode
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="dashboardControlModeCommand"
            ),
            source=EntityInfo(
                entityType="dashboard",
                entityId=source_entity_id
            ),
            payload=payload
        )

    @staticmethod
    def create_dashboard_control_mode_result_event(
        command_id: str,
        success: bool,
        mode: str,
        message: str,
    ) -> Event:
        """Create a dashboardControlModeResult event for mode command responses."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="dashboardControlModeResult"
            ),
            source=EntityInfo(
                entityType="contextManager",
                entityId="CM-MainController"
            ),
            payload={
                "command_id": command_id,
                "success": success,
                "mode": mode,
                "message": message,
            }
        )

    @staticmethod
    def create_dashboard_state_update_event(
        agents: Dict[str, Any],
        summary: Dict[str, Any]
    ) -> Event:
        """Create a dashboardStateUpdate event for publishing system state to dashboard."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="dashboardStateUpdate"
            ),
            source=EntityInfo(
                entityType="contextManager",
                entityId="CM-MainController"
            ),
            payload={
                "agents": agents,
                "summary": summary
            }
        )

    @staticmethod
    def create_rules_command_result_event(
        command_id: str,
        success: bool,
        message: str,
        action: str,
        loaded_files: Optional[List[str]] = None
    ) -> Event:
        """Create a rulesCommandResult event for rules management command results."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="rulesCommandResult"
            ),
            source=EntityInfo(
                entityType="contextManager",
                entityId="CM-MainController"
            ),
            payload={
                "command_id": command_id,
                "success": success,
                "message": message,
                "action": action,
                "loaded_files": loaded_files or []
            }
        )

    @staticmethod
    def create_sensor_config_command_result_event(
        command_id: str,
        success: bool,
        message: str,
        agent_id: Optional[str],
        action: Optional[str] = None,
        current_sensors: Optional[List[str]] = None
    ) -> Event:
        """Create a sensorConfigCommandResult event for sensor configuration command results."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="sensorConfigCommandResult"
            ),
            source=EntityInfo(
                entityType="contextManager",
                entityId="CM-MainController"
            ),
            payload={
                "command_id": command_id,
                "success": success,
                "message": message,
                "agent_id": agent_id,
                "action": action,
                "current_sensors": current_sensors or []
            }
        )

    @staticmethod
    def create_threshold_config_event(
        agent_id: str,
        threshold_type: str,  # "light", "temperature"
        config: Dict[str, Any],
        source_entity_id: str = "Dashboard"
    ) -> Event:
        """Create a thresholdConfig event for dynamic threshold management."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="thresholdConfig",
                priority="Normal"
            ),
            source=EntityInfo(
                entityType="dashboard",
                entityId=source_entity_id
            ),
            payload=asdict(ThresholdConfigPayload(
                threshold_type=threshold_type,
                agent_id=agent_id,
                config=config
            ))
        )

    @staticmethod
    def create_threshold_config_result_event(
        agent_id: str,
        command_id: str,
        success: bool,
        message: str,
        threshold_type: str,
        current_config: Dict[str, Any]
    ) -> Event:
        """Create a thresholdConfigResult event from virtual agent back to context manager."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="thresholdConfigResult"
            ),
            source=EntityInfo(
                entityType="virtualAgent",
                entityId=agent_id
            ),
            payload=asdict(ThresholdConfigResultPayload(
                command_id=command_id,
                success=success,
                message=message,
                agent_id=agent_id,
                threshold_type=threshold_type,
                current_config=current_config
            ))
        )

    @staticmethod
    def create_dashboard_threshold_command_result_event(
        command_id: str,
        success: bool,
        message: str,
        agent_id: str,
        threshold_type: str,
        current_config: Dict[str, Any]
    ) -> Event:
        """Create a dashboardThresholdCommandResult event for threshold configuration command results."""
        return Event(
            event=EventMetadata(
                id="",
                timestamp="",
                type="dashboardThresholdCommandResult"
            ),
            source=EntityInfo(
                entityType="contextManager",
                entityId="CM-MainController"
            ),
            payload={
                "command_id": command_id,
                "success": success,
                "message": message,
                "agent_id": agent_id,
                "threshold_type": threshold_type,
                "current_config": current_config
            }
        )

class MQTTMessage:
    """Utility class for MQTT message serialization/deserialization."""
    
    @staticmethod
    def serialize(event: Event) -> str:
        """Serialize Event object to JSON string for MQTT."""
        return json.dumps(asdict(event), indent=2)
    
    @staticmethod
    def deserialize(message: str) -> Event:
        """Deserialize JSON string to Event object."""
        data = json.loads(message)
        
        # Reconstruct the Event object
        event_data = data["event"]
        source_data = data["source"]
        payload_data = data["payload"]
        
        return Event(
            event=EventMetadata(**event_data),
            source=EntityInfo(**source_data),
            payload=payload_data
        )
    
    @staticmethod
    def get_payload_as(event: Event, payload_class):
        """Extract payload as specific payload class."""
        if payload_class == SensorPayload:
            return SensorPayload(**event.payload)
        elif payload_class == ContextPayload:
            return ContextPayload(**event.payload)
        elif payload_class == ActionPayload:
            return ActionPayload(**event.payload)
        elif payload_class == ActionReportPayload:
            return ActionReportPayload(**event.payload)
        elif payload_class == ThresholdConfigPayload:
            return ThresholdConfigPayload(**event.payload)
        elif payload_class == ThresholdConfigResultPayload:
            return ThresholdConfigResultPayload(**event.payload)
        else:
            return event.payload

class TopicManager:
    """Manages MQTT topic naming conventions."""
    
    # ASYNCHRONOUS/CONTINUOUS TOPICS
    @staticmethod
    def sensor_broadcast(sensor_id: str) -> str:
        """Topic for readSensor events broadcasted by SensorAgent (ASYNCHRONOUS - one-to-many)."""
        return f"sensor/{sensor_id}/readSensor"
    
    @staticmethod
    def virtual_to_context(virtual_agent_id: str) -> str:
        """Topic for updateContext events from VirtualAgent to ContextManager (ASYNCHRONOUS)."""
        return f"virtual/{virtual_agent_id}/to/context/updateContext"
    
    # SYNCHRONOUS/ONE-TIME TOPICS
    @staticmethod
    def context_to_virtual_action(virtual_agent_id: str) -> str:
        """Topic for applyAction events from ContextManager to VirtualAgent (SYNCHRONOUS - Request)."""
        return f"context/to/{virtual_agent_id}/applyAction"
    
    @staticmethod
    def virtual_to_context_report(virtual_agent_id: str) -> str:
        """Topic for actionReport events from VirtualAgent to ContextManager (SYNCHRONOUS - Response)."""
        return f"virtual/{virtual_agent_id}/to/context/actionReport"

    # DASHBOARD CONTROL TOPICS
    @staticmethod
    def dashboard_control_command() -> str:
        """Topic for manual control commands from dashboard."""
        return "dashboard/control/command"
    
    @staticmethod
    def dashboard_control_result() -> str:
        """Topic for dashboard control command results."""
        return "dashboard/control/result"

    @staticmethod
    def dashboard_control_mode_command() -> str:
        """Topic for explicit control mode commands from dashboard."""
        return "dashboard/control/mode/command"

    @staticmethod
    def dashboard_control_mode_result() -> str:
        """Topic for explicit control mode command results."""
        return "dashboard/control/mode/result"
    
    @staticmethod
    def dashboard_context_state() -> str:
        """Topic for publishing current system state to dashboard."""
        return "dashboard/context/state"

    # RULES MANAGEMENT TOPICS
    @staticmethod
    def rules_management_command() -> str:
        """Topic for rules management commands."""
        return "context/rules/command"
    
    @staticmethod
    def rules_management_result() -> str:
        """Topic for rules management command results."""
        return "context/rules/result"
    
    # DASHBOARD RULES TOPICS (via Rule Agent)
    @staticmethod
    def dashboard_rules_request() -> str:
        """Topic for dashboard rule management requests."""
        return "dashboard/rules/request"
    
    @staticmethod
    def dashboard_rules_response() -> str:
        """Topic for dashboard rule management responses."""
        return "dashboard/rules/response"

    # SENSOR CONFIGURATION TOPICS
    @staticmethod
    def sensor_config_command(agent_id: str) -> str:
        """Topic for sensor configuration commands for a specific agent."""
        return f"agent/{agent_id}/sensorConfig"
    
    @staticmethod
    def sensor_config_result(agent_id: str) -> str:
        """Topic for sensor configuration command results."""
        return f"agent/{agent_id}/sensorConfig/result"

    # DASHBOARD SENSOR CONFIG TOPICS
    @staticmethod
    def dashboard_sensor_config_command() -> str:
        """Topic for dashboard sensor configuration commands to context manager."""
        return "dashboard/sensor/config/command"
    
    @staticmethod
    def dashboard_sensor_config_result() -> str:
        """Topic for dashboard sensor configuration command results."""
        return "dashboard/sensor/config/result"

    # THRESHOLD CONFIGURATION TOPICS
    @staticmethod
    def dashboard_threshold_command() -> str:
        """Topic for threshold configuration commands from dashboard."""
        return "dashboard/threshold/command"
    
    @staticmethod
    def dashboard_threshold_result() -> str:
        """Topic for threshold configuration command results."""
        return "dashboard/threshold/result"
    
    @staticmethod
    def threshold_config_command(agent_id: str) -> str:
        """Topic for threshold configuration commands for a specific agent."""
        return f"agent/{agent_id}/thresholdConfig"
    
    @staticmethod
    def threshold_config_result(agent_id: str) -> str:
        """Topic for threshold configuration command results."""
        return f"agent/{agent_id}/thresholdConfig/result" 