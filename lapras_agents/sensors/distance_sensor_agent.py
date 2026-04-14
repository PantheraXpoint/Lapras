#!/usr/bin/env python3
"""
Distance Sensor Agent using Phidget DistanceSensor (newer sensors)
Follows the same pattern as InfraredSensorAgent but uses different hardware library.
Outputs compatible proximity_status format for existing rules.
"""

import logging
import time
from typing import Optional, Any, Dict, List
import threading 
from Phidget22.PhidgetException import PhidgetException, ErrorCode
from Phidget22.Devices.DistanceSensor import DistanceSensor
from Phidget22.Devices.Manager import Manager
from Phidget22.Phidget import Phidget

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lapras_middleware.sensor_agent import SensorAgent
from lapras_middleware.event import EventFactory, MQTTMessage, TopicManager

logger = logging.getLogger(__name__)

class DistanceSensorAgent(SensorAgent):
    """Distance sensor agent using Phidget DistanceSensor with multiple channels."""

    def __init__(self, sensor_id: str = "distance", virtual_agent_id: str = "aircon", channel: int = 0,
                 mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        
        # Thread-safe initialization flag
        self.hardware_ready = threading.Event()  # Starts as "not set"
        
        # Phidget sensors for all channels
        self.distance_sensors: Dict[int, Optional[DistanceSensor]] = {}
        self.sensor_initialized: Dict[int, bool] = {}
        self.channels = []  # Will be populated during initialization
        
        # Sensor configuration (compatible with infrared sensor format)
        self.SENSOR_MODEL_NAME = "Distance Phidget 1300mm"
        self.SENSOR_MIN_CM = 2.0   # 20mm minimum range
        self.SENSOR_MAX_CM = 130.0  # 1300mm maximum range (130cm)
        self.PROXIMITY_THRESHOLD_CM = 129.0  # Compatible with infrared threshold (149cm adjusted for 130cm max)
        
        # Device parameters
        self.HUB_SERIAL_NUMBER = 745871  # Your hub serial number
        self.HUB_PORTS = [0, 1, 2, 3, 4, 5]  # All available hub ports to scan
        self.DATA_INTERVAL_MS = 30  # Data reading interval in milliseconds (must be 30-60000)
        self.OPEN_TIMEOUT_MS = 3000  # Timeout for sensor attachment

        # Rate limiting for warnings per channel
        self._last_warn_times: Dict[int, Dict[str, float]] = {}

        # Initialize the base SensorAgent class with the sensor ID
        super().__init__(sensor_id, "distance", virtual_agent_id, mqtt_broker, mqtt_port)

        self.set_reading_interval(1.0)

        # Initialize sensor hardware after everything is set up
        self.start_sensor()

    def discover_channels(self) -> List[int]:
        """Discover all connected distance sensors on VINT hub ports."""
        logger.info(f"[{self.agent_id}] Discovering distance sensors on hub {self.HUB_SERIAL_NUMBER}...")
        discovered_ports = []
        
        # Try each hub port for distance sensors
        for hub_port in self.HUB_PORTS:
            sensor = None
            try:
                sensor = DistanceSensor()
                
                # For VINT distance sensors, connect via hub port
                sensor.setDeviceSerialNumber(self.HUB_SERIAL_NUMBER)
                sensor.setHubPort(hub_port)
                sensor.setChannel(0)
                sensor.setIsHubPortDevice(False)  # VINT devices are not hub port devices
                
                # Try to open with timeout
                sensor.openWaitForAttachment(self.OPEN_TIMEOUT_MS)
                
                if sensor.getAttached():
                    logger.info(f"[{self.agent_id}] ✓ Found Distance Sensor on Hub Port {hub_port}")
                    
                    # Get sensor details
                    try:
                        device_name = sensor.getDeviceName()
                        device_serial = sensor.getDeviceSerialNumber()
                        logger.info(f"[{self.agent_id}]   Device: {device_name} (Serial: {device_serial})")
                    except:
                        logger.info(f"[{self.agent_id}]   Device: Could not get device details")
                    
                    discovered_ports.append(hub_port)
                    
                    # Quick sensor initialization check (don't configure yet)
                    try:
                        test_distance = sensor.getDistance()
                        logger.info(f"[{self.agent_id}]   Initial reading: {test_distance/10:.1f} cm")
                    except:
                        logger.info(f"[{self.agent_id}]   Sensor on port {hub_port} will initialize during streaming")
                    
                    # Close for now - will reopen during initialization
                    sensor.close()
                else:
                    sensor.close()
                    logger.debug(f"[{self.agent_id}]   Hub Port {hub_port}: No sensor attached")
                    
            except PhidgetException as e:
                if sensor:
                    try:
                        sensor.close()
                    except:
                        pass
                logger.debug(f"[{self.agent_id}]   Hub Port {hub_port}: No distance sensor ({e.details})")
            except Exception as e:
                if sensor:
                    try:
                        sensor.close()
                    except:
                        pass
                logger.debug(f"[{self.agent_id}]   Hub Port {hub_port}: Unexpected error - {str(e)}")
                        
        logger.info(f"[{self.agent_id}] Discovery complete. Found sensors on hub ports: {discovered_ports}")
        return discovered_ports

    def initialize_sensor(self):
        """Initialize the Phidget distance sensors for all discovered channels."""
        # First discover available channels
        self.channels = self.discover_channels()
        
        if not self.channels:
            logger.error(f"[{self.agent_id}] No channels available for initialization")
            # Set hardware_ready to allow agent to continue running
            self.hardware_ready.set()
            return
            
        # Create sensor IDs ONLY for channels that have sensors
        self.sensor_ids = [f"{self.agent_id}_{i+1}" for i in range(len(self.channels))]
        
        # Create a mapping of channels to sensor IDs ONLY for channels with sensors
        self.channel_to_sensor_id = dict(zip(self.channels, self.sensor_ids))
        
        # Initialize warning times for channels with sensors
        for ch in self.channels:
            self.sensor_initialized[ch] = False
            self._last_warn_times[ch] = {
                'init_fail': 0,
                'attach_fail': 0,
                'unknown_val': 0
            }

        logger.info(f"[{self.agent_id}] Initializing distance sensors for channels {self.channels}...")

        # Clear any existing sensors
        for ch in self.channels:
            self.distance_sensors[ch] = None
            self.sensor_initialized[ch] = False

        try:
            sensors = []
            for channel in self.channels:
                logger.info(f"[{self.agent_id}] Creating DistanceSensor object for hub port {channel}")
                sensor = DistanceSensor()

                logger.info(f"[{self.agent_id}] Setting DeviceSerialNumber to {self.HUB_SERIAL_NUMBER} for hub port {channel}")
                sensor.setDeviceSerialNumber(self.HUB_SERIAL_NUMBER)

                logger.info(f"[{self.agent_id}] Setting HubPort to {channel}")
                sensor.setHubPort(channel)
                sensor.setChannel(0)  # Always channel 0 for VINT distance sensors

                logger.info(f"[{self.agent_id}] Setting setIsHubPortDevice(False) for hub port {channel}")
                sensor.setIsHubPortDevice(False)

                sensors.append((channel, sensor))

            # Open all sensors
            logger.info(f"[{self.agent_id}] Opening all sensors...")
            for channel, sensor in sensors:
                logger.info(f"[{self.agent_id}] Calling openWaitForAttachment({self.OPEN_TIMEOUT_MS}ms) for hub port {channel}...")
                sensor.openWaitForAttachment(self.OPEN_TIMEOUT_MS)

            # Check attachment status and configure
            all_attached = True
            for channel, sensor in sensors:
                if sensor.getAttached():
                    logger.info(f"[{self.agent_id}] SUCCESS! Hub Port {channel} Distance Sensor ATTACHED. SN: {sensor.getDeviceSerialNumber()}")
                    try:
                        logger.info(f"[{self.agent_id}] Setting DataInterval to {self.DATA_INTERVAL_MS}ms for hub port {channel}")
                        sensor.setDataInterval(self.DATA_INTERVAL_MS)
                        sensor.setDistanceChangeTrigger(0)  # Continuous readings

                        self.distance_sensors[channel] = sensor
                        self.sensor_initialized[channel] = True
                        logger.info(f"[{self.agent_id}] Hub Port {channel} initialization successful")

                    except PhidgetException as pe_post_attach:
                        logger.error(f"[{self.agent_id}] PhidgetException during post-attach config for hub port {channel}: {pe_post_attach.code} - {pe_post_attach.details}")
                        sensor.close()
                        all_attached = False
                else:
                    logger.error(f"[{self.agent_id}] FAILED. Hub Port {channel} openWaitForAttachment completed, but Distance Sensor is NOT attached")
                    sensor.close()
                    all_attached = False

            if all_attached:
                logger.info(f"[{self.agent_id}] All distance sensors initialization successful")
                # Signal that hardware is ready
                self.hardware_ready.set()
                logger.info(f"[{self.agent_id}] Hardware ready signal set - threads can now proceed")
            else:
                logger.error(f"[{self.agent_id}] Some sensors failed to initialize")
                # Clean up any successfully opened sensors
                for channel, sensor in sensors:
                    try:
                        sensor.close()
                    except:
                        pass
                    self.distance_sensors[channel] = None
                    self.sensor_initialized[channel] = False
                raise PhidgetException(ErrorCode.EPHIDGET_TIMEOUT)

        except PhidgetException as e:
            logger.error(f"[{self.agent_id}] PhidgetException in initialize_sensor: {e.code} ({hex(e.code)}) - {e.details}")
            # Clean up
            for channel in self.channels:
                if channel in self.distance_sensors and self.distance_sensors[channel]:
                    try:
                        self.distance_sensors[channel].close()
                    except:
                        pass
                    self.distance_sensors[channel] = None
                    self.sensor_initialized[channel] = False
            raise
        except Exception as ex:
            logger.error(f"[{self.agent_id}] Generic Exception in initialize_sensor: {str(ex)}")
            # Clean up
            for channel in self.channels:
                if channel in self.distance_sensors and self.distance_sensors[channel]:
                    try:
                        self.distance_sensors[channel].close()
                    except:
                        pass
                    self.distance_sensors[channel] = None
                    self.sensor_initialized[channel] = False
            raise

    def cleanup_sensor(self):
        """Clean up all Phidget distance sensors."""
        logger.info(f"[{self.agent_id}] Cleaning up distance sensors...")

        for channel in self.channels:
            if channel in self.distance_sensors and self.distance_sensors[channel]:
                is_attached_before_close = False
                try:
                    is_attached_before_close = self.distance_sensors[channel].getAttached()
                except:
                    pass

                logger.info(f"[{self.agent_id}] Attempting to close hub port {channel} sensor. Was attached: {is_attached_before_close}")
                try:
                    self.distance_sensors[channel].close()
                    logger.info(f"[{self.agent_id}] Hub Port {channel} sensor close() called")
                except PhidgetException as e:
                    logger.error(f"[{self.agent_id}] Error closing hub port {channel} sensor: {e.code} - {e.details}")

                self.distance_sensors[channel] = None

            self.sensor_initialized[channel] = False

        logger.info(f"[{self.agent_id}] Distance sensors cleanup complete")

    def read_sensor_channel(self, channel: int) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """Read a specific sensor channel and calculate distance."""
        # First check if hardware is ready (non-blocking check)
        if not self.hardware_ready.is_set():
            # Hardware not ready yet, return None without logging spam
            return None, None, None
            
        if channel not in self.channels:
            current_time = time.time()
            if current_time - self._last_warn_times.get(channel, {}).get('init_fail', 0) > 5:
                logger.warning(f"[{self.agent_id}] Hub Port {channel} not in available channels {self.channels}; skipping read")
                if channel not in self._last_warn_times:
                    self._last_warn_times[channel] = {}
                self._last_warn_times[channel]['init_fail'] = current_time
            return None, None, None
            
        if not self.sensor_initialized.get(channel, False):
            current_time = time.time()
            if current_time - self._last_warn_times[channel]['init_fail'] > 5:
                logger.warning(f"[{self.agent_id}] Hub Port {channel} sensor not initialized; skipping read")
                self._last_warn_times[channel]['init_fail'] = current_time
            return None, None, None

        sensor = self.distance_sensors.get(channel)
        if not sensor:
            current_time = time.time()
            if current_time - self._last_warn_times[channel]['init_fail'] > 5:
                logger.warning(f"[{self.agent_id}] Hub Port {channel} sensor object not found; skipping read")
                self._last_warn_times[channel]['init_fail'] = current_time
            return None, None, None

        try:
            # Get distance in mm from the sensor
            distance_mm = sensor.getDistance()
            
            # Convert to cm for consistency with infrared sensors
            distance_cm = distance_mm / 10.0
            
            # Handle out-of-range values (like in test_distance_sensor.py)
            if distance_mm < 0 or distance_mm > 1300:
                distance_cm = self.SENSOR_MAX_CM  # Use max range when out of range
            
            # Clamp to sensor range
            calculated_cm = max(self.SENSOR_MIN_CM, min(self.SENSOR_MAX_CM, distance_cm))

            # Determine proximity status (compatible with infrared sensor format)
            proximity_status = "near" if calculated_cm < self.PROXIMITY_THRESHOLD_CM else "far"

            # Prepare metadata (compatible with infrared sensor format)
            metadata = {
                "proximity_status": proximity_status,
                "raw_distance_mm": distance_mm,
                "distance_cm": calculated_cm,
                "sensor_model": self.SENSOR_MODEL_NAME,
                "threshold_cm": self.PROXIMITY_THRESHOLD_CM,
                "channel": channel,
                "hub_port": channel  # For clarity
            }

            return round(calculated_cm, 2), "cm", metadata

        except PhidgetException as e:
            current_time = time.time()
            if e.code == ErrorCode.EPHIDGET_UNKNOWNVAL:
                if current_time - self._last_warn_times[channel]['unknown_val'] > 5:
                    logger.warning(f"[{self.agent_id}] Hub Port {channel} sensor value unknown (EPHIDGET_UNKNOWNVAL)")
                    self._last_warn_times[channel]['unknown_val'] = current_time
                # Return max distance when sensor is initializing (like test_distance_sensor.py)
                return self.SENSOR_MAX_CM, "cm", {
                    "proximity_status": "far",
                    "distance_cm": self.SENSOR_MAX_CM,
                    "sensor_model": self.SENSOR_MODEL_NAME,
                    "threshold_cm": self.PROXIMITY_THRESHOLD_CM,
                    "channel": channel,
                    "status": "initializing"
                }
            elif e.code == ErrorCode.EPHIDGET_NOTATTACHED:
                logger.error(f"[{self.agent_id}] Hub Port {channel} PhidgetException: Sensor not attached during read. {e.details}")
                self.sensor_initialized[channel] = False
            else:
                logger.error(f"[{self.agent_id}] Hub Port {channel} PhidgetException reading sensor: {e.code} ({hex(e.code)}) - {e.details}")
                self.sensor_initialized[channel] = False
            return None, None, None
        except Exception as e:
            logger.error(f"[{self.agent_id}] Hub Port {channel} generic error reading sensor: {type(e).__name__} - {str(e)}")
            self.sensor_initialized[channel] = False
            return None, None, None

    def read_sensor(self) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """Read all distance sensor channels and return combined data."""
        # Wait for hardware to be ready before reading
        if not self.hardware_ready.wait(timeout=10.0):
            logger.warning(f"[{self.agent_id}] Hardware not ready for read_sensor() - timeout")
            return None, None, None
            
        all_readings = {}
        any_success = False

        for channel in self.channels:
            value, unit, metadata = self.read_sensor_channel(channel)
            if value is not None:
                all_readings[f"channel_{channel}"] = {
                    "value": value,
                    "unit": unit,
                    "metadata": metadata
                }
                any_success = True

        if not any_success:
            return None, None, None

        # Create combined metadata with all channel data
        combined_metadata = {
            "sensor_model": self.SENSOR_MODEL_NAME,
            "threshold_cm": self.PROXIMITY_THRESHOLD_CM,
            "channels": self.channels,
            "readings": all_readings
        }

        # For compatibility, return the first successful reading as the primary value
        # but include all data in metadata
        primary_channel = None
        primary_value = None
        for channel in self.channels:
            if f"channel_{channel}" in all_readings:
                primary_channel = channel
                primary_value = all_readings[f"channel_{channel}"]["value"]
                break

        combined_metadata["primary_channel"] = primary_channel

        return primary_value, "cm", combined_metadata

    def _read_and_publish(self):
        """Override parent method to publish each channel to its own topic."""
        try:
            # Wait for hardware to be ready (max 10 seconds)
            if not self.hardware_ready.wait(timeout=10.0):
                logger.warning(f"[{self.agent_id}] Hardware initialization timeout - skipping read")
                return
            
            self.reading_count += 1
            # Read and publish ONLY for channels that have sensors
            for channel in self.channels:
                value, unit, metadata = self.read_sensor_channel(channel)
                
                if value is not None:  # Only publish if we got a valid reading
                    sensor_id = self.channel_to_sensor_id[channel]
                    
                    # Create readSensor event for this specific channel
                    event = EventFactory.create_sensor_event(
                        sensor_id=sensor_id,
                        sensor_type=self.sensor_type,
                        value=value,
                        unit=unit,
                        metadata=metadata
                    )
                    
                    # Publish to MQTT using the broadcast topic pattern for this specific sensor ID
                    topic = TopicManager.sensor_broadcast(sensor_id)
                    message = MQTTMessage.serialize(event)
                    self.mqtt_client.publish(topic, message, qos=1)
                    logger.info(f"[{self.agent_id}] Published {sensor_id} reading #{self.reading_count}: {value} {unit} to topic: {topic}")
                    
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error reading/publishing sensor data: {e}") 