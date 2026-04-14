#!/usr/bin/env python3
import logging
import requests
import time
import threading
from typing import Optional, Any, Dict, List
from abc import ABC, abstractmethod
from datetime import datetime

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lapras_middleware.sensor_agent import SensorAgent
from lapras_middleware.event import EventFactory, MQTTMessage, TopicManager

logger = logging.getLogger(__name__)

class MonnitSensorAgent(SensorAgent, ABC):
    """Base class for Monnit sensor agents that handles API communication."""
    
    # Monnit API configuration - fixed values as requested
    API_KEY_ID = "kO1L9ova4KSD"
    API_SECRET_KEY = "Het79frgls3qdGbh9wsju5hOVSQF5AW3"
    NETWORK_ID = 61731
    BASE_URL = "https://www.imonnit.com/json"
    
    # Sensor type to application ID mapping
    SENSOR_TYPE_TO_APP_ID = {
        'temperature': 2,
        'activity': 5,
        'door': 9,
        'tilt': 75,
        'motion': 101,
        'light': 107
    }
    
    def __init__(self, sensor_type: str, virtual_agent_id: str = "any",
                 mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        self.monnit_sensor_type = sensor_type
        self.network_id = self.NETWORK_ID
        
        # Initialize API headers
        self.headers = {
            "APIKeyID": self.API_KEY_ID,
            "APISecretKey": self.API_SECRET_KEY,
            "Content-Type": "application/json"
        }
        
        # Sensor management
        self.monnit_sensors: List[Dict] = []
        self.sensor_cache: Dict[int, Dict] = {}
        self.sensors_ready = threading.Event()
        self.reading_interval = 0.1  # Default 0.1 seconds for Monnit sensors
        
        # Add mapping from sensor_id to friendly_id for consistent naming
        self.sensor_id_mapping: Dict[int, str] = {}
        self.friendly_id_counter = 0
        
        # Initialize parent class with a temporary sensor_id (will be overridden)
        super().__init__(f"monnit_{sensor_type}", sensor_type, virtual_agent_id, mqtt_broker, mqtt_port)
        
        # Set reading interval for Monnit sensors
        self.set_reading_interval(self.reading_interval)
        
        logger.info(f"[{self.agent_id}] MonnitSensorAgent initialized for sensor type: {sensor_type}")
    
    def get_sensor_list(self, name=None, application_id=None, status=None, account_id=None):
        """Get list of sensors with optional filtering."""
        url = f"{self.BASE_URL}/SensorList"
        
        # Build parameters (only include non-None values)
        params = {}
        if name is not None:
            params["Name"] = name
        if self.network_id is not None:
            params["NetworkID"] = self.network_id
        if application_id is not None:
            params["ApplicationID"] = application_id
        if status is not None:
            params["Status"] = status
        if account_id is not None:
            params["accountID"] = account_id
        
        try:
            response = requests.post(url, headers=self.headers, json=params)
            if response.status_code == 200:
                response_data = response.json()
                # Check if there's an error in the response
                if isinstance(response_data.get('Result'), str):
                    logger.error(f"[{self.agent_id}] API Error: {response_data.get('Result')}")
                    return None
                return response_data
            else:
                logger.error(f"[{self.agent_id}] HTTP Error: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"[{self.agent_id}] Exception getting sensor list: {e}")
            return None
    
    def get_single_sensor_data(self, sensor_id):
        """Get data for a specific sensor using SensorGet API."""
        url = f"{self.BASE_URL}/SensorGet"
        params = {"sensorID": sensor_id}
        
        try:
            response = requests.post(url, headers=self.headers, json=params)
            if response.status_code == 200:
                response_data = response.json()
                # Check if there's an error in the response
                if isinstance(response_data.get('Result'), str):
                    logger.error(f"[{self.agent_id}] Sensor API Error for {sensor_id}: {response_data.get('Result')}")
                    return None
                return response_data
            else:
                logger.error(f"[{self.agent_id}] HTTP Error getting sensor {sensor_id}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"[{self.agent_id}] Exception getting sensor {sensor_id}: {e}")
            return None
    
    def get_sensors_by_type(self):
        """Get sensors filtered by type using API-level filtering."""
        app_id = self.SENSOR_TYPE_TO_APP_ID.get(self.monnit_sensor_type)
        if app_id is None:
            logger.error(f"[{self.agent_id}] Unknown sensor type: {self.monnit_sensor_type}")
            return []
        
        logger.info(f"[{self.agent_id}] Fetching {self.monnit_sensor_type} sensors (ApplicationID: {app_id})...")
        
        # Use API filtering to get only the sensors we want
        sensor_list = self.get_sensor_list(application_id=app_id)
        
        if not sensor_list or 'Result' not in sensor_list:
            logger.error(f"[{self.agent_id}] Failed to get sensor list")
            return []
        
        all_sensors = sensor_list['Result']
        
        # Check if Result is an error message (string) instead of sensor list
        if isinstance(all_sensors, str):
            logger.error(f"[{self.agent_id}] API Error: {all_sensors}")
            return []
        
        # Ensure we have a list of sensors
        if not isinstance(all_sensors, list):
            logger.error(f"[{self.agent_id}] Unexpected response format. Expected list, got: {type(all_sensors)}")
            return []
        
        # Cache sensor info for later use
        for sensor in all_sensors:
            if isinstance(sensor, dict):
                sensor_id = sensor.get('SensorID')
                if sensor_id:
                    self.sensor_cache[sensor_id] = {
                        'name': sensor.get('SensorName', 'Unknown'),
                        'type': self.monnit_sensor_type,
                        'network_id': sensor.get('CSNetID'),
                        'app_id': sensor.get('ApplicationID')
                    }
        
        return all_sensors
    
    def initialize_sensor(self):
        """Initialize Monnit sensors by discovering them via API."""
        logger.info(f"[{self.agent_id}] Initializing Monnit {self.monnit_sensor_type} sensors...")
        
        # Get sensors of the specified type
        self.monnit_sensors = self.get_sensors_by_type()
        
        if not self.monnit_sensors:
            logger.error(f"[{self.agent_id}] No {self.monnit_sensor_type} sensors found")
            return
        
        logger.info(f"[{self.agent_id}] Found {len(self.monnit_sensors)} {self.monnit_sensor_type} sensors")
        
        # Log sensor names
        for i, sensor in enumerate(self.monnit_sensors, 1):
            sensor_id = sensor.get('SensorID')
            sensor_name = sensor.get('SensorName', 'Unknown')
            logger.info(f"[{self.agent_id}] [{i}] {sensor_name} (ID: {sensor_id})")
        
        # Signal that sensors are ready
        self.sensors_ready.set()
        logger.info(f"[{self.agent_id}] Monnit sensors initialization complete")
    
    def cleanup_sensor(self):
        """Clean up Monnit sensors (no hardware cleanup needed for API-based sensors)."""
        logger.info(f"[{self.agent_id}] Cleaning up Monnit sensors...")
        self.monnit_sensors = []
        self.sensor_cache = {}
        self.sensors_ready.clear()
    
    def _read_and_publish(self):
        """Override parent method to publish each Monnit sensor to its own topic."""
        try:
            # Wait for sensors to be ready (max 10 seconds)
            if not self.sensors_ready.wait(timeout=10.0):
                logger.warning(f"[{self.agent_id}] Sensors not ready - skipping read")
                return
            
            self.reading_count += 1
            
            # Read all sensors and publish each one separately
            for sensor in self.monnit_sensors:
                sensor_id = sensor.get('SensorID')
                sensor_name = sensor.get('SensorName', 'Unknown')
                
                if sensor_id:
                    # Get real-time data from Monnit API
                    sensor_data = self.get_single_sensor_data(sensor_id)
                    
                    if sensor_data and 'Result' in sensor_data:
                        result = sensor_data['Result']
                        
                        # Process the reading using the subclass implementation
                        value, unit, metadata = self.process_sensor_reading(result, sensor_name, sensor_id)
                        
                        if value is not None:
                            # Create a user-friendly sensor ID from the sensor name
                            # Convert "M01|MonnitMotionSensorAgent|712680" -> "motion_1" 
                            # Convert "S1B|MonnitActivitySensorAgent|505717" -> "activity_1"
                            friendly_sensor_id = self._create_friendly_sensor_id(sensor_name, sensor_id)
                            
                            # Create readSensor event for this specific sensor
                            event = EventFactory.create_sensor_event(
                                sensor_id=friendly_sensor_id,
                                sensor_type=self.sensor_type,
                                value=value,
                                unit=unit,
                                metadata=metadata
                            )
                            
                            # Publish to MQTT using the broadcast topic pattern for this specific sensor
                            topic = TopicManager.sensor_broadcast(friendly_sensor_id)
                            message = MQTTMessage.serialize(event)
                            self.mqtt_client.publish(topic, message, qos=1)
                            logger.info(f"[{self.agent_id}] Published {sensor_name} ({friendly_sensor_id}) reading #{self.reading_count}: {value} {unit} to topic: {topic}")
                    
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error reading/publishing sensor data: {e}")
    
    def _create_friendly_sensor_id(self, sensor_name: str, sensor_id: int) -> str:
        """Create a consistent user-friendly sensor ID that doesn't change between readings."""
        try:
            # Check if we already have a friendly ID for this sensor_id
            if sensor_id in self.sensor_id_mapping:
                return self.sensor_id_mapping[sensor_id]
            
            # Create a new friendly ID for this sensor (only happens once per sensor)
            self.friendly_id_counter += 1
            
            # Create simple sequential IDs that match dashboard agent expectations
            if self.monnit_sensor_type == "temperature":
                friendly_id = f"temperature_{self.friendly_id_counter}"
            elif self.monnit_sensor_type == "light":
                friendly_id = f"light_{self.friendly_id_counter}"
            elif self.monnit_sensor_type == "motion":
                # For motion sensors, try to extract number from name for consistency
                if "|" in sensor_name:
                    name_part = sensor_name.split("|")[0].strip()
                    if name_part.startswith("M") and len(name_part) >= 2:
                        number = name_part[1:]
                        if number.isdigit():
                            friendly_id = f"motion_{number}"
                        else:
                            friendly_id = f"motion_{name_part.lower()}"
                    else:
                        friendly_id = f"motion_{self.friendly_id_counter}"
                else:
                    friendly_id = f"motion_{self.friendly_id_counter}"
            elif self.monnit_sensor_type == "activity":
                # For activity sensors, extract S1B, S2A style names
                if "|" in sensor_name:
                    name_part = sensor_name.split("|")[0].strip()
                    if name_part.startswith("S"):
                        friendly_id = f"activity_{name_part.lower()}"
                    else:
                        friendly_id = f"activity_{self.friendly_id_counter}"
                else:
                    friendly_id = f"activity_{self.friendly_id_counter}"
            elif self.monnit_sensor_type == "door":
                # For door sensors, extract D01 style names
                if "|" in sensor_name:
                    name_part = sensor_name.split("|")[0].strip()
                    if name_part.startswith("D") and len(name_part) >= 2:
                        number = name_part[1:]
                        if number.isdigit():
                            friendly_id = f"door_{number}"
                        else:
                            friendly_id = f"door_{self.friendly_id_counter}"
                    else:
                        friendly_id = f"door_{self.friendly_id_counter}"
                else:
                    friendly_id = f"door_{self.friendly_id_counter}"
            else:
                # Default fallback for any sensor type
                friendly_id = f"{self.monnit_sensor_type}_{self.friendly_id_counter}"
            
            # Store the mapping so this sensor always gets the same friendly ID
            self.sensor_id_mapping[sensor_id] = friendly_id
            logger.info(f"[{self.agent_id}] Mapped sensor {sensor_id} ({sensor_name}) -> {friendly_id}")
            
            return friendly_id
            
        except Exception as e:
            logger.warning(f"[{self.agent_id}] Error creating friendly sensor ID for {sensor_name}: {e}")
            return f"{self.monnit_sensor_type}_{sensor_id}"
    
    def read_sensor(self) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """Read all Monnit sensors and return combined data."""
        # Wait for sensors to be ready
        if not self.sensors_ready.wait(timeout=10.0):
            logger.warning(f"[{self.agent_id}] Sensors not ready for read_sensor() - timeout")
            return None, None, None
        
        all_readings = {}
        failed_readings = {}
        any_success = False
        
        for sensor in self.monnit_sensors:
            sensor_id = sensor.get('SensorID')
            sensor_name = sensor.get('SensorName', 'Unknown')
            
            if sensor_id:
                # Get real-time data from Monnit API
                sensor_data = self.get_single_sensor_data(sensor_id)
                
                if sensor_data and 'Result' in sensor_data:
                    result = sensor_data['Result']
                    
                    # Process the reading using the subclass implementation
                    value, unit, metadata = self.process_sensor_reading(result, sensor_name, sensor_id)
                    
                    if value is not None:
                        # Create a user-friendly sensor ID
                        friendly_sensor_id = self._create_friendly_sensor_id(sensor_name, sensor_id)
                        
                        all_readings[friendly_sensor_id] = {
                            "value": value,
                            "unit": unit,
                            "metadata": metadata,
                            "name": sensor_name
                        }
                        any_success = True
                    else:
                        # Track failed processing
                        friendly_sensor_id = self._create_friendly_sensor_id(sensor_name, sensor_id)
                        failed_readings[friendly_sensor_id] = {
                            "name": sensor_name,
                            "reason": "processing_failed",
                            "raw_reading": result.get('CurrentReading', 'N/A')
                        }
                else:
                    # Track failed API calls
                    friendly_sensor_id = self._create_friendly_sensor_id(sensor_name, sensor_id)
                    failed_readings[friendly_sensor_id] = {
                        "name": sensor_name,
                        "reason": "api_failed",
                        "raw_reading": "N/A"
                    }
        
        if not any_success:
            return None, None, None
        
        # Create combined metadata with all sensor data
        combined_metadata = {
            "sensor_type": self.monnit_sensor_type,
            "network_id": self.network_id,
            "total_sensors": len(self.monnit_sensors),
            "successful_sensors": len(all_readings),
            "failed_sensors": len(failed_readings),
            "readings": all_readings,
            "failed_readings": failed_readings
        }
        
        # For compatibility, return the first successful reading as the primary value
        primary_value = None
        primary_unit = None
        for sensor_key, reading_data in all_readings.items():
            primary_value = reading_data["value"]
            primary_unit = reading_data["unit"]
            break
        
        return primary_value, primary_unit, combined_metadata
    
    @abstractmethod
    def process_sensor_reading(self, result: Dict, sensor_name: str, sensor_id: int) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """
        Process a single sensor reading from the Monnit API result.
        
        Parameters:
        - result: The 'Result' field from the Monnit API response
        - sensor_name: The name of the sensor
        - sensor_id: The ID of the sensor
        
        Returns:
        - tuple: (processed_value, unit, metadata)
        """
        pass 