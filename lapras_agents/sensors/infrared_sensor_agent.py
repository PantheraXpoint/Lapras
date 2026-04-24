import logging
import time
from typing import Optional, Any, Dict, List
import threading 
from Phidget22.PhidgetException import PhidgetException, ErrorCode
from Phidget22.Devices.VoltageRatioInput import VoltageRatioInput
from Phidget22.VoltageRatioSensorType import VoltageRatioSensorType
from Phidget22.Devices.Manager import Manager
from Phidget22.Phidget import Phidget

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lapras_middleware.sensor_agent import SensorAgent
from lapras_middleware.event import EventFactory, MQTTMessage, TopicManager

logger = logging.getLogger(__name__)

class InfraredSensorAgent(SensorAgent):
    """Infrared distance sensor agent using Phidget sensor with multiple channels."""

    def __init__(self, sensor_id: str = "infrared", virtual_agent_id: str = "aircon", channel: int = 1,
                 mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        
        # Thread-safe initialization flag
        self.hardware_ready = threading.Event()  # Starts as "not set"
        
        # Phidget sensors for all channels
        self.ir_sensors: Dict[int, Optional[VoltageRatioInput]] = {}
        self.sensor_initialized: Dict[int, bool] = {}
        self.channels = []  # Will be populated during initialization
        
        # Sensor configuration (Sharp 2Y0A02 - 20-150cm)
        self.SENSOR_MODEL_NAME = "Sharp 2Y0A02 (20-150cm)"
        self.SENSOR_MIN_CM = 20.0
        self.SENSOR_MAX_CM = 150.0
        self.FORMULA_VALID_SV_MIN = 80.0
        self.FORMULA_VALID_SV_MAX = 490.0
        self.K_DISTANCE = 9462.0
        self.SV_OFFSET = 16.92
        self.PROXIMITY_THRESHOLD_CM = 149.0
        
        # Device parameters
        self.DEVICE_SERIAL_NUMBER = 455869  # run this command to see the serial number "phidget22admin -d"
        self.TARGET_CHANNEL = channel
        self.OPEN_TIMEOUT_MS = 5000

        # Rate limiting for warnings per channel
        self._last_warn_times: Dict[int, Dict[str, float]] = {}

        # Initialize the base SensorAgent class with the sensor ID
        super().__init__(sensor_id, "infrared", virtual_agent_id, mqtt_broker, mqtt_port)

        self.set_reading_interval(1.0)

        # Initialize sensor hardware after everything is set up
        # This will set self.hardware_ready when complete
        self.start_sensor()

    def discover_channels(self) -> List[int]:
        """Actively probe channels 0-7 to find available channels on the Phidget device."""
        logger.info(f"[{self.agent_id}] Probing channels 0-7 for device {self.DEVICE_SERIAL_NUMBER}...")
        available_channels = []
        
        # First try to get the device info to verify it exists
        try:
            manager = Manager()
            manager.open()
            devices = manager.getAttachedDevices()
            device_found = False
            for device in devices:
                if device.getDeviceSerialNumber() == self.DEVICE_SERIAL_NUMBER:
                    device_found = True
                    break
            manager.close()
            
            if not device_found:
                logger.error(f"[{self.agent_id}] Device with serial number {self.DEVICE_SERIAL_NUMBER} not found")
                return []
                
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error checking device existence: {str(e)}")
            # Continue with channel probing even if device check fails
            logger.info(f"[{self.agent_id}] Continuing with channel probing despite device check error")

        # Now probe each channel
        for ch in range(8):  # Most Phidget boards have up to 8 channels
            sensor = None
            try:
                sensor = VoltageRatioInput()
                sensor.setDeviceSerialNumber(self.DEVICE_SERIAL_NUMBER)
                sensor.setChannel(ch)
                sensor.setIsHubPortDevice(False)
                
                # Try to open with a shorter timeout
                sensor.openWaitForAttachment(1000)  # 1 second timeout per channel
                
                if sensor.getAttached():
                    # Verify we can actually read from the sensor
                    try:
                        # Try to get a reading to verify the sensor is working
                        sensor.getVoltageRatio()
                        available_channels.append(ch)
                        logger.info(f"[{self.agent_id}] Channel {ch} attached and verified working.")
                    except PhidgetException as e:
                        logger.warning(f"[{self.agent_id}] Channel {ch} attached but failed to read: {e.code} - {e.details}")
                else:
                    logger.debug(f"[{self.agent_id}] Channel {ch} not attached")
                    
            except PhidgetException as e:
                if e.code != ErrorCode.EPHIDGET_TIMEOUT:
                    logger.warning(f"[{self.agent_id}] Channel {ch} error: {e.code} - {e.details}")
            except Exception as ex:
                logger.warning(f"[{self.agent_id}] Channel {ch} generic error: {str(ex)}")
            finally:
                if sensor:
                    try:
                        sensor.close()
                    except:
                        pass

        if not available_channels:
            logger.warning(f"[{self.agent_id}] No active channels found for device {self.DEVICE_SERIAL_NUMBER}")
        else:
            logger.info(f"[{self.agent_id}] Discovered {len(available_channels)} active channels: {available_channels}")
        return available_channels

    def initialize_sensor(self):
        """Initialize the Phidget infrared sensors for all discovered channels."""
        # First discover available channels
        self.channels = self.discover_channels()
        
        if not self.channels:
            logger.error(f"[{self.agent_id}] No channels available for initialization")
            # Instead of raising an exception, set hardware_ready and return
            # This allows the agent to continue running even if no sensors are found
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

        logger.info(f"[{self.agent_id}] Initializing infrared sensors for channels {self.channels}...")

        # Clear any existing sensors
        for ch in self.channels:
            self.ir_sensors[ch] = None
            self.sensor_initialized[ch] = False

        try:
            sensors = []
            for channel in self.channels:
                logger.info(f"[{self.agent_id}] Creating VoltageRatioInput object for channel {channel}")
                sensor = VoltageRatioInput()

                logger.info(f"[{self.agent_id}] Setting DeviceSerialNumber to {self.DEVICE_SERIAL_NUMBER} for channel {channel}")
                sensor.setDeviceSerialNumber(self.DEVICE_SERIAL_NUMBER)

                logger.info(f"[{self.agent_id}] Setting Channel to {channel}")
                sensor.setChannel(channel)

                logger.info(f"[{self.agent_id}] Setting setIsHubPortDevice(False) for channel {channel}")
                sensor.setIsHubPortDevice(False)

                sensors.append((channel, sensor))

            # Open all sensors
            logger.info(f"[{self.agent_id}] Opening all sensors...")
            for channel, sensor in sensors:
                logger.info(f"[{self.agent_id}] Calling openWaitForAttachment({self.OPEN_TIMEOUT_MS}ms) for channel {channel}...")
                sensor.openWaitForAttachment(self.OPEN_TIMEOUT_MS)

            # Check attachment status and configure
            all_attached = True
            for channel, sensor in sensors:
                if sensor.getAttached():
                    logger.info(f"[{self.agent_id}] SUCCESS! Channel {channel} Phidget ATTACHED. SN: {sensor.getDeviceSerialNumber()}")
                    try:
                        logger.info(f"[{self.agent_id}] Setting SensorType to VOLTAGERATIO for channel {channel}")
                        sensor.setSensorType(VoltageRatioSensorType.SENSOR_TYPE_VOLTAGERATIO)
                        logger.info(f"[{self.agent_id}] Setting DataInterval to 200ms for channel {channel}")
                        sensor.setDataInterval(30)

                        self.ir_sensors[channel] = sensor
                        self.sensor_initialized[channel] = True
                        logger.info(f"[{self.agent_id}] Channel {channel} initialization successful")

                    except PhidgetException as pe_post_attach:
                        logger.error(f"[{self.agent_id}] PhidgetException during post-attach config for channel {channel}: {pe_post_attach.code} - {pe_post_attach.details}")
                        sensor.close()
                        all_attached = False
                else:
                    logger.error(f"[{self.agent_id}] FAILED. Channel {channel} openWaitForAttachment completed, but Phidget is NOT attached")
                    sensor.close()
                    all_attached = False

            if all_attached:
                logger.info(f"[{self.agent_id}] All infrared sensors initialization successful")
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
                    self.ir_sensors[channel] = None
                    self.sensor_initialized[channel] = False
                raise PhidgetException(ErrorCode.EPHIDGET_TIMEOUT)

        except PhidgetException as e:
            logger.error(f"[{self.agent_id}] PhidgetException in initialize_sensor: {e.code} ({hex(e.code)}) - {e.details}")
            # Clean up
            for channel in self.channels:
                if channel in self.ir_sensors and self.ir_sensors[channel]:
                    try:
                        self.ir_sensors[channel].close()
                    except:
                        pass
                    self.ir_sensors[channel] = None
                    self.sensor_initialized[channel] = False
            raise
        except Exception as ex:
            logger.error(f"[{self.agent_id}] Generic Exception in initialize_sensor: {str(ex)}")
            # Clean up
            for channel in self.channels:
                if channel in self.ir_sensors and self.ir_sensors[channel]:
                    try:
                        self.ir_sensors[channel].close()
                    except:
                        pass
                    self.ir_sensors[channel] = None
                    self.sensor_initialized[channel] = False
            raise

    def cleanup_sensor(self):
        """Clean up all Phidget infrared sensors."""
        logger.info(f"[{self.agent_id}] Cleaning up infrared sensors...")

        for channel in self.channels:
            if channel in self.ir_sensors and self.ir_sensors[channel]:
                is_attached_before_close = False
                try:
                    is_attached_before_close = self.ir_sensors[channel].getAttached()
                except:
                    pass

                logger.info(f"[{self.agent_id}] Attempting to close channel {channel} sensor. Was attached: {is_attached_before_close}")
                try:
                    self.ir_sensors[channel].close()
                    logger.info(f"[{self.agent_id}] Channel {channel} sensor close() called")
                except PhidgetException as e:
                    logger.error(f"[{self.agent_id}] Error closing channel {channel} sensor: {e.code} - {e.details}")

                self.ir_sensors[channel] = None

            self.sensor_initialized[channel] = False

        logger.info(f"[{self.agent_id}] Infrared sensors cleanup complete")

    def read_sensor_channel(self, channel: int) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """Read a specific sensor channel and calculate distance."""
        # First check if hardware is ready (non-blocking check)
        if not self.hardware_ready.is_set():
            # Hardware not ready yet, return None without logging spam
            return None, None, None
            
        if channel not in self.channels:
            current_time = time.time()
            if current_time - self._last_warn_times.get(channel, {}).get('init_fail', 0) > 5:
                logger.warning(f"[{self.agent_id}] Channel {channel} not in available channels {self.channels}; skipping read")
                if channel not in self._last_warn_times:
                    self._last_warn_times[channel] = {}
                self._last_warn_times[channel]['init_fail'] = current_time
            return None, None, None
            
        if not self.sensor_initialized.get(channel, False):
            current_time = time.time()
            if current_time - self._last_warn_times[channel]['init_fail'] > 5:
                logger.warning(f"[{self.agent_id}] Channel {channel} sensor not initialized; skipping read")
                self._last_warn_times[channel]['init_fail'] = current_time
            return None, None, None

        sensor = self.ir_sensors.get(channel)
        if not sensor:
            current_time = time.time()
            if current_time - self._last_warn_times[channel]['init_fail'] > 5:
                logger.warning(f"[{self.agent_id}] Channel {channel} sensor object not found; skipping read")
                self._last_warn_times[channel]['init_fail'] = current_time
            return None, None, None

        try:
            voltage_ratio = sensor.getVoltageRatio()
            sensor_value_for_formula = voltage_ratio * 1000

            calculated_cm = self.SENSOR_MAX_CM  # Default to max distance

            if sensor_value_for_formula >= self.FORMULA_VALID_SV_MIN and sensor_value_for_formula <= self.FORMULA_VALID_SV_MAX:
                denominator = sensor_value_for_formula - self.SV_OFFSET
                if denominator > 0.01:
                    distance_from_formula = self.K_DISTANCE / denominator
                    calculated_cm = max(self.SENSOR_MIN_CM, min(self.SENSOR_MAX_CM, distance_from_formula))
                else:
                    calculated_cm = self.SENSOR_MIN_CM
            elif sensor_value_for_formula < self.FORMULA_VALID_SV_MIN:
                calculated_cm = self.SENSOR_MAX_CM
            elif sensor_value_for_formula > self.FORMULA_VALID_SV_MAX:
                calculated_cm = self.SENSOR_MIN_CM

            # Determine proximity status
            proximity_status = "near" if calculated_cm < self.PROXIMITY_THRESHOLD_CM else "far"

            # Prepare metadata
            metadata = {
                "proximity_status": proximity_status,
                "raw_voltage_ratio": voltage_ratio,
                "sensor_value": sensor_value_for_formula,
                "sensor_model": self.SENSOR_MODEL_NAME,
                "threshold_cm": self.PROXIMITY_THRESHOLD_CM,
                "channel": channel
            }

            return round(calculated_cm, 2), "cm", metadata

        except PhidgetException as e:
            current_time = time.time()
            if e.code == ErrorCode.EPHIDGET_UNKNOWNVAL:
                if current_time - self._last_warn_times[channel]['unknown_val'] > 5:
                    logger.warning(f"[{self.agent_id}] Channel {channel} sensor value unknown (EPHIDGET_UNKNOWNVAL)")
                    self._last_warn_times[channel]['unknown_val'] = current_time
            elif e.code == ErrorCode.EPHIDGET_NOTATTACHED:
                logger.error(f"[{self.agent_id}] Channel {channel} PhidgetException: Sensor not attached during read. {e.details}")
                self.sensor_initialized[channel] = False
            else:
                logger.error(f"[{self.agent_id}] Channel {channel} PhidgetException reading sensor: {e.code} ({hex(e.code)}) - {e.details}")
                self.sensor_initialized[channel] = False
            return None, None, None
        except Exception as e:
            logger.error(f"[{self.agent_id}] Channel {channel} generic error reading sensor: {type(e).__name__} - {str(e)}")
            self.sensor_initialized[channel] = False
            return None, None, None

    def read_sensor(self) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """Read all infrared sensor channels and return combined data."""
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
                    # No target field needed - routing handled by MQTT topic
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