#!/usr/bin/env python3
"""
Dashboard Agent - Aggregates sensor data from all sensor types for monitoring.
Similar to LightHueAgent but purely for data collection and visualization.
"""

import logging
import time
import sys
import os
from collections import defaultdict

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lapras_middleware.virtual_agent import VirtualAgent
from lapras_middleware.event import EventFactory, MQTTMessage, TopicManager, SensorPayload

logger = logging.getLogger(__name__)

class DashboardAgent(VirtualAgent):
    def __init__(self, agent_id: str = "dashboard", mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883, 
                 sensor_config: dict = None, transmission_interval: float = 2.0):
        
        # Initialize attributes that might be accessed by perception thread first
        self.transmission_interval = transmission_interval  # seconds between transmissions
        self.last_transmission_time = 0.0
        self.pending_state_update = False
        
        # Default comprehensive sensor configuration 
        if sensor_config is None:
            sensor_config = {
                "infrared": ["infrared_1", "infrared_2"],
                "distance": ["distance_1", "distance_2", "distance_3", "distance_4"],  # Add distance sensors
                "motion": ["motion_01", "motion_02", "motion_03", "motion_04", "motion_05", "motion_06", "motion_07", "motion_08", "motion_mp1", "motion_mf1", "motion_mb1"],
                "temperature": ["temperature_1", "temperature_2","temperature_3"],
                "door": ["door_01"],
                "activity": ["activity_s1b", "activity_s2a", "activity_s3a", "activity_s4a","activity_s5a","activity_s6a","activity_s7a","activity_s2b","activity_s3b","activity_s4b","activity_s5b","activity_s6b","activity_s7b"],
                "light": ["light_1"],  # Add light sensors - will be populated if available
                "tilt": ["tilt_1", "tilt_2"]  # Add both tilt sensors for aircon monitoring
            }
        
        self.sensor_config = sensor_config
        self.supported_sensor_types = ["infrared", "distance", "motion", "temperature", "door", "activity", "light", "tilt"]
        
        # Store sensor data with timestamps for staleness detection
        self.sensor_data = defaultdict(dict)  # sensor_id -> sensor_data
        self.sensor_last_seen = defaultdict(float)  # sensor_id -> timestamp
        
        super().__init__(agent_id, "dashboard", mqtt_broker, mqtt_port)
        
        logger.info(f"[{self.agent_id}] DashboardAgent initialized")

        # Initialize local state for dashboard monitoring
        self.local_state.update({
            "dashboard_status": "active",
            "total_sensors": 0,
            "sensors_online": 0,
            "sensors_offline": 0,
            "last_activity_time": 0,
            "activity_summary": {
                "infrared_active": 0,
                "motion_active": 0,
                "temperature_alerts": 0,
                "doors_open": 0,
                "activity_detected": 0,
                "light_alerts": 0,
                "tilt_alerts": 0
            }
        })
        
        # Dynamically add sensors based on configuration
        self._configure_sensors()
        
        logger.info(f"[{self.agent_id}] DashboardAgent initialization completed with {self.local_state['total_sensors']} sensors and transmission interval: {transmission_interval}s")
    
    def _configure_sensors(self):
        """Configure sensors based on the sensor_config."""
        total_sensors = 0
        for sensor_type, sensor_ids in self.sensor_config.items():
            if sensor_type not in self.supported_sensor_types:
                logger.warning(f"[{self.agent_id}] Unsupported sensor type: {sensor_type}")
                continue
                
            for sensor_id in sensor_ids:
                self.add_sensor_agent(sensor_id)
                total_sensors += 1
                logger.info(f"[{self.agent_id}] Added {sensor_type} sensor: {sensor_id}")
        
        self.local_state["total_sensors"] = total_sensors
        logger.info(f"[{self.agent_id}] Configured {total_sensors} sensors across {len(self.sensor_config)} sensor types")
    
    def _update_sensor_config(self, new_sensor_config: dict, action: str = "configure"):
        """Update subclass-specific sensor configuration (called by base class)."""
        try:
            if action == "configure":
                # Replace entire configuration
                self.sensor_config = new_sensor_config
            elif action == "add":
                # Add to existing configuration
                for sensor_type, sensor_ids in new_sensor_config.items():
                    if sensor_type not in self.sensor_config:
                        self.sensor_config[sensor_type] = []
                    for sensor_id in sensor_ids:
                        if sensor_id not in self.sensor_config[sensor_type]:
                            self.sensor_config[sensor_type].append(sensor_id)
            elif action == "remove":
                # Remove from existing configuration
                for sensor_type, sensor_ids in new_sensor_config.items():
                    if sensor_type in self.sensor_config:
                        for sensor_id in sensor_ids:
                            if sensor_id in self.sensor_config[sensor_type]:
                                self.sensor_config[sensor_type].remove(sensor_id)
                        # Remove empty sensor types
                        if not self.sensor_config[sensor_type]:
                            del self.sensor_config[sensor_type]
            
            # Update total sensors count
            total_sensors = sum(len(sensors) for sensors in self.sensor_config.values())
            with self.state_lock:
                self.local_state["total_sensors"] = total_sensors
            
            logger.info(f"[{self.agent_id}] Updated sensor configuration: {self.sensor_config}")
            logger.info(f"[{self.agent_id}] Total sensors now: {total_sensors}")
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error updating sensor configuration: {e}")
    
    def _process_sensor_update(self, sensor_payload: SensorPayload, sensor_id: str):
        """Process all sensor data updates for dashboard monitoring."""
        current_time = time.time()
        logger.info(f"[{self.agent_id}] Processing sensor update: {sensor_id}, type: {sensor_payload.sensor_type}, value: {sensor_payload.value}")
        
        # Process based on sensor type
        if sensor_payload.sensor_type == "infrared":
            self._process_infrared_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "distance":
            self._process_distance_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "motion":
            self._process_motion_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "temperature":
            self._process_temperature_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "door":
            self._process_door_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "activity":
            self._process_activity_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "light":
            self._process_light_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "tilt":
            self._process_tilt_sensor(sensor_payload, sensor_id, current_time)
        else:
            logger.warning(f"[{self.agent_id}] Unsupported sensor type: {sensor_payload.sensor_type}")
            
        # Update sensor tracking
        self.sensor_last_seen[sensor_id] = current_time
        
        # Update overall activity summary
        self._update_activity_summary()
        
        # Schedule transmission to context manager
        self._schedule_transmission()
    
    def _process_infrared_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process infrared sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
                "last_update": current_time
            }
    
    def _process_distance_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process distance sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
                "last_update": current_time
            }
    
    def _process_motion_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process motion sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
                "last_update": current_time
            }
    
    def _process_temperature_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process temperature sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
                "last_update": current_time
            }
    
    def _process_door_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process door sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
                "last_update": current_time
            }
    
    def _process_activity_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process activity sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
                "last_update": current_time
            }
    
    def _process_light_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process light sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
                "last_update": current_time
            }
    
    def _process_tilt_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process tilt sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
                "last_update": current_time
            }
    
    def _update_activity_summary(self):
        """Update the activity summary based on all sensor data."""
        current_time = time.time()
        
        with self.state_lock:
            # Count sensors online/offline (30 second timeout)
            sensors_online = 0
            sensors_offline = 0
            
            for sensor_id in self.sensor_config.get("infrared", []) + self.sensor_config.get("distance", []) + \
                           self.sensor_config.get("motion", []) + self.sensor_config.get("temperature", []) + \
                           self.sensor_config.get("door", []) + self.sensor_config.get("activity", []) + \
                           self.sensor_config.get("light", []) + self.sensor_config.get("tilt", []):
                if sensor_id in self.sensor_last_seen:
                    if current_time - self.sensor_last_seen[sensor_id] < 30:
                        sensors_online += 1
                    else:
                        sensors_offline += 1
                else:
                    sensors_offline += 1
            
            self.local_state["sensors_online"] = sensors_online
            self.local_state["sensors_offline"] = sensors_offline
            
            # Count active sensors by type
            infrared_active = sum(1 for sensor_id in self.sensor_config.get("infrared", []) 
                                if sensor_id in self.sensor_data and 
                                self.sensor_data[sensor_id].get("metadata", {}).get("proximity_status") == "near")
            
            distance_active = sum(1 for sensor_id in self.sensor_config.get("distance", [])
                                if sensor_id in self.sensor_data and
                                self.sensor_data[sensor_id].get("metadata", {}).get("proximity_status") == "near")
            
            motion_active = sum(1 for sensor_id in self.sensor_config.get("motion", [])
                              if sensor_id in self.sensor_data and
                              self.sensor_data[sensor_id].get("metadata", {}).get("motion_status") == "motion")
            
            temperature_alerts = sum(1 for sensor_id in self.sensor_config.get("temperature", [])
                                   if sensor_id in self.sensor_data and
                                   self.sensor_data[sensor_id].get("metadata", {}).get("temperature_status") in ["hot", "cold"])
            
            doors_open = sum(1 for sensor_id in self.sensor_config.get("door", [])
                           if sensor_id in self.sensor_data and
                           self.sensor_data[sensor_id].get("metadata", {}).get("door_status") == "open")
            
            activity_detected = sum(1 for sensor_id in self.sensor_config.get("activity", [])
                                  if sensor_id in self.sensor_data and
                                  self.sensor_data[sensor_id].get("metadata", {}).get("activity_status") == "active")
            
            light_alerts = sum(1 for sensor_id in self.sensor_config.get("light", [])
                             if sensor_id in self.sensor_data and
                             self.sensor_data[sensor_id].get("metadata", {}).get("light_status") in ["bright", "dark"])
            
            tilt_alerts = sum(1 for sensor_id in self.sensor_config.get("tilt", [])
                             if sensor_id in self.sensor_data and
                             self.sensor_data[sensor_id].get("metadata", {}).get("tilt_status") in ["tilted", "level"])
            
            # Update activity summary
            self.local_state["activity_summary"] = {
                "infrared_active": infrared_active,
                "distance_active": distance_active,
                "motion_active": motion_active,
                "temperature_alerts": temperature_alerts,
                "doors_open": doors_open,
                "activity_detected": activity_detected,
                "light_alerts": light_alerts,
                "tilt_alerts": tilt_alerts
            }
            
            # Update last activity time if any activity detected
            if any([infrared_active, distance_active, motion_active, activity_detected]):
                self.local_state["last_activity_time"] = current_time
    
    def _schedule_transmission(self):
        """Schedule rate-limited transmission to context manager."""
        current_time = time.time()
        
        # Mark that we have a pending state update
        self.pending_state_update = True
        
        # Check if enough time has passed since last transmission
        if current_time - self.last_transmission_time >= self.transmission_interval:
            self._transmit_to_context_manager()
    
    def _transmit_to_context_manager(self):
        """Actually transmit state to context manager and reset pending flag."""
        if self.pending_state_update:
            self._trigger_state_publication()
            self.last_transmission_time = time.time()
            self.pending_state_update = False
            logger.debug(f"[{self.agent_id}] Transmitted dashboard state to context manager")
    
    def perception(self):
        """Internal perception logic - runs continuously."""
        current_time = time.time()
        
        # Handle rate-limited transmissions
        if (self.pending_state_update and 
            current_time - self.last_transmission_time >= self.transmission_interval):
            self._transmit_to_context_manager()
        
        # Update activity summary periodically (even without new sensor data)
        if not hasattr(self, '_last_summary_update') or current_time - self._last_summary_update > 5:
            self._update_activity_summary()
            self._last_summary_update = current_time
        
        # Periodic state logging
        if not hasattr(self, '_last_perception_log') or current_time - self._last_perception_log > 30:
            with self.state_lock:
                summary = self.local_state.get("activity_summary", {})
                logger.info(f"[{self.agent_id}] Dashboard summary - Online: {self.local_state.get('sensors_online', 0)}/{self.local_state.get('total_sensors', 0)}, "
                           f"Activity: IR={summary.get('infrared_active', 0)}, Dist={summary.get('distance_active', 0)}, Motion={summary.get('motion_active', 0)}, "
                           f"Temp alerts={summary.get('temperature_alerts', 0)}, Doors open={summary.get('doors_open', 0)}, "
                           f"Activity={summary.get('activity_detected', 0)}, Light alerts={summary.get('light_alerts', 0)}, "
                           f"Tilt alerts={summary.get('tilt_alerts', 0)}")
            self._last_perception_log = current_time
    
    def _clean_data_for_serialization(self, data):
        """Clean data to ensure it's serializable."""
        if isinstance(data, dict):
            cleaned = {}
            for key, value in data.items():
                if isinstance(value, (str, int, float, bool, type(None))):
                    cleaned[key] = value
                elif isinstance(value, dict):
                    cleaned[key] = self._clean_data_for_serialization(value)
                elif isinstance(value, list):
                    cleaned[key] = [self._clean_data_for_serialization(item) if isinstance(item, dict) else item for item in value]
                else:
                    # Convert other types to string
                    cleaned[key] = str(value)
            return cleaned
        return data
    
    def execute_action(self, action_payload):
        """Dashboard agent does not execute actions - it's read-only."""
        logger.warning(f"[{self.agent_id}] Dashboard agent received action request but does not support actions: {action_payload.actionName}")
        return {
            "success": False,
            "message": "Dashboard agent is read-only and does not support actions"
        } 