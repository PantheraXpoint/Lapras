import logging
import time
import sys
import os
import urllib.request
import json
import subprocess
from collections import defaultdict

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lapras_middleware.virtual_agent import VirtualAgent
from lapras_middleware.event import EventFactory, MQTTMessage, TopicManager, SensorPayload, ActionPayload, ActionReportPayload, ThresholdConfigPayload

logger = logging.getLogger(__name__)

class ClubHouseAgent(VirtualAgent):
    def __init__(self, agent_id: str = None, mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883, 
                 sensor_config: dict = None, transmission_interval: float = 0.5,
                 preset_mode: str = "all-normal"):
        
        # Initialize attributes that might be accessed by perception thread first
        self.transmission_interval = transmission_interval
        self.last_transmission_time = 0.0
        self.pending_state_update = False
        
        # Store preset mode first to determine agent_id
        self.preset_mode = preset_mode
        
        # Determine agent_id based on preset mode if not explicitly provided
        if agent_id is None:
            if preset_mode in ["back-read", "back-nap"]:
                agent_id = "back"
            elif preset_mode in ["front-read", "front-nap"]:
                agent_id = "front"
            elif preset_mode in ["all-normal", "all-clean"]:
                agent_id = "all"
            else:
                agent_id = "event_venue"  # fallback
        
        super().__init__(agent_id, "event_venue", mqtt_broker, mqtt_port)
        
        logger.info(f"[{self.agent_id}] ClubHouseAgent initialized with preset: {preset_mode}")
        
        # Default sensor configuration if none provided
        if sensor_config is None:
            if preset_mode in ["back-read", "back-nap"]:
                sensor_config = {
                    "infrared": ["infrared_1", "infrared_2"],
                    "distance": ["distance_1", "distance_2"]
                }
            elif preset_mode in ["front-read", "front-nap"]:
                sensor_config = {
                    "infrared": ["infrared_3", "infrared_4"],
                    "distance": ["distance_3", "distance_4"]
                }
            elif preset_mode in ["all-normal", "all-clean"]:
                sensor_config = {
                    "infrared": ["infrared_1", "infrared_2", "infrared_3", "infrared_4"],
                    "distance": ["distance_1", "distance_2", "distance_3", "distance_4"]
                }
            else:
                # Default fallback
                sensor_config = {
                    "infrared": ["infrared_1", "infrared_2"],
                    "distance": ["distance_1", "distance_2"]
                }
        
        self.sensor_config = sensor_config
        self.supported_sensor_types = ["infrared", "distance", "motion", "activity", "light", "temperature", "door"]

        # Dynamic threshold configuration for sensor classification
        self.light_threshold_config = {
            "threshold": 1000.0,  # lux; above = bright, below = dark
            "last_update": time.time()
        }
        self.temperature_threshold_config = {
            "threshold": 25.0,  # °C; above = hot, below = cool
            "last_update": time.time()
        }
        
        # Hue Bridge configuration
        self.BRIDGE_IP = "143.248.55.137:10090"
        self.USERNAME = "P2laHGvjzthn7Ip5-fAAIbVB9ulu9OlHWk8L7Yex"
        self.BASE_URL = f"http://{self.BRIDGE_IP}/api/{self.USERNAME}"
        
        # IR controller script path
        self.ir_script_path = "lapras_agents/utils/test_ir_controller.py"
        
        # Configure preset-specific settings
        self._configure_preset_settings()
        
        # Test both light and IR controller availability
        self._test_light_controller()
        self._test_ir_controller()
        
        # Initialize local state
        self.local_state.update({
            "power": "off",
            "proximity_status": "unknown",
            "infrared_1_proximity_status": "unknown",
            "motion_status": "unknown",
            "activity_status": "unknown",
            "activity_detected": False,
            "light_status": "unknown",
            "temperature_status": "unknown",
            "door_status": "unknown",
            "light_power": "off",
            "aircon_power": "off",
            "light_threshold": self.light_threshold_config["threshold"],
            "temp_threshold": self.temperature_threshold_config["threshold"],
            **self._build_mode_state_fields(),
        })
        
        self.sensor_data = defaultdict(dict)
        
        # Configure sensors based on configuration
        self._configure_sensors()
        
        logger.info(f"[{self.agent_id}] ClubHouseAgent initialization completed with sensors: {sensor_config}, transmission interval: {transmission_interval}s, preset: {preset_mode}")

        # Publish initial state to context manager
        self._trigger_initial_state_publication()
    
    def _configure_preset_settings(self):
        """Configure settings based on preset mode."""
        # Extract position and mode from preset_mode
        if "back" in self.preset_mode:
            position = "back"
        elif "front" in self.preset_mode:
            position = "front"
        elif "all" in self.preset_mode:
            position = "all"
        else:
            position = "back"  # default fallback
            
        if "nap" in self.preset_mode:
            mode = "nap"
        elif "read" in self.preset_mode:
            mode = "read"
        elif "normal" in self.preset_mode:
            mode = "normal"
        elif "clean" in self.preset_mode:
            mode = "clean"
        elif "infrared" in self.preset_mode:
            mode = "read"  # legacy infrared modes default to read
        else:
            mode = "read"  # default fallback
        
        # Configure light group based on POSITION
        if position == "back":
            self.light_group = "back"
        elif position == "front":
            self.light_group = "front"
        elif position == "all":
            self.light_group = "all"
        
        # Configure light settings based on MODE
        if mode == "nap":
            self.light_settings = {
                "on": True,
                "bri": 100,      # nap mode: dimmer light
                "hue": int(100 / 360 * 65535),  # warm/yellow light for relaxation
                "sat": 150       # saturation
            }
        elif mode == "read":
            self.light_settings = {
                "on": True,
                "bri": 250,      # read mode: brighter light
                "hue": int(200 / 360 * 65535),  # cooler/blue light for reading
                "sat": 150       # saturation
            }
        elif mode == "normal":
            self.light_settings = {
                "on": True,
                "bri": 100,      # normal mode: standard light
                "hue": int(100 / 360 * 65535),  # warm light
                "sat": 150       # saturation
            }
        elif mode == "clean":
            self.light_settings = {
                "on": True,
                "bri": 250,      # clean mode: bright light for cleaning
                "hue": int(200 / 360 * 65535),  # cool/white light for visibility
                "sat": 250       # higher saturation for cleaning
            }
        
        # Configure IR device and aircon settings based on POSITION and MODE
        if position == "back":
            self.ir_device_serial = 164793
            if mode == "nap":
                self.aircon_temp_command = "temp_down"  # cooler for nap
            else:
                self.aircon_temp_command = "temp_up"    # warmer for other modes
        elif position == "front":
            self.ir_device_serial = 322207
            if mode == "nap":
                self.aircon_temp_command = "temp_down"  # cooler for nap
            else:
                self.aircon_temp_command = "temp_up"    # warmer for other modes
        elif position == "all":
            # Handle 'all' position with multiple devices
            self.ir_device_serials = [164793, 322207]
            if mode == "normal":
                self.aircon_mode = "both_on"  # Both aircons ON
            elif mode == "clean":
                self.aircon_mode = "both_off"  # Both aircons OFF
            

        
        else:
            # Default fallback - should not reach here with new logic
            logger.warning(f"[{self.agent_id}] Fallback case reached for preset mode: {self.preset_mode}")
            # Set safe defaults
            self.light_group = "back"
            self.light_settings = {
                "on": True,
                "bri": 250,
                "hue": int(200 / 360 * 65535),
                "sat": 150
            }
            self.ir_device_serial = 322207
            self.aircon_temp_command = "temp_up"
            
        if hasattr(self, 'ir_device_serials') and isinstance(self.ir_device_serials, list):
            logger.info(f"[{self.agent_id}] Configured for {self.preset_mode}: light_group={self.light_group}, ir_devices={self.ir_device_serials}, aircon_mode={self.aircon_mode}")
        else:
            logger.info(f"[{self.agent_id}] Configured for {self.preset_mode}: light_group={self.light_group}, ir_device={self.ir_device_serial}, temp_cmd={self.aircon_temp_command}")

    def _extract_position_mode(self):
        """Return (position, mode) derived from current preset_mode string."""
        if "back" in self.preset_mode:
            position = "back"
        elif "front" in self.preset_mode:
            position = "front"
        elif "all" in self.preset_mode:
            position = "all"
        else:
            position = "unknown"

        if "nap" in self.preset_mode:
            mode = "nap"
        elif "read" in self.preset_mode:
            mode = "read"
        elif "normal" in self.preset_mode:
            mode = "normal"
        elif "clean" in self.preset_mode:
            mode = "clean"
        else:
            mode = "unknown"
        return position, mode

    def _build_mode_state_fields(self):
        """Build rich mode/profile state fields for immediate UI visibility."""
        position, mode = self._extract_position_mode()
        fields = {
            "preset_mode": self.preset_mode,
            "preset_position": position,
            "mode_name": mode,
            "light_group": getattr(self, "light_group", ""),
            "light_profile_bri": (self.light_settings or {}).get("bri"),
            "light_profile_hue": (self.light_settings or {}).get("hue"),
            "light_profile_sat": (self.light_settings or {}).get("sat"),
        }
        if hasattr(self, "aircon_mode"):
            fields["aircon_profile_mode"] = self.aircon_mode
        if hasattr(self, "aircon_temp_command"):
            fields["aircon_profile_temp_command"] = self.aircon_temp_command
        return fields
    
    def _test_light_controller(self):
        """Test if the Hue bridge is accessible."""
        try:
            url = f"{self.BASE_URL}/lights"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as response:
                result = response.read().decode("utf-8")
                logger.info(f"[{self.agent_id}] Hue bridge accessible at {self.BRIDGE_IP}")
        except Exception as e:
            logger.warning(f"[{self.agent_id}] Hue bridge not accessible: {e} - using mock mode")
    
    def _test_ir_controller(self):
        """Test if the IR controller script is available and working."""
        try:
            result = subprocess.run([
                "python", self.ir_script_path, "--help"
            ], capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                logger.info(f"[{self.agent_id}] IR controller script available at {self.ir_script_path}")
                logger.info(f"[{self.agent_id}] Will control device: {self.ir_device_serial}")
            else:
                logger.warning(f"[{self.agent_id}] IR controller script not working properly - using mock mode")
                
        except Exception as e:
            logger.warning(f"[{self.agent_id}] IR controller script not available: {e} - using mock mode")
    
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
        
        logger.info(f"[{self.agent_id}] Configured {total_sensors} sensors across {len(self.sensor_config)} sensor types")
    
    def _update_sensor_config(self, new_sensor_config: dict, action: str = "configure"):
        """Update subclass-specific sensor configuration (called by base class)."""
        try:
            if action == "configure":
                self.sensor_config = new_sensor_config
            elif action == "add":
                for sensor_type, sensor_ids in new_sensor_config.items():
                    if sensor_type not in self.sensor_config:
                        self.sensor_config[sensor_type] = []
                    for sensor_id in sensor_ids:
                        if sensor_id not in self.sensor_config[sensor_type]:
                            self.sensor_config[sensor_type].append(sensor_id)
            elif action == "remove":
                for sensor_type, sensor_ids in new_sensor_config.items():
                    if sensor_type in self.sensor_config:
                        for sensor_id in sensor_ids:
                            if sensor_id in self.sensor_config[sensor_type]:
                                self.sensor_config[sensor_type].remove(sensor_id)
                        if not self.sensor_config[sensor_type]:
                            del self.sensor_config[sensor_type]
            
            logger.info(f"[{self.agent_id}] Updated sensor configuration: {self.sensor_config}")
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error updating sensor configuration: {e}")
    
    def _process_sensor_update(self, sensor_payload: SensorPayload, sensor_id: str):
        """Process all sensor data updates immediately."""
        current_time = time.time()
        logger.info(f"[{self.agent_id}] Processing sensor update: {sensor_id}, type: {sensor_payload.sensor_type}, value: {sensor_payload.value}")
        
        if sensor_payload.sensor_type == "infrared":
            self._process_infrared_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "distance":
            self._process_distance_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "motion":
            self._process_motion_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "activity":
            self._process_activity_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "light":
            self._process_light_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "temperature":
            self._process_temperature_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "door":
            self._process_door_sensor(sensor_payload, sensor_id, current_time)
        else:
            logger.warning(f"[{self.agent_id}] Unsupported sensor type: {sensor_payload.sensor_type}")
    
    def _process_infrared_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process infrared sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            if not sensor_payload.metadata or "proximity_status" not in sensor_payload.metadata:
                logger.warning(f"[{self.agent_id}] No proximity_status in infrared sensor metadata")
                return

            # Track infrared_1-specific proximity status for conditional rule evaluation
            if sensor_id == "infrared_1":
                ir1_status = sensor_payload.metadata.get("proximity_status", "unknown")
                if self.local_state.get("infrared_1_proximity_status") != ir1_status:
                    self.local_state["infrared_1_proximity_status"] = ir1_status
                    logger.info(f"[{self.agent_id}] Updated infrared_1_proximity_status to: {ir1_status}")

            # Update global proximity status (consider both infrared and distance sensors)
            proximity_detected = any(
                sensor_info.get("metadata", {}).get("proximity_status") == "near"
                for sensor_info in self.sensor_data.values()
                if sensor_info.get("sensor_type") in ["infrared", "distance"]
            )
            new_proximity_status = "near" if proximity_detected else "far"
            
            if self.local_state.get("proximity_status") != new_proximity_status:
                self.local_state["proximity_status"] = new_proximity_status
                logger.info(f"[{self.agent_id}] Updated proximity_status to: {new_proximity_status}")

            self._update_activity_detected()
            self._schedule_transmission()
    
    def _process_distance_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process distance sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            if not sensor_payload.metadata or "proximity_status" not in sensor_payload.metadata:
                logger.warning(f"[{self.agent_id}] No proximity_status in distance sensor metadata")
                return
                
            proximity_detected = any(
                sensor_info.get("metadata", {}).get("proximity_status") == "near"
                for sensor_info in self.sensor_data.values()
                if sensor_info.get("sensor_type") in ["infrared", "distance"]
            )
            new_proximity_status = "near" if proximity_detected else "far"
            
            if self.local_state.get("proximity_status") != new_proximity_status:
                self.local_state["proximity_status"] = new_proximity_status
                logger.info(f"[{self.agent_id}] Updated proximity_status to: {new_proximity_status}")

            self._update_activity_detected()
            self._schedule_transmission()
    
    def _process_motion_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process motion sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            if not sensor_payload.metadata or "motion_status" not in sensor_payload.metadata:
                logger.warning(f"[{self.agent_id}] No motion_status in motion sensor metadata")
                return
                
            motion_detected = any(
                sensor_info.get("metadata", {}).get("motion_status") == "motion"
                for sensor_info in self.sensor_data.values()
                if sensor_info.get("sensor_type") == "motion"
            )
            new_motion_status = "motion" if motion_detected else "no_motion"
            
            if self.local_state.get("motion_status") != new_motion_status:
                self.local_state["motion_status"] = new_motion_status
                logger.info(f"[{self.agent_id}] Updated motion_status to: {new_motion_status}")

            self._update_activity_detected()
            self._schedule_transmission()
    
    def _process_activity_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process activity sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            if not sensor_payload.metadata or "activity_status" not in sensor_payload.metadata:
                logger.warning(f"[{self.agent_id}] No activity_status in activity sensor metadata")
                return
                
            activity_detected = any(
                sensor_info.get("metadata", {}).get("activity_status") == "active"
                for sensor_info in self.sensor_data.values()
                if sensor_info.get("sensor_type") == "activity"
            )
            new_activity_status = "active" if activity_detected else "inactive"
            
            if self.local_state.get("activity_status") != new_activity_status:
                self.local_state["activity_status"] = new_activity_status
                logger.info(f"[{self.agent_id}] Updated activity_status to: {new_activity_status}")

            self._update_activity_detected()
            self._schedule_transmission()
    
    def _process_light_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process light sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            try:
                lux_value = float(sensor_payload.value)
                light_status = "dark" if lux_value < self.light_threshold_config["threshold"] else "bright"
                
                if self.local_state.get("light_status") != light_status:
                    self.local_state["light_status"] = light_status
                    logger.info(f"[{self.agent_id}] Updated light_status to: {light_status} (lux: {lux_value})")
                
            except (ValueError, TypeError) as e:
                logger.warning(f"[{self.agent_id}] Could not convert light sensor value to float: {sensor_payload.value}, error: {e}")

            self._schedule_transmission()
    
    def _process_temperature_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process temperature sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            try:
                temp_value = float(sensor_payload.value)
                temp_status = "hot" if temp_value >= self.temperature_threshold_config["threshold"] else "cool"
                
                if self.local_state.get("temperature_status") != temp_status:
                    self.local_state["temperature_status"] = temp_status
                    logger.info(f"[{self.agent_id}] Updated temperature_status to: {temp_status} (temp: {temp_value}°C)")
                
            except (ValueError, TypeError) as e:
                logger.warning(f"[{self.agent_id}] Could not convert temperature sensor value to float: {sensor_payload.value}, error: {e}")

            self._schedule_transmission()
    
    def _process_door_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process door sensor updates."""
        with self.state_lock:
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            try:
                door_open_boolean = bool(sensor_payload.value)
                new_door_status = "open" if door_open_boolean else "closed"
                
                if self.local_state.get("door_status") != new_door_status:
                    self.local_state["door_status"] = new_door_status
                    logger.info(f"[{self.agent_id}] Updated door_status to: {new_door_status}")

                self._update_activity_detected()
                self._schedule_transmission()
                
            except (ValueError, TypeError) as e:
                logger.warning(f"[{self.agent_id}] Could not convert door sensor value to boolean: {sensor_payload.value}, error: {e}")
    
    def _update_activity_detected(self):
        """Update the activity_detected field that rules use for decisions."""
        activity_detected = (
            self.local_state.get("proximity_status") == "near" or
            self.local_state.get("motion_status") == "motion" or
            self.local_state.get("activity_status") == "active"
        )
        
        old_activity_detected = self.local_state.get("activity_detected", False)
        if old_activity_detected != activity_detected:
            self.local_state["activity_detected"] = activity_detected
            logger.info(f"[{self.agent_id}] Updated activity_detected: {old_activity_detected} → {activity_detected}")
    
    def _schedule_transmission(self):
        """Schedule rate-limited transmission to context manager."""
        current_time = time.time()
        self.pending_state_update = True
        
        if current_time - self.last_transmission_time >= self.transmission_interval:
            self._transmit_to_context_manager()
    
    def _transmit_to_context_manager(self):
        """Actually transmit state to context manager and reset pending flag."""
        if self.pending_state_update:
            self._trigger_state_publication()
            self.last_transmission_time = time.time()
            self.pending_state_update = False
            logger.debug(f"[{self.agent_id}] Transmitted state to context manager")
    
    def perception(self):
        """Internal perception logic - runs continuously."""
        current_time = time.time()
        
        if (self.pending_state_update and 
            current_time - self.last_transmission_time >= self.transmission_interval):
            self._transmit_to_context_manager()
        
        if not hasattr(self, '_last_perception_log') or current_time - self._last_perception_log > 10:
            with self.state_lock:
                logger.info(f"[{self.agent_id}] Current state: {self.local_state}")
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
                    cleaned[key] = str(value)
            return cleaned
        return data

    def _update_sensor_data(self, event, sensor_payload: SensorPayload):
        """Update sensor data and trigger perception update."""
        sensor_id = event.source.entityId
        
        proximity_changed = False
        complete_state = None
        sensor_data_copy = None
        
        with self.state_lock:
            old_sensor_data = self.sensor_data.get(sensor_id, {})
            
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
                "timestamp": event.event.timestamp
            }
            
            if sensor_payload.sensor_type == "infrared" and sensor_payload.metadata:
                old_proximity = old_sensor_data.get("metadata", {}).get("proximity_status") if old_sensor_data else None
                new_proximity = sensor_payload.metadata.get("proximity_status")
                
                if old_proximity != new_proximity:
                    proximity_changed = True
                    logger.info(f"[{self.agent_id}] Proximity status changed: '{old_proximity}' → '{new_proximity}'")
            
            complete_state = self.local_state.copy()
            sensor_data_copy = self.sensor_data.copy()
        
        self._process_sensor_update(sensor_payload, sensor_id)
        
        clean_complete_state = self._clean_data_for_serialization(complete_state)
        clean_sensor_data = self._clean_data_for_serialization(sensor_data_copy)
        
        self._publish_context_update_with_data(clean_complete_state, clean_sensor_data)
        
        if proximity_changed:
            logger.info(f"[{self.agent_id}] Published context update - PROXIMITY CHANGED: {sensor_payload.value}{sensor_payload.unit}")

    # ------------------------------------------------------------------
    # Threshold MQTT handling
    # ------------------------------------------------------------------

    def _setup_threshold_subscription(self):
        """Subscribe to threshold config command topic."""
        try:
            threshold_topic = TopicManager.threshold_config_command(self.agent_id)
            self.mqtt_client.subscribe(threshold_topic, qos=1)
            logger.info(f"[{self.agent_id}] Subscribed to threshold config topic: {threshold_topic}")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Failed to subscribe to threshold config topic: {e}")

    def _handle_threshold_config_message(self, client, userdata, msg):
        """Decode and dispatch a thresholdConfig MQTT message."""
        try:
            message_str = msg.payload.decode("utf-8")
            event = MQTTMessage.deserialize(message_str)
            if event.event.type == "thresholdConfig":
                threshold_payload = MQTTMessage.get_payload_as(event, ThresholdConfigPayload)
                self._process_threshold_config(threshold_payload, event.event.id)
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error handling threshold config message: {e}")

    def _process_threshold_config(self, threshold_payload: ThresholdConfigPayload, command_id: str):
        """Apply a light or temperature threshold update and publish the result."""
        try:
            t_type = threshold_payload.threshold_type
            if t_type not in ("light", "temperature"):
                result_event = EventFactory.create_threshold_config_result_event(
                    agent_id=self.agent_id,
                    command_id=command_id,
                    success=False,
                    message=f"Unsupported threshold type: {t_type}",
                    threshold_type=t_type,
                    current_config={}
                )
                self._publish_threshold_config_result(result_event)
                return

            config = threshold_payload.config
            success = False
            message = ""

            with self.state_lock:
                if t_type == "light":
                    cfg = self.light_threshold_config
                    if "threshold" in config:
                        new_val = float(config["threshold"])
                        if new_val > 0:
                            old_val = cfg["threshold"]
                            cfg["threshold"] = new_val
                            cfg["last_update"] = time.time()
                            self.local_state["light_threshold"] = new_val
                            success = True
                            message = f"Light threshold updated from {old_val} lux to {new_val} lux"
                            logger.info(f"[{self.agent_id}] {message}")
                            self._reevaluate_light_status()
                        else:
                            message = f"Invalid light threshold: {new_val}. Must be > 0"
                    else:
                        message = "No threshold value provided in configuration"
                    current_config = cfg.copy()

                else:  # temperature
                    cfg = self.temperature_threshold_config
                    if "threshold" in config:
                        new_val = float(config["threshold"])
                        if 0 <= new_val <= 50:
                            old_val = cfg["threshold"]
                            cfg["threshold"] = new_val
                            cfg["last_update"] = time.time()
                            self.local_state["temp_threshold"] = new_val
                            success = True
                            message = f"Temperature threshold updated from {old_val}°C to {new_val}°C"
                            logger.info(f"[{self.agent_id}] {message}")
                            self._reevaluate_temperature_status()
                        else:
                            message = f"Invalid temperature threshold: {new_val}. Must be between 0-50°C"
                    else:
                        message = "No threshold value provided in configuration"
                    current_config = cfg.copy()

            result_event = EventFactory.create_threshold_config_result_event(
                agent_id=self.agent_id,
                command_id=command_id,
                success=success,
                message=message,
                threshold_type=t_type,
                current_config=current_config
            )
            self._publish_threshold_config_result(result_event)

            if success:
                self._trigger_state_publication()

        except Exception as e:
            logger.error(f"[{self.agent_id}] Error processing threshold config: {e}")
            result_event = EventFactory.create_threshold_config_result_event(
                agent_id=self.agent_id,
                command_id=command_id,
                success=False,
                message=f"Error processing threshold config: {str(e)}",
                threshold_type=threshold_payload.threshold_type,
                current_config={}
            )
            self._publish_threshold_config_result(result_event)

    def _publish_threshold_config_result(self, result_event):
        """Publish threshold configuration result to MQTT."""
        try:
            result_topic = TopicManager.threshold_config_result(self.agent_id)
            message = MQTTMessage.serialize(result_event)
            self.mqtt_client.publish(result_topic, message, qos=1)
            logger.debug(f"[{self.agent_id}] Published threshold config result to {result_topic}")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Failed to publish threshold config result: {e}")

    def _reevaluate_light_status(self):
        """Re-classify light status using the current threshold against stored sensor data."""
        for sensor_data in self.sensor_data.values():
            if sensor_data.get("sensor_type") == "light":
                try:
                    self._classify_light_status(float(sensor_data["value"]))
                except (ValueError, TypeError):
                    pass
                return

    def _reevaluate_temperature_status(self):
        """Re-classify temperature status using the current threshold against stored sensor data."""
        for sensor_data in self.sensor_data.values():
            if sensor_data.get("sensor_type") == "temperature":
                try:
                    self._classify_temperature_status(float(sensor_data["value"]))
                except (ValueError, TypeError):
                    pass
                return

    def _classify_light_status(self, lux_value: float):
        """Update light_status using dynamic threshold (must be called under state_lock)."""
        threshold = self.light_threshold_config["threshold"]
        new_status = "dark" if lux_value < threshold else "bright"
        if self.local_state.get("light_status") != new_status:
            self.local_state["light_status"] = new_status
            logger.info(f"[{self.agent_id}] Updated light_status to: {new_status} (lux: {lux_value}, threshold: {threshold})")

    def _classify_temperature_status(self, temp_value: float):
        """Update temperature_status using dynamic threshold (must be called under state_lock)."""
        threshold = self.temperature_threshold_config["threshold"]
        new_status = "hot" if temp_value >= threshold else "cool"
        if self.local_state.get("temperature_status") != new_status:
            self.local_state["temperature_status"] = new_status
            logger.info(f"[{self.agent_id}] Updated temperature_status to: {new_status} (temp: {temp_value}°C, threshold: {threshold}°C)")

    def _on_connect(self, client, userdata, flags, rc):
        """Extend base _on_connect to add threshold subscription."""
        super()._on_connect(client, userdata, flags, rc)
        if rc == 0:
            self._setup_threshold_subscription()

    def _on_message(self, client, userdata, msg):
        """Route threshold config messages; delegate everything else to base class."""
        if msg.topic == TopicManager.threshold_config_command(self.agent_id):
            self._handle_threshold_config_message(client, userdata, msg)
        else:
            super()._on_message(client, userdata, msg)

    # ------------------------------------------------------------------

    def __turn_on_lights(self):
        """Turn on lights with preset-specific settings."""
        try:
            # Ensure group exists (from light_control.py logic)
            self.__ensure_group_exists(self.light_group)
            
            # Get group ID
            group_id = self.__get_group_id_by_name(self.light_group)
            if group_id is None:
                return {
                    "success": False,
                    "message": f"Could not find or create light group: {self.light_group}"
                }
            
            # Turn on lights with preset settings
            url = f"{self.BASE_URL}/groups/{group_id}/action"
            data = json.dumps(self.light_settings).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="PUT")
            
            with urllib.request.urlopen(req, timeout=10) as response:
                result = response.read().decode("utf-8")
                
            return {
                "success": True,
                "message": f"Turned on {self.light_group} lights with preset settings: {result}",
                "new_state": {"light_power": "on"}
            }
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error turning on lights: {e}")
            return {
                "success": False,
                "message": f"Error turning on lights: {str(e)}"
            }
    
    def __turn_off_lights(self):
        """Turn off lights."""
        try:
            group_id = self.__get_group_id_by_name(self.light_group)
            if group_id is None:
                return {
                    "success": False,
                    "message": f"Could not find light group: {self.light_group}"
                }

            url = f"{self.BASE_URL}/groups/{group_id}/action"
            data = json.dumps({"on": False}).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="PUT")

            with urllib.request.urlopen(req, timeout=10) as response:
                result = response.read().decode("utf-8")

            return {
                "success": True,
                "message": f"Turned off {self.light_group} lights: {result}",
                "new_state": {"light_power": "off"}
            }

        except Exception as e:
            logger.error(f"[{self.agent_id}] Error turning off lights: {e}")
            return {
                "success": False,
                "message": f"Error turning off lights: {str(e)}"
            }

    # Bridge inventory used for reconciliation in __turn_on_lights_by_ids.
    # When the rule specifies a subset, any KNOWN light not in the subset is
    # turned OFF so switching between different light_ids sets produces a
    # visible delta instead of leaving stale lights on.
    KNOWN_HUE_LIGHTS = ("1", "2", "3", "5", "6", "7", "8", "10")

    def __turn_on_lights_by_ids(self, light_ids):
        """Turn on a specific list of Hue lights, applying preset light_settings.

        Also turns OFF any KNOWN_HUE_LIGHTS not in the target set so the result
        is exactly {light_ids} on, all others off — necessary for the failover
        demo where switching rule subsets must be visually obvious.
        """
        target_set = {str(x) for x in light_ids}
        excluded = [lid for lid in self.KNOWN_HUE_LIGHTS if lid not in target_set]

        # Step 1: turn OFF lights that should not be in the new subset.
        for lid in excluded:
            try:
                url = f"{self.BASE_URL}/lights/{lid}/state"
                data = json.dumps({"on": False}).encode("utf-8")
                req = urllib.request.Request(url, data=data, method="PUT")
                with urllib.request.urlopen(req, timeout=10) as response:
                    response.read()
                logger.info(f"[{self.agent_id}] Excluded light {lid} turned OFF (not in target subset)")
            except Exception as e:
                logger.warning(f"[{self.agent_id}] Could not turn OFF excluded light {lid}: {e}")

        # Step 2: turn ON target lights with preset settings.
        # effect:none cancels any lingering effect (colorloop, alert) so a
        # previously-on light receives the new color cleanly. transitiontime:0
        # makes the change immediate.
        on_payload = dict(self.light_settings)
        on_payload["effect"] = "none"
        on_payload["transitiontime"] = 0

        successes, failures = [], []
        for raw_id in light_ids:
            light_id = str(raw_id)
            try:
                url = f"{self.BASE_URL}/lights/{light_id}/state"
                data = json.dumps(on_payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, method="PUT")
                with urllib.request.urlopen(req, timeout=10) as response:
                    response.read()
                successes.append(light_id)
            except Exception as e:
                logger.error(f"[{self.agent_id}] Error turning ON light {light_id}: {e}")
                failures.append(f"{light_id}({e})")

        if not failures:
            return {
                "success": True,
                "message": f"Turned ON lights {successes} (excluded OFF: {excluded})",
                "new_state": {"light_power": "on"},
            }
        if successes:
            return {
                "success": False,
                "message": f"Partial ON: success={successes}, failed={failures}, excluded OFF: {excluded}",
                "new_state": {"light_power": "partial"},
            }
        return {
            "success": False,
            "message": f"All target lights failed to turn ON: {failures}",
            "new_state": {"light_power": "off"},
        }

    def __turn_off_lights_by_ids(self, light_ids):
        """Turn off a specific list of Hue lights."""
        successes, failures = [], []
        for raw_id in light_ids:
            light_id = str(raw_id)
            try:
                url = f"{self.BASE_URL}/lights/{light_id}/state"
                data = json.dumps({"on": False}).encode("utf-8")
                req = urllib.request.Request(url, data=data, method="PUT")
                with urllib.request.urlopen(req, timeout=10) as response:
                    response.read()
                successes.append(light_id)
            except Exception as e:
                logger.error(f"[{self.agent_id}] Error turning OFF light {light_id}: {e}")
                failures.append(f"{light_id}({e})")

        if not failures:
            return {
                "success": True,
                "message": f"Turned OFF lights {successes}",
                "new_state": {"light_power": "off"},
            }
        if successes:
            return {
                "success": False,
                "message": f"Partial OFF: success={successes}, failed={failures}",
                "new_state": {"light_power": "partial"},
            }
        return {
            "success": False,
            "message": f"All target lights failed to turn OFF: {failures}",
            "new_state": {"light_power": "on"},
        }
    
    def __get_group_id_by_name(self, group_name):
        """Get group ID by name."""
        try:
            url = f"{self.BASE_URL}/groups"
            req = urllib.request.Request(url, method="GET")
            
            with urllib.request.urlopen(req, timeout=5) as response:
                result = response.read().decode("utf-8")
                groups = json.loads(result)
                
            for group_id, group_info in groups.items():
                if group_info.get('name', '').lower() == group_name.lower():
                    return int(group_id)
            
            return None
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error getting group ID: {e}")
            return None
    
    def __ensure_group_exists(self, group_name):
        """Ensure a light group exists, create it if it doesn't."""
        # Check if group exists
        group_id = self.__get_group_id_by_name(group_name)
        if group_id is not None:
            return group_id
        
        # Create group if it doesn't exist
        try:
            # Define light IDs for different groups (from light_control.py)
            predefined_groups = {
                'front': ['1', '2', '8', '10'],
                'back': ['3', '5', '6', '7'],
                'left': ['1', '5', '6', '10'],
                'right': ['2', '3', '7', '8'],
                'all': ['1', '2', '3', '5', '6', '7', '8', '10']
            }
            
            if group_name not in predefined_groups:
                logger.warning(f"[{self.agent_id}] Unknown group name: {group_name}")
                return None
            
            light_ids = predefined_groups[group_name]
            
            url = f"{self.BASE_URL}/groups"
            group_data = {'name': group_name, 'lights': light_ids}
            data = json.dumps(group_data).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            
            with urllib.request.urlopen(req, timeout=10) as response:
                result = response.read().decode("utf-8")
                logger.info(f"[{self.agent_id}] Created light group '{group_name}': {result}")
                
            # Return the newly created group ID
            return self.__get_group_id_by_name(group_name)
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error creating group '{group_name}': {e}")
            return None
    
    def __turn_on_aircon(self):
        """Turn on aircon with preset-specific settings."""
        try:
            # Check if this is an 'all' mode that requires special handling
            if hasattr(self, 'aircon_mode') and self.aircon_mode == "both_off":
                # In all-clean mode, aircons should stay OFF even when turn_on is called
                return {
                    "success": True,
                    "message": "Aircons kept OFF (clean mode)",
                    "new_state": {"aircon_power": "off"}
                }
            elif hasattr(self, 'ir_device_serials') and isinstance(self.ir_device_serials, list):
                # Handle multiple devices for 'all' modes
                return self._execute_multiple_ir_commands("on")
            else:
                # Single device mode (back/front presets)
                # First turn on the aircon
                result1 = self._execute_ir_command("on")
                if not result1["success"]:
                    return result1
                
                # Small delay between commands
                time.sleep(0.5)
                
                # Then adjust temperature
                result2 = self._execute_ir_command(self.aircon_temp_command)
                
                combined_success = result1["success"] and result2["success"]
                combined_message = f"Aircon ON: {result1['message']}; Temp adjust: {result2['message']}"
                
                return {
                    "success": combined_success,
                    "message": combined_message,
                    "new_state": {"aircon_power": "on"}
                }
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error turning on aircon: {e}")
            return {
                "success": False,
                "message": f"Error turning on aircon: {str(e)}"
            }
    
    def __turn_off_aircon(self):
        """Turn off aircon."""
        try:
            # Check if this is an 'all' mode with multiple devices
            if hasattr(self, 'ir_device_serials') and isinstance(self.ir_device_serials, list):
                # Handle multiple devices for 'all' modes
                return self._execute_multiple_ir_commands("off")
            else:
                # Single device mode (back/front presets)
                result = self._execute_ir_command("off")
                
                if result["success"]:
                    result["new_state"] = {"aircon_power": "off"}
                
                return result
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error turning off aircon: {e}")
            return {
                "success": False,
                "message": f"Error turning off aircon: {str(e)}"
            }
    
    def _execute_multiple_ir_commands(self, command: str) -> dict:
        """Execute IR command on multiple devices (for 'all' modes)."""
        try:
            results = {"success": True, "messages": [], "failures": []}
            
            for device_serial in self.ir_device_serials:
                try:
                    cmd = ["python", self.ir_script_path, "--device", str(device_serial), command]
                    logger.debug(f"[{self.agent_id}] Executing: {' '.join(cmd)}")
                    
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=10,
                        cwd=os.getcwd()
                    )
                    
                    if result.returncode == 0:
                        success_msg = f"Device {device_serial}: {command} successful"
                        results["messages"].append(success_msg)
                        logger.info(f"[{self.agent_id}] {success_msg}")
                    else:
                        error_msg = f"Device {device_serial}: {command} failed - {result.stderr.strip()}"
                        results["failures"].append(error_msg)
                        results["success"] = False
                        logger.error(f"[{self.agent_id}] {error_msg}")
                        
                except Exception as e:
                    error_msg = f"Device {device_serial}: {command} error - {str(e)}"
                    results["failures"].append(error_msg)
                    results["success"] = False
                    logger.error(f"[{self.agent_id}] {error_msg}")
            
            # Combine all messages
            if results["success"]:
                combined_message = f"All devices {command}: " + "; ".join(results["messages"])
                new_state = {"aircon_power": "on" if command == "on" else "off"}
            else:
                success_count = len(results["messages"])
                total_count = len(self.ir_device_serials)
                combined_message = f"{command} completed: {success_count}/{total_count} devices successful"
                if results["failures"]:
                    combined_message += f". Failures: {'; '.join(results['failures'])}"
                new_state = {"aircon_power": "partial"}
            
            return {
                "success": results["success"],
                "message": combined_message,
                "new_state": new_state
            }
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error executing multiple IR commands: {e}")
            return {
                "success": False,
                "message": f"Error executing multiple IR commands: {str(e)}"
            }
    
    def _execute_ir_command(self, command: str) -> dict:
        """Execute IR command on the preset device."""
        try:
            cmd = ["python", self.ir_script_path, "--device", str(self.ir_device_serial), command]
            logger.debug(f"[{self.agent_id}] Executing: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                cwd=os.getcwd()
            )
            
            if result.returncode == 0:
                success_msg = f"Device {self.ir_device_serial}: {command} command successful"
                logger.info(f"[{self.agent_id}] {success_msg}")
                return {
                    "success": True,
                    "message": success_msg
                }
            else:
                error_msg = f"Device {self.ir_device_serial}: {command} command failed - {result.stderr.strip()}"
                logger.error(f"[{self.agent_id}] {error_msg}")
                return {
                    "success": False,
                    "message": error_msg
                }
                
        except subprocess.TimeoutExpired:
            error_msg = f"Device {self.ir_device_serial}: {command} command timed out"
            logger.error(f"[{self.agent_id}] {error_msg}")
            return {
                "success": False,
                "message": error_msg
            }
        except Exception as e:
            error_msg = f"Device {self.ir_device_serial}: {command} command error - {str(e)}"
            logger.error(f"[{self.agent_id}] {error_msg}")
            return {
                "success": False,
                "message": error_msg
            }
    
    def _change_mode(self, new_mode: str) -> dict:
        """Change the agent's mode (nap, read, normal, clean) and reconfigure settings."""
        try:
            position, current_mode = self._extract_position_mode()
            if position == "unknown":
                position = "back"

            # Short-circuit when the requested mode matches the current one.
            # Rule switches within the same profile (e.g. repair-clean → failure-clean →
            # failover-clean, all "clean") otherwise trigger __turn_on_lights() here,
            # which issues a group call that momentarily lights EVERY member of the
            # Hue group — including lights the new rule excludes. Skipping the reapply
            # lets the subsequent rule evaluation drive lighting via
            # __turn_on_lights_by_ids, which reconciles to exactly the rule's light_ids.
            if new_mode == current_mode:
                logger.info(
                    f"[{self.agent_id}] change_mode('{new_mode}') is a no-op "
                    f"(already in mode '{current_mode}'); skipping profile reapply "
                    f"so rule's light_ids drives lighting."
                )
                return {
                    "success": True,
                    "message": f"Already in mode '{new_mode}'; reapply skipped",
                    "new_state": self._build_mode_state_fields(),
                }

            old_preset_mode = self.preset_mode
            if position == "all":
                if new_mode in ["normal", "clean"]:
                    self.preset_mode = f"{position}-{new_mode}"
                else:
                    return {
                        "success": False,
                        "message": f"Invalid mode '{new_mode}' for position '{position}'. Valid modes: normal, clean",
                        "new_state": {}
                    }
            else:  # back or front
                if new_mode in ["nap", "read"]:
                    self.preset_mode = f"{position}-{new_mode}"
                else:
                    return {
                        "success": False,
                        "message": f"Invalid mode '{new_mode}' for position '{position}'. Valid modes: nap, read",
                        "new_state": {}
                    }
            
            # Reconfigure settings with new mode
            self._configure_preset_settings()

            mode_state_fields = self._build_mode_state_fields()
            with self.state_lock:
                self.local_state.update(mode_state_fields)
                currently_powered_on = (
                    self.local_state.get("power") == "on"
                    or self.local_state.get("light_power") == "on"
                    or self.local_state.get("aircon_power") == "on"
                )
            
            logger.info(f"[{self.agent_id}] Mode changed: {old_preset_mode} → {self.preset_mode}")
            logger.info(f"[{self.agent_id}] New light settings: brightness={self.light_settings['bri']}, hue={self.light_settings['hue']}")
            if hasattr(self, 'aircon_temp_command'):
                logger.info(f"[{self.agent_id}] New aircon temp command: {self.aircon_temp_command}")
            elif hasattr(self, 'aircon_mode'):
                logger.info(f"[{self.agent_id}] New aircon mode: {self.aircon_mode}")

            new_state = dict(mode_state_fields)
            message = f"Mode changed from {old_preset_mode} to {self.preset_mode}"

            # Immediate visibility path: if powered on, reapply current profile right away.
            if currently_powered_on:
                light_result = self.__turn_on_lights()
                aircon_result = self.__turn_on_aircon()
                overall_success = light_result["success"] and aircon_result["success"]
                applied_state = {
                    "power": "on" if overall_success else "partial",
                    "light_power": light_result.get("new_state", {}).get("light_power", self.local_state.get("light_power", "unknown")),
                    "aircon_power": aircon_result.get("new_state", {}).get("aircon_power", self.local_state.get("aircon_power", "unknown")),
                }
                with self.state_lock:
                    self.local_state.update(applied_state)
                new_state.update(applied_state)
                message += (
                    f"; profile applied immediately while powered on "
                    f"(light: {light_result['message']}; aircon: {aircon_result['message']})"
                )
            
            return {
                "success": True,
                "message": message,
                "new_state": new_state,
            }
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error changing mode to '{new_mode}': {e}")
            return {
                "success": False,
                "message": f"Error changing mode: {str(e)}",
                "new_state": {}
            }

    def execute_action(self, action_payload: ActionPayload) -> dict:
        """Execute venue control actions (both lights and aircon)."""
        logger.info(f"[{self.agent_id}] Executing action: {action_payload.actionName}")
        
        try:
            if action_payload.actionName in {"turn_on", "turn_off"}:
                # Manual UI sends explicit per-subsystem targets.
                # For backward compatibility and rule-driven automation paths,
                # missing target falls back to combined behavior ("both").
                params = action_payload.parameters if isinstance(action_payload.parameters, dict) else {}
                target = str(params.get("target", "both")).strip().lower()
                if target not in {"light", "aircon", "both"}:
                    return {
                        "success": False,
                        "message": "Invalid parameters.target (expected 'light' or 'aircon')",
                        "new_state": {},
                    }

                # Optional per-light targeting from rule decision or manual command.
                # When absent, the existing group-based path runs unchanged.
                raw_light_ids = params.get("light_ids")
                light_ids_override = (
                    [str(x) for x in raw_light_ids]
                    if isinstance(raw_light_ids, list) and raw_light_ids
                    else None
                )

                def _light_on():
                    return (
                        self.__turn_on_lights_by_ids(light_ids_override)
                        if light_ids_override is not None
                        else self.__turn_on_lights()
                    )

                def _light_off():
                    return (
                        self.__turn_off_lights_by_ids(light_ids_override)
                        if light_ids_override is not None
                        else self.__turn_off_lights()
                    )

                turning_on = action_payload.actionName == "turn_on"
                if target == "both":
                    light_result = _light_on() if turning_on else _light_off()
                    aircon_result = self.__turn_on_aircon() if turning_on else self.__turn_off_aircon()
                    overall_success = bool(light_result.get("success")) and bool(aircon_result.get("success"))
                    with self.state_lock:
                        self.local_state["light_power"] = (light_result.get("new_state") or {}).get("light_power", self.local_state.get("light_power", "unknown"))
                        self.local_state["aircon_power"] = (aircon_result.get("new_state") or {}).get("aircon_power", self.local_state.get("aircon_power", "unknown"))
                        light_power = str(self.local_state.get("light_power", "unknown")).lower()
                        aircon_power = str(self.local_state.get("aircon_power", "unknown")).lower()
                        if light_power == "on" and aircon_power == "on":
                            self.local_state["power"] = "on"
                        elif light_power == "off" and aircon_power == "off":
                            self.local_state["power"] = "off"
                        else:
                            self.local_state["power"] = "partial"
                        new_state = {
                            "power": self.local_state.get("power", "partial"),
                            "light_power": self.local_state.get("light_power", "unknown"),
                            "aircon_power": self.local_state.get("aircon_power", "unknown"),
                        }
                    result = {
                        "success": overall_success,
                        "message": f"Lights: {light_result.get('message', '')}; Aircon: {aircon_result.get('message', '')}",
                        "new_state": new_state,
                    }
                    self._trigger_state_publication()
                    return result

                if target == "light":
                    sub_result = _light_on() if turning_on else _light_off()
                    state_key = "light_power"
                else:
                    sub_result = self.__turn_on_aircon() if turning_on else self.__turn_off_aircon()
                    state_key = "aircon_power"

                success = bool(sub_result.get("success"))
                sub_state = (sub_result.get("new_state") or {}) if isinstance(sub_result, dict) else {}
                updated_value = sub_state.get(state_key, "unknown")

                with self.state_lock:
                    self.local_state[state_key] = updated_value
                    light_power = str(self.local_state.get("light_power", "unknown")).lower()
                    aircon_power = str(self.local_state.get("aircon_power", "unknown")).lower()
                    if light_power == "on" and aircon_power == "on":
                        self.local_state["power"] = "on"
                    elif light_power == "off" and aircon_power == "off":
                        self.local_state["power"] = "off"
                    else:
                        self.local_state["power"] = "partial"
                    new_state = {
                        "power": self.local_state.get("power", "partial"),
                        "light_power": self.local_state.get("light_power", "unknown"),
                        "aircon_power": self.local_state.get("aircon_power", "unknown"),
                    }

                result = {
                    "success": success,
                    "message": f"{target}: {sub_result.get('message', '')}".strip(),
                    "new_state": new_state,
                }
                self._trigger_state_publication()
                
            elif action_payload.actionName == "change_mode":
                # Change agent mode (nap, read, normal, clean)
                mode = action_payload.parameters.get("mode") if action_payload.parameters else None
                if not mode:
                    result = {
                        "success": False,
                        "message": "Missing 'mode' parameter for change_mode action",
                        "new_state": {}
                    }
                else:
                    result = self._change_mode(mode)
                    self._trigger_state_publication()
                
            else:
                result = {
                    "success": False,
                    "message": f"Unknown action: {action_payload.actionName}",
                    "new_state": {}
                }
                logger.warning(f"[{self.agent_id}] Unknown action: {action_payload.actionName}")
            
            return result
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error executing action: {e}")
            return {
                "success": False,
                "message": f"Error executing action: {str(e)}"
            }
    
    def _trigger_initial_state_publication(self):
        """Trigger initial state publication to context manager after initialization."""
        try:
            time.sleep(0.5)
            self._trigger_state_publication()
            logger.info(f"[{self.agent_id}] Initial state published to context manager")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error publishing initial state: {e}")
    
    def stop(self):
        """Stop the event venue agent."""
        logger.info(f"[{self.agent_id}] ClubHouseAgent stopping")
        super().stop() 