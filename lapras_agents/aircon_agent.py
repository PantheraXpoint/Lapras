import logging
import time
import sys
import os
import subprocess
from collections import defaultdict

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lapras_middleware.virtual_agent import VirtualAgent
from lapras_middleware.event import EventFactory, MQTTMessage, TopicManager, SensorPayload, ActionPayload, ActionReportPayload, ThresholdConfigPayload

logger = logging.getLogger(__name__)

class AirconAgent(VirtualAgent):
    def __init__(self, agent_id: str = "aircon", mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883, 
                 sensor_config: dict = None, transmission_interval: float = 0.5,
                 temp_threshold: float = 20.0):
        
        # Initialize attributes that might be accessed by perception thread first
        self.transmission_interval = transmission_interval  # seconds between transmissions
        self.last_transmission_time = 0.0
        self.pending_state_update = False
        
        super().__init__(agent_id, "aircon", mqtt_broker, mqtt_port)
        
        logger.info(f"[{self.agent_id}] AirconAgent initialized")

        # Default sensor configuration if none provided
        if sensor_config is None:
            sensor_config = {
                "infrared": ["infrared_1"],  # Default to infrared sensor
                "temperature": ["temperature_1"]  # Add temperature sensor support
            }
        
        self.sensor_config = sensor_config
        self.supported_sensor_types = ["infrared", "distance", "motion", "activity", "temperature", "door"]
        
        # Temperature threshold configuration for hot/cool classification - DYNAMIC (simplified)
        self.temperature_threshold_config = {
            "threshold": temp_threshold,  # Above this = hot, below = cool
            "last_update": time.time()
        }
        
        # Define the two IR controller device serial numbers
        self.ir_device_serials = [322207, 164793]
        
        # Path to the IR controller script
        self.ir_script_path = "lapras_agents/utils/test_ir_controller.py"

        # Minimal mode scaffold for consistent cross-agent controls.
        self.mode_profiles = {
            "normal": {"description": "default comfort profile"},
            "eco": {"description": "energy-saving profile"},
            "boost": {"description": "rapid cooling profile"},
        }
        self.current_mode = "normal"
        
        # Test IR controller availability
        self._test_ir_controller()
        
        # Initialize local state with known state
        self.local_state.update({
            "power": "off",
            "proximity_status": "unknown",
            "motion_status": "unknown", 
            "activity_status": "unknown",
            "activity_detected": False,
            "temperature_status": "unknown",  # hot, cool (simplified)
            "temp_threshold": self.temperature_threshold_config["threshold"],  # Expose current threshold
            "current_temperature": None,  # Will be updated by temperature sensor
            "door_status": "unknown",  # open, closed, unknown
            "mode_name": self.current_mode,
        })
        
        self.sensor_data = defaultdict(dict)  # Store sensor data with sensor_id as key
        
        # Dynamically add sensors based on configuration
        self._configure_sensors()
        
        logger.info(f"[{self.agent_id}] AirconAgent initialization completed with sensors: {sensor_config} and transmission interval: {transmission_interval}s, temp threshold: {temp_threshold}°C")

        # Publish initial state to context manager
        self._trigger_initial_state_publication()
    
    def _test_ir_controller(self):
        """Test if the IR controller script is available and working."""
        try:
            # Test if the script exists and is executable
            result = subprocess.run([
                "python", self.ir_script_path, "--help"
            ], capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                logger.info(f"[{self.agent_id}] IR controller script available at {self.ir_script_path}")
                logger.info(f"[{self.agent_id}] Will control devices: {self.ir_device_serials}")
            else:
                logger.warning(f"[{self.agent_id}] IR controller script not working properly - using mock mode")
                
        except Exception as e:
            logger.warning(f"[{self.agent_id}] IR controller script not available: {e} - using mock mode")
    
    def _execute_ir_command(self, command: str) -> dict:
        """Execute IR command on both devices using shell commands."""
        results = {"success": True, "messages": [], "failures": []}
        
        for device_serial in self.ir_device_serials:
            try:
                cmd = ["python", self.ir_script_path, "--device", str(device_serial), command]
                logger.debug(f"[{self.agent_id}] Executing: {' '.join(cmd)}")
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,  # 10 second timeout
                    cwd=os.getcwd()
                )
                
                if result.returncode == 0:
                    success_msg = f"Device {device_serial}: {command} command successful"
                    results["messages"].append(success_msg)
                    logger.info(f"[{self.agent_id}] {success_msg}")
                else:
                    error_msg = f"Device {device_serial}: {command} command failed - {result.stderr.strip()}"
                    results["failures"].append(error_msg)
                    results["success"] = False
                    logger.error(f"[{self.agent_id}] {error_msg}")
                    
            except subprocess.TimeoutExpired:
                error_msg = f"Device {device_serial}: {command} command timed out"
                results["failures"].append(error_msg)
                results["success"] = False
                logger.error(f"[{self.agent_id}] {error_msg}")
                
            except Exception as e:
                error_msg = f"Device {device_serial}: {command} command error - {str(e)}"
                results["failures"].append(error_msg)
                results["success"] = False
                logger.error(f"[{self.agent_id}] {error_msg}")
        
        # Combine all messages
        if results["success"]:
            combined_message = f"Successfully executed '{command}' on all devices: " + "; ".join(results["messages"])
        else:
            success_count = len(results["messages"])
            total_count = len(self.ir_device_serials)
            combined_message = f"'{command}' completed with {success_count}/{total_count} devices successful"
            if results["failures"]:
                combined_message += f". Failures: {'; '.join(results['failures'])}"
        
        return {
            "success": results["success"],
            "message": combined_message,
            "individual_results": {
                "successes": results["messages"],
                "failures": results["failures"]
            }
        }
    
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
            
            logger.info(f"[{self.agent_id}] Updated sensor configuration: {self.sensor_config}")
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error updating sensor configuration: {e}")
    
    def _process_sensor_update(self, sensor_payload: SensorPayload, sensor_id: str):
        """Process all sensor data updates immediately - no timing logic."""
        current_time = time.time()
        logger.info(f"[{self.agent_id}] Processing sensor update: {sensor_id}, type: {sensor_payload.sensor_type}, value: {sensor_payload.value}")
        
        # Process every sensor update immediately - let context manager handle timing decisions
        if sensor_payload.sensor_type == "infrared":
            self._process_infrared_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "distance":
            self._process_distance_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "motion":
            self._process_motion_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "activity":
            self._process_activity_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "temperature":
            self._process_temperature_sensor(sensor_payload, sensor_id, current_time)
        elif sensor_payload.sensor_type == "door":
            self._process_door_sensor(sensor_payload, sensor_id, current_time)
        else:
            logger.warning(f"[{self.agent_id}] Unsupported sensor type: {sensor_payload.sensor_type}")
    
    def _process_infrared_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process infrared sensor updates."""
        with self.state_lock:
            # Update sensor data
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            if not sensor_payload.metadata or "proximity_status" not in sensor_payload.metadata:
                logger.warning(f"[{self.agent_id}] No proximity_status in infrared sensor metadata")
                return
                
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

            # Update activity_detected field for rules
            self._update_activity_detected()

            # Rate-limited state publication to context manager
            self._schedule_transmission()
    
    def _process_distance_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process distance sensor updates (same as infrared - uses proximity_status)."""
        with self.state_lock:
            # Update sensor data
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            if not sensor_payload.metadata or "proximity_status" not in sensor_payload.metadata:
                logger.warning(f"[{self.agent_id}] No proximity_status in distance sensor metadata")
                return
                
            # Update global proximity status (distance sensors work same as infrared)
            proximity_detected = any(
                sensor_info.get("metadata", {}).get("proximity_status") == "near"
                for sensor_info in self.sensor_data.values()
                if sensor_info.get("sensor_type") in ["infrared", "distance"]  # Both sensor types
            )
            new_proximity_status = "near" if proximity_detected else "far"
            
            if self.local_state.get("proximity_status") != new_proximity_status:
                self.local_state["proximity_status"] = new_proximity_status
                logger.info(f"[{self.agent_id}] Updated proximity_status to: {new_proximity_status}")

            # Update activity_detected field for rules
            self._update_activity_detected()

            # Rate-limited state publication to context manager
            self._schedule_transmission()
    
    def _process_motion_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process motion sensor updates."""
        with self.state_lock:
            # Update sensor data
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            if not sensor_payload.metadata or "motion_status" not in sensor_payload.metadata:
                logger.warning(f"[{self.agent_id}] No motion_status in motion sensor metadata")
                return
                
            # Update global motion status
            motion_detected = any(
                sensor_info.get("metadata", {}).get("motion_status") == "motion"
                for sensor_info in self.sensor_data.values()
                if sensor_info.get("sensor_type") == "motion"
            )
            new_motion_status = "motion" if motion_detected else "no_motion"
            
            if self.local_state.get("motion_status") != new_motion_status:
                self.local_state["motion_status"] = new_motion_status
                logger.info(f"[{self.agent_id}] Updated motion_status to: {new_motion_status}")

            # Update activity_detected field for rules
            self._update_activity_detected()

            # Rate-limited state publication to context manager
            self._schedule_transmission()
    
    def _process_activity_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process activity sensor updates."""
        with self.state_lock:
            # Update sensor data
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            if not sensor_payload.metadata or "activity_status" not in sensor_payload.metadata:
                logger.warning(f"[{self.agent_id}] No activity_status in activity sensor metadata")
                return
                
            # Update global activity status
            activity_detected = any(
                sensor_info.get("metadata", {}).get("activity_status") == "active"
                for sensor_info in self.sensor_data.values()
                if sensor_info.get("sensor_type") == "activity"
            )
            new_activity_status = "active" if activity_detected else "inactive"
            
            if self.local_state.get("activity_status") != new_activity_status:
                self.local_state["activity_status"] = new_activity_status
                logger.info(f"[{self.agent_id}] Updated activity_status to: {new_activity_status}")

            # Update activity_detected field for rules
            self._update_activity_detected()

            # Rate-limited state publication to context manager
            self._schedule_transmission()
    
    def _process_temperature_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process temperature sensor updates and classify as hot/cool."""
        with self.state_lock:
            # Update sensor data
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            # Update current temperature in local state from sensor data
            try:
                temp_value = float(sensor_payload.value)
                self.local_state["current_temperature"] = temp_value
                logger.info(f"[{self.agent_id}] Updated current_temperature from sensor: {temp_value}°C")
                
                # Classify temperature using dynamic thresholds
                self._classify_temperature_status(temp_value)
                
            except (ValueError, TypeError) as e:
                logger.warning(f"[{self.agent_id}] Could not convert temperature sensor value to float: {sensor_payload.value}, error: {e}")
                return

            # Rate-limited state publication to context manager
            self._schedule_transmission()
    
    def _process_door_sensor(self, sensor_payload: SensorPayload, sensor_id: str, current_time: float):
        """Process door sensor updates and transform boolean to door_status."""
        with self.state_lock:
            # Update sensor data
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
            }

            # Transform boolean value to door_status string
            # True = door open, False = door closed
            try:
                door_open_boolean = bool(sensor_payload.value)
                new_door_status = "open" if door_open_boolean else "closed"
                
                if self.local_state.get("door_status") != new_door_status:
                    self.local_state["door_status"] = new_door_status
                    logger.info(f"[{self.agent_id}] Updated door_status to: {new_door_status} (boolean value: {door_open_boolean})")

                # Update activity_detected field for rules
                self._update_activity_detected()

                # Rate-limited state publication to context manager
                self._schedule_transmission()
                
            except (ValueError, TypeError) as e:
                logger.warning(f"[{self.agent_id}] Could not convert door sensor value to boolean: {sensor_payload.value}, error: {e}")
                return
    
    def _update_activity_detected(self):
        """Update the activity_detected field that rules use for decisions."""
        # Check if any sensor shows activity
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
        
        # Mark that we have a pending state update
        self.pending_state_update = True
        
        # Check if enough time has passed since last transmission
        if current_time - self.last_transmission_time >= self.transmission_interval:
            self._transmit_to_context_manager()
        # If not enough time has passed, the periodic check will handle it
    
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
        
        # Handle rate-limited transmissions
        if (self.pending_state_update and 
            current_time - self.last_transmission_time >= self.transmission_interval):
            self._transmit_to_context_manager()
        
        # Periodic state logging
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
                    # Convert other types to string
                    cleaned[key] = str(value)
            return cleaned
        return data

    def _update_sensor_data(self, event, sensor_payload: SensorPayload):
        """Update sensor data and trigger perception update."""
        sensor_id = event.source.entityId
        
        logger.info(f"[{self.agent_id}] DEBUG: _update_sensor_data started for sensor {sensor_id}")
        
        # Track if proximity status changed (for infrared sensors)
        proximity_changed = False
        complete_state = None
        sensor_data_copy = None
        
        with self.state_lock:
            old_sensor_data = self.sensor_data.get(sensor_id, {})
            
            # Store new sensor data
            self.sensor_data[sensor_id] = {
                "sensor_type": sensor_payload.sensor_type,
                "value": sensor_payload.value,
                "unit": sensor_payload.unit,
                "metadata": sensor_payload.metadata,
                "timestamp": event.event.timestamp
            }
            
            # Check if proximity status changed for infrared sensors
            if sensor_payload.sensor_type == "infrared" and sensor_payload.metadata:
                assert isinstance(old_sensor_data, dict), f"[YUHENG] meta is {old_sensor_data}, sensor_payload metadata is {sensor_payload.metadata}"
                old_proximity = old_sensor_data.get("metadata", {}).get("proximity_status") if old_sensor_data else None
                new_proximity = sensor_payload.metadata.get("proximity_status")
                
                if old_proximity != new_proximity:
                    proximity_changed = True
                    logger.info(f"[{self.agent_id}] Proximity status changed: '{old_proximity}' → '{new_proximity}'")
            
            # Prepare data for publishing while we have the lock
            complete_state = self.local_state.copy()
            sensor_data_copy = self.sensor_data.copy()
        
        # Always trigger perception update for subclass processing
        self._process_sensor_update(sensor_payload, sensor_id)
        
        # Clean the data before publishing
        clean_complete_state = self._clean_data_for_serialization(complete_state)
        clean_sensor_data = self._clean_data_for_serialization(sensor_data_copy)
        
        self._publish_context_update_with_data(clean_complete_state, clean_sensor_data)
        
        # Log differently based on whether proximity changed
        if proximity_changed:
            logger.info(f"[{self.agent_id}] Published context update - PROXIMITY CHANGED: {sensor_payload.value}{sensor_payload.unit}")
        else:
            pass

    def __turn_on_aircon(self):
        """Turn on both air conditioners using IR controller commands."""
        try:
            result = self._execute_ir_command("on")
            
            if result["success"]:
                return {
                    "success": True,
                    "message": f"Turned on all aircons via IR controllers: {result['message']}",
                    "new_state": {
                        "power": "on"
                    }
                }
            else:
                return {
                    "success": False,
                    "message": f"Failed to turn on some/all aircons: {result['message']}"
                }
                
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error turning on aircons: {e}")
            return {
                "success": False,
                "message": f"Error turning on aircons: {str(e)}"
            }
    
    def __turn_off_aircon(self):
        """Turn off both air conditioners using IR controller commands."""
        try:
            result = self._execute_ir_command("off")
            
            if result["success"]:
                return {
                    "success": True,
                    "message": f"Turned off all aircons via IR controllers: {result['message']}",
                    "new_state": {
                        "power": "off"
                    }
                }
            else:
                return {
                    "success": False,
                    "message": f"Failed to turn off some/all aircons: {result['message']}"
                }
                
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error turning off aircons: {e}")
            return {
                "success": False,
                "message": f"Error turning off aircons: {str(e)}"
            }

    def _change_mode(self, new_mode: str) -> dict:
        mode = (new_mode or "").strip().lower()
        if mode not in self.mode_profiles:
            return {
                "success": False,
                "message": f"Unsupported mode '{new_mode}'. Available modes: {sorted(self.mode_profiles.keys())}",
                "new_state": {},
            }

        old_mode = self.current_mode
        self.current_mode = mode
        with self.state_lock:
            self.local_state["mode_name"] = mode

        return {
            "success": True,
            "message": f"Mode changed from {old_mode} to {mode}",
            "new_state": {
                "mode_name": mode,
                "mode_profile": self.mode_profiles[mode],
            },
        }
    
    def execute_action(self, action_payload: ActionPayload) -> dict:
        """Execute aircon control actions with fast response (no verification)."""
        logger.info(f"[{self.agent_id}] Executing action: {action_payload.actionName}")
        
        # Check if verification should be skipped for faster response
        # For manual commands or when explicitly disabled
        skip_verification = action_payload.parameters and action_payload.parameters.get("skip_verification", False)
        
        try:            
            if action_payload.actionName == "turn_on":
                # Execute the physical action
                result = self.__turn_on_aircon()
                
                if skip_verification:
                    # Skip verification for faster response
                    logger.info(f"[{self.agent_id}] Turn ON command executed (verification skipped for speed)")
                    with self.state_lock:
                        self.local_state["power"] = "on"
                        result["new_state"]["power"] = "on"
                        result["message"] = "Turn ON: executed (verification skipped)"
                else:
                    # Update local state immediately without verification (aircon doesn't have verification)
                    logger.info(f"[{self.agent_id}] Turn ON command executed")
                    with self.state_lock:
                        self.local_state["power"] = "on"
                        result["new_state"]["power"] = "on"
                
                # Always trigger state publication
                self._trigger_state_publication()
                
            elif action_payload.actionName == "turn_off":
                # Execute the physical action
                result = self.__turn_off_aircon()
                
                if skip_verification:
                    # Skip verification for faster response
                    logger.info(f"[{self.agent_id}] Turn OFF command executed (verification skipped for speed)")
                    with self.state_lock:
                        self.local_state["power"] = "off"
                        result["new_state"]["power"] = "off"
                        result["message"] = "Turn OFF: executed (verification skipped)"
                else:
                    # Update local state immediately without verification (aircon doesn't have verification)
                    logger.info(f"[{self.agent_id}] Turn OFF command executed")
                    with self.state_lock:
                        self.local_state["power"] = "off"
                        result["new_state"]["power"] = "off"
                
                # Always trigger state publication
                self._trigger_state_publication()

            elif action_payload.actionName == "change_mode":
                mode = action_payload.parameters.get("mode") if action_payload.parameters else None
                if not mode:
                    result = {
                        "success": False,
                        "message": "Missing 'mode' parameter for change_mode action",
                        "new_state": {},
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
    
    def stop(self):
        """Stop the aircon agent."""
        logger.info(f"[{self.agent_id}] AirconAgent stopping")
        super().stop()

    def _trigger_initial_state_publication(self):
        """Trigger initial state publication to context manager after initialization."""
        try:
            # Small delay to ensure MQTT connection is ready
            import time
            time.sleep(0.5)
            
            # Trigger state publication with actual aircon state
            self._trigger_state_publication()
            logger.info(f"[{self.agent_id}] Initial state published to context manager: power={self.local_state.get('power')}")
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error publishing initial state: {e}")

    def refresh_aircon_state(self):
        """Manually refresh the aircon state and update local state."""
        try:
            logger.info(f"[{self.agent_id}] Manually refreshing aircon state...")
            
            with self.state_lock:
                old_power_state = self.local_state.get("power")
                # Since we don't have verification anymore, just log current state
                current_power_state = self.local_state.get("power")
                
                if old_power_state == current_power_state:
                    logger.debug(f"[{self.agent_id}] Aircon state refresh: no change ({current_power_state})")
                    return False  # No state change
                else:
                    logger.info(f"[{self.agent_id}] Aircon state refreshed: {old_power_state} → {current_power_state}")
                    # Trigger state publication since state changed
                    self._trigger_state_publication()
                    return True  # State changed
                    
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error refreshing aircon state: {e}")
            return False

    def _setup_threshold_subscription(self):
        """Setup subscription for threshold configuration commands."""
        try:
            threshold_topic = TopicManager.threshold_config_command(self.agent_id)
            self.mqtt_client.subscribe(threshold_topic, qos=1)
            logger.info(f"[{self.agent_id}] Subscribed to threshold config topic: {threshold_topic}")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Failed to subscribe to threshold config topic: {e}")

    def _handle_threshold_config_message(self, client, userdata, msg):
        """Handle threshold configuration messages."""
        try:
            message_str = msg.payload.decode('utf-8')
            event = MQTTMessage.deserialize(message_str)
            
            if event.event.type == "thresholdConfig":
                threshold_payload = MQTTMessage.get_payload_as(event, ThresholdConfigPayload)
                self._process_threshold_config(threshold_payload, event.event.id)
                
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error handling threshold config message: {e}")

    def _process_threshold_config(self, threshold_payload: ThresholdConfigPayload, command_id: str):
        """Process temperature threshold configuration update (simplified to single threshold)."""
        try:
            if threshold_payload.threshold_type != "temperature":
                result_event = EventFactory.create_threshold_config_result_event(
                    agent_id=self.agent_id,
                    command_id=command_id,
                    success=False,
                    message=f"Unsupported threshold type: {threshold_payload.threshold_type}",
                    threshold_type=threshold_payload.threshold_type,
                    current_config=self.temperature_threshold_config.copy()
                )
                self._publish_threshold_config_result(result_event)
                return

            config = threshold_payload.config
            success = False
            message = ""
            
            with self.state_lock:
                old_threshold = self.temperature_threshold_config["threshold"]
                
                # Update threshold configuration (simplified to single threshold)
                if "threshold" in config:
                    new_threshold = float(config["threshold"])
                    
                    if 0 <= new_threshold <= 50:  # Reasonable temp range
                        self.temperature_threshold_config["threshold"] = new_threshold
                        self.temperature_threshold_config["last_update"] = time.time()
                        
                        # Update local state to reflect new threshold
                        self.local_state["temp_threshold"] = new_threshold
                        
                        success = True
                        message = f"Temperature threshold updated from {old_threshold}°C to {new_threshold}°C"
                        logger.info(f"[{self.agent_id}] {message}")
                        
                        # Re-evaluate current temperature status with new threshold
                        self._reevaluate_temperature_status()
                        
                    else:
                        message = f"Invalid threshold value: {new_threshold}. Must be between 0-50°C"
                else:
                    message = "No threshold value provided in configuration"

            # Send result back
            result_event = EventFactory.create_threshold_config_result_event(
                agent_id=self.agent_id,
                command_id=command_id,
                success=success,
                message=message,
                threshold_type="temperature",
                current_config=self.temperature_threshold_config.copy()
            )
            self._publish_threshold_config_result(result_event)
            
            if success:
                # Trigger state publication to update context manager
                self._trigger_state_publication()
                
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error processing threshold config: {e}")
            result_event = EventFactory.create_threshold_config_result_event(
                agent_id=self.agent_id,
                command_id=command_id,
                success=False,
                message=f"Error processing threshold config: {str(e)}",
                threshold_type="temperature",
                current_config=self.temperature_threshold_config.copy()
            )
            self._publish_threshold_config_result(result_event)

    def _publish_threshold_config_result(self, result_event):
        """Publish threshold configuration result."""
        try:
            result_topic = TopicManager.threshold_config_result(self.agent_id)
            message = MQTTMessage.serialize(result_event)
            self.mqtt_client.publish(result_topic, message, qos=1)
            logger.debug(f"[{self.agent_id}] Published threshold config result to {result_topic}")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Failed to publish threshold config result: {e}")

    def _reevaluate_temperature_status(self):
        """Re-evaluate temperature status with current sensor data using new threshold."""
        # Find the most recent temperature sensor reading
        latest_temp_data = None
        for sensor_id, sensor_data in self.sensor_data.items():
            if sensor_data.get("sensor_type") == "temperature":
                if latest_temp_data is None:
                    latest_temp_data = sensor_data
                # Could add timestamp comparison here if needed
        
        if latest_temp_data:
            try:
                temp_value = float(latest_temp_data["value"])
                self._classify_temperature_status(temp_value)
                
            except (ValueError, TypeError) as e:
                logger.warning(f"[{self.agent_id}] Could not re-evaluate temperature status: {e}")

    def _classify_temperature_status(self, temp_value: float):
        """Classify temperature as hot/cool using simple threshold comparison."""
        threshold = self.temperature_threshold_config["threshold"]
        
        # Simple threshold logic: above threshold = hot, below = cool
        new_status = "hot" if temp_value >= threshold else "cool"
        
        if self.local_state.get("temperature_status") != new_status:
            self.local_state["temperature_status"] = new_status
            logger.info(f"[{self.agent_id}] Updated temperature_status to: {new_status} (temp: {temp_value}°C, threshold: {threshold}°C)")

    def get_current_threshold_config(self) -> dict:
        """Get current temperature threshold configuration."""
        with self.state_lock:
            return self.temperature_threshold_config.copy()

    def _on_message(self, client, userdata, msg):
        """Override to handle threshold config messages in addition to base functionality."""
        # Check if this is a threshold config message
        if msg.topic == TopicManager.threshold_config_command(self.agent_id):
            self._handle_threshold_config_message(client, userdata, msg)
        else:
            # Call parent's message handler for other messages
            super()._on_message(client, userdata, msg)

    def _on_connect(self, client, userdata, flags, rc):
        """Override to add threshold subscription when MQTT connection is ready."""
        # Call parent's _on_connect first to set up regular subscriptions
        super()._on_connect(client, userdata, flags, rc)
        
        # Add threshold subscription after MQTT is connected
        if rc == 0:
            self._setup_threshold_subscription()


