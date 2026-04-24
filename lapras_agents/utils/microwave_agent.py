# lapras_agents/microwave_agent.py
import logging
import time
import json

from Phidget22.PhidgetException import PhidgetException, ErrorCode
from Phidget22.Devices.VoltageRatioInput import VoltageRatioInput
from Phidget22.VoltageRatioSensorType import VoltageRatioSensorType
from Phidget22.Unit import Unit
from lapras_middleware.agent import Agent # Ensure this path is correct

logger = logging.getLogger(__name__)

# --- Phidget Configuration ---
LIGHT_SENSOR_SERIAL_NUMBER = 455869
LIGHT_SENSOR_CHANNEL = 2
LIGHT_SENSOR_DATA_INTERVAL_MS = 250
PHIDGET_OPEN_TIMEOUT_MS = 5000

class MicrowaveAgent(Agent):
    INTERNAL_LIGHT_THRESHOLD_LUX = 0.3

    def __init__(self, agent_id: str = "microwave_1", mqtt_broker: str = "localhost", mqtt_port: int = 1883):
        self.light_sensor = None
        self._phidget_initialized_successfully = False
        self._last_perception_log_time = time.time()
        self._last_init_fail_warn_time = 0
        self._last_attach_fail_warn_time = 0
        self._last_unknown_val_warn_time = 0
        
        self._simulated_cook_end_time = 0 
        self._actual_cooking_start_time = 0 
        
        self._door_last_known_status = "unknown" 
        self._door_status_last_changed_time = time.time()

        # Sequence Tracking
        self._door_was_open_long_enough = False # Agent's internal flag for step 1 of sequence

        super().__init__(agent_id, mqtt_broker, mqtt_port)
        logger.info(f"[{self.agent_id}] super().__init__ completed.")

        with self.state_lock:
            # Initialize state by calling the reset method
            self._reset_all_states_to_idle(initializing=True)
        logger.info(f"[{self.agent_id}] Initial local_state: {self.local_state}")

        try:
            self.initialize_light_sensor_strictly_minimal()
            if self._phidget_initialized_successfully:
                logger.info(f"[{self.agent_id}] Phidget Light Sensor initialization was SUCCESSFUL.")
        except Exception as e: 
            logger.error(f"[{self.agent_id}] CRITICAL Exception during Phidget Light Sensor init: {e}", exc_info=True)
            self._phidget_initialized_successfully = False # Ensure flag is correct
        logger.info(f"[{self.agent_id}] MicrowaveAgent __init__ completed. Phidget init success flag: {self._phidget_initialized_successfully}")

    def _reset_all_states_to_idle(self, initializing=False):
        """Resets microwave states to their idle defaults."""
        logger.info(f"[{self.agent_id}] AGENT_LOGIC: Resetting all states to idle defaults.")
        
        self.local_state["microwave/state"] = "idle"
        self.local_state["microwave/cooking_start_time_ts"] = 0.0
        self.local_state["microwave/current_cooking_duration_seconds"] = 0
        self.local_state["display_message"] = "Microwave Ready"
        self.local_state["alert_status"] = "none"
        
        # Reset internal cooking timers
        self._actual_cooking_start_time = 0
        self._simulated_cook_end_time = 0
        
        # Reset door sequence tracking flag
        self._door_was_open_long_enough = False
        
        if initializing: # Only set these on first init
            self.local_state["microwave/internal_light_lux"] = 0.0
            self.local_state["microwave/door_status"] = "unknown"
            self.local_state["microwave/current_door_open_duration"] = 0
            self.local_state["microwave/current_door_closed_duration"] = 0
            self._door_last_known_status = "unknown"
            self._door_status_last_changed_time = time.time()
        else: # For general resets, keep current door status and lux, but reset durations
            self.local_state["microwave/current_door_open_duration"] = 0
            self.local_state["microwave/current_door_closed_duration"] = 0
            self._door_status_last_changed_time = time.time() # Reset timer for current status

    # --- Phidget Initialization and Handlers (Keep the working version from your logs) ---
    def initialize_light_sensor_strictly_minimal(self):
        method_name = f"[{self.agent_id}_LIGHT_INIT_STRICT_MINIMAL]"
        logger.info(f"{method_name} Attempting for SN: {LIGHT_SENSOR_SERIAL_NUMBER}, Ch: {LIGHT_SENSOR_CHANNEL}...")
        self.light_sensor = None 
        self._phidget_initialized_successfully = False
        ch = None 
        try:
            ch = VoltageRatioInput()
            ch.setDeviceSerialNumber(LIGHT_SENSOR_SERIAL_NUMBER)
            ch.setChannel(LIGHT_SENSOR_CHANNEL)
            ch.setIsHubPortDevice(False)
            ch.setOnAttachHandler(self._on_light_sensor_attach) 
            ch.setOnDetachHandler(self._on_light_sensor_detach)
            ch.setOnErrorHandler(self._on_light_sensor_error)
            logger.info(f"{method_name} Calling openWaitForAttachment({PHIDGET_OPEN_TIMEOUT_MS}ms)...")
            ch.openWaitForAttachment(PHIDGET_OPEN_TIMEOUT_MS)
            if ch.getAttached():
                logger.info(f"{method_name} SUCCESS! Phidget ATTACHED. SN: {ch.getDeviceSerialNumber()}, Ch: {ch.getChannel()}")
                try:
                    ch.setSensorType(VoltageRatioSensorType.SENSOR_TYPE_1105)
                    ch.setDataInterval(LIGHT_SENSOR_DATA_INTERVAL_MS)
                    self.light_sensor = ch
                    self._phidget_initialized_successfully = True 
                    logger.info(f"{method_name} Post-attach configuration complete.")
                except PhidgetException as pe_post_attach:
                    logger.error(f"{method_name} PhidgetException during post-attach config: {pe_post_attach.code} - {pe_post_attach.details}", exc_info=True)
                    try: 
                        ch.close(); 
                    except: 
                        pass
                    raise 
            else:
                logger.error(f"{method_name} FAILED. openWaitForAttachment completed, but Phidget is NOT attached.")
                raise PhidgetException(ErrorCode.EPHIDGET_TIMEOUT, "Minimal Initialize Light Sensor: openWaitForAttachment returned but not attached.")
        except PhidgetException as e:
            logger.error(f"{method_name} PhidgetException: {e.code} ({hex(e.code)}) - {e.details}", exc_info=True)
            if ch is not None: 
                try: 
                    ch.close(); 
                except: 
                    pass
            self._phidget_initialized_successfully = False 
            raise 
        except Exception as ex:
            logger.error(f"{method_name} Generic Exception: {str(ex)}", exc_info=True)
            if ch is not None: 
                try: 
                    ch.close(); 
                except: 
                    pass
            self._phidget_initialized_successfully = False 
            raise
    
    def _on_light_sensor_attach(self, ph): 
        logger.info(f"[{self.agent_id}_LIGHT_ATTACH_HANDLER] Event: Light Sensor ATTACHED. SN: {ph.getDeviceSerialNumber()}")
        try:
            ph.setSensorType(VoltageRatioSensorType.SENSOR_TYPE_1105)
            ph.setDataInterval(LIGHT_SENSOR_DATA_INTERVAL_MS)
            if not self._phidget_initialized_successfully:
                 self._phidget_initialized_successfully = True 
            logger.info(f"[{self.agent_id}_LIGHT_ATTACH_HANDLER] Configuration confirmed/re-applied.")
        except PhidgetException as e:
            logger.error(f"[{self.agent_id}_LIGHT_ATTACH_HANDLER] Error in attach handler config: {e.details}")
            self._phidget_initialized_successfully = False
        except Exception as ex:
            logger.error(f"[{self.agent_id}_LIGHT_ATTACH_HANDLER] Generic error in attach handler: {str(ex)}")
            self._phidget_initialized_successfully = False

    def _on_light_sensor_detach(self, ph): 
        logger.warning(f"[{self.agent_id}_LIGHT_DETACH_HANDLER] Event: Light Sensor DETACHED.")
        self._phidget_initialized_successfully = False
        if self.light_sensor == ph: self.light_sensor = None

    def _on_light_sensor_error(self, ph, code, desc): 
        logger.error(f"[{self.agent_id}_LIGHT_ERROR_HANDLER] Event: Phidget ERROR. Code: {code} - {desc}")
        if code == ErrorCode.EPHIDGET_NOTATTACHED: self._phidget_initialized_successfully = False
        if self.light_sensor == ph and code == ErrorCode.EPHIDGET_NOTATTACHED: self.light_sensor = None
    # --- End Phidget ---

    def perception(self) -> None:
        current_time = time.time()
        
        # --- Fetch current state values to work with ---
        # These will be updated based on new sensor readings and logic
        internal_lux = self.local_state.get("microwave/internal_light_lux", 0.0)
        # Use the agent's internal _door_last_known_status for consistent duration logic;
        # newly_determined_door_status will reflect the current sensor reading.
        newly_determined_door_status = self._door_last_known_status 
        
        current_op_state = str(self.local_state.get("microwave/state", "idle"))
        # door_sequence_pending_closure is the published version of the agent's internal _door_was_open_long_enough flag
        # We'll primarily work with the internal flag _door_was_open_long_enough within perception
        
        actual_cooking_start_ts = self.local_state.get("microwave/cooking_start_time_ts", 0.0)
        
        current_door_open_duration = 0  # Will be recalculated based on persisted status and time
        current_door_closed_duration = 0 # Will be recalculated

        # --- 1. Read Light Sensor & Determine Current Door Status ---
        if self._phidget_initialized_successfully and self.light_sensor and self.light_sensor.getAttached():
            try:
                current_lux_reading = self.light_sensor.getSensorValue()
                internal_lux = round(current_lux_reading, 2)
                if internal_lux >= self.INTERNAL_LIGHT_THRESHOLD_LUX: # Accessing class attribute
                    newly_determined_door_status = "open"
                else:
                    newly_determined_door_status = "closed"
            except PhidgetException as e:
                newly_determined_door_status = "unknown" 
                if e.code == ErrorCode.EPHIDGET_UNKNOWNVAL:
                    if current_time - self._last_unknown_val_warn_time > 10:
                        logger.warning(f"[{self.agent_id}_PERCEPTION] Internal light sensor value unknown (EPHIDGET_UNKNOWNVAL). Door status -> unknown.")
                        self._last_unknown_val_warn_time = current_time
                elif e.code == ErrorCode.EPHIDGET_NOTATTACHED:
                     logger.error(f"[{self.agent_id}_PERCEPTION] Internal light sensor not attached during read. Marking as uninitialized.")
                     self._phidget_initialized_successfully = False # Stop further reads
                else:
                    logger.error(f"[{self.agent_id}_PERCEPTION] PhidgetException reading internal light: {e.details}")
                    self._phidget_initialized_successfully = False 
            except Exception as e:
                newly_determined_door_status = "unknown"
                logger.error(f"[{self.agent_id}_PERCEPTION] Generic error reading internal light: {e}", exc_info=True)
                self._phidget_initialized_successfully = False
        else: 
            newly_determined_door_status = "unknown" # Cannot determine if sensor is not OK
            # Logging for sensor not initialized/attached
            if not self._phidget_initialized_successfully :
                if current_time - self._last_init_fail_warn_time > 5 :
                     logger.warning(f"[{self.agent_id}_PERCEPTION] Light sensor not initialized, skipping sensor read.")
                     self._last_init_fail_warn_time = current_time
            elif (not self.light_sensor or not self.light_sensor.getAttached()):
                 if current_time - self._last_attach_fail_warn_time > 5:
                    logger.warning(f"[{self.agent_id}_PERCEPTION] Light sensor missing or detached, skipping sensor read.")
                    self._last_attach_fail_warn_time = current_time
        
        # --- 2. Handle Interruptions: Door opened while "busy" (cooking) ---
        # This takes precedence and might change current_op_state
        if current_op_state == "busy" and newly_determined_door_status == "open" and self._door_last_known_status != "open":
            logger.warning(f"[{self.agent_id}_PERCEPTION] DOOR JUST OPENED WHILE BUSY! Stopping cook and resetting.")
            # Call _reset_all_states_to_idle which will set state to "idle", clear timers, reset sequence flag.
            # We also want to set a specific message for this interruption.
            with self.state_lock: # Ensure immediate state change and reset
                self._reset_all_states_to_idle() # This sets state to "idle"
                current_op_state = "interrupted" # Override to "interrupted" for this cycle's logic
                self.local_state["microwave/state"] = current_op_state # Persist "interrupted"
                self.local_state["display_message"] = "ERROR: Door opened during cooking!"
                self.local_state["alert_status"] = "door_error_busy"
                # Update door status immediately based on sensor for this cycle
                self.local_state["microwave/door_status"] = "open" 
            self._door_last_known_status = "open" # Update internal tracker
            self._door_status_last_changed_time = current_time # Reset timer for open duration
            # Durations will be calculated fresh below based on the new "open" state

        # --- 3. Update Door Durations based on newly_determined_door_status ---
        if newly_determined_door_status != self._door_last_known_status: # If status changed in this cycle
            self._door_status_last_changed_time = current_time
            self._door_last_known_status = newly_determined_door_status
            current_door_open_duration = 0 
            current_door_closed_duration = 0
        else: # Status is the same as last perception cycle
            if newly_determined_door_status == "open":
                current_door_open_duration = int(current_time - self._door_status_last_changed_time)
            elif newly_determined_door_status == "closed":
                current_door_closed_duration = int(current_time - self._door_status_last_changed_time)
        
        # --- 4. Cooking Duration and Completion (if not interrupted) ---
        current_cooking_duration_s = 0
        if current_op_state == "busy": # Check current_op_state again, might have changed
            if self._actual_cooking_start_time > 0: # Use internal reliable start time
                current_cooking_duration_s = int(current_time - self._actual_cooking_start_time)
            
            if self._simulated_cook_end_time > 0 and current_time >= self._simulated_cook_end_time:
                logger.info(f"[{self.agent_id}_PERCEPTION] Microwave cooking time ELAPSED.")
                current_op_state = "ready" 
                # Timers and sequence flags will be reset when transitioning from "ready" or by _reset_all_states_to_idle
        
        # --- 5. Agent-side Sequence Flag Logic (_door_was_open_long_enough) ---
        # This flag indicates if the first part of the door sequence (open for 3s while idle) was met.
        # It's published as "microwave/door_sequence_pending_closure".
        if current_op_state == "idle" and \
           newly_determined_door_status == "open" and \
           current_door_open_duration >= 3:
            if not self._door_was_open_long_enough: # Set it only once per valid open period
                logger.info(f"[{self.agent_id}_PERCEPTION] Door has been open for >= 3s while idle. Setting sequence flag.")
                self._door_was_open_long_enough = True
        elif newly_determined_door_status == "closed": # If door closes, sequence part 1 is no longer actively met by current door state
            if self._door_was_open_long_enough and current_op_state == "idle":
                 # It remains true, waiting for closed duration rule, unless MW becomes busy
                 pass
            else: # If not idle, or if it was never true, ensure it's false
                 self._door_was_open_long_enough = False
        elif newly_determined_door_status != "open": # If door is not open for any other reason (e.g. "unknown")
            self._door_was_open_long_enough = False


        # --- 6. State Transitions to Idle and Message Updates ---
        call_full_reset_flag = False
        new_display_message = self.local_state.get("display_message", "Microwave Ready") # Default to current
        new_alert_status = self.local_state.get("alert_status", "none")

        if current_op_state == "ready":
            new_display_message = "Cooking complete! Food is ready."
            new_alert_status = "none"
            # If door is then opened *after* being ready, reset everything to true idle.
            if newly_determined_door_status == "open" and self._door_last_known_status != "open": # If it *just* opened
                logger.info(f"[{self.agent_id}_PERCEPTION] Door opened after 'ready'. Will reset to full idle.")
                call_full_reset_flag = True
        elif current_op_state == "interrupted": # Set by door open during busy
            new_display_message = "ERROR: Door opened during cooking!"
            new_alert_status = "door_error_busy"
            # If door is now closed after interruption, reset everything to true idle.
            if newly_determined_door_status == "closed":
                logger.info(f"[{self.agent_id}_PERCEPTION] Door closed after 'interrupted'. Will reset to full idle.")
                call_full_reset_flag = True
        elif current_op_state == "idle":
            if self._door_was_open_long_enough: # Step 1 of sequence is met
                 new_display_message = "Door open >3s. Close door for auto-cook."
            # else: message remains "Microwave Ready" (set by _reset_all_states_to_idle) or by other rules
        
        # If microwave becomes busy NOT through our auto-cook sequence (e.g., by manual command)
        # ensure the internal sequence flag is reset.
        if current_op_state == "busy" and not new_display_message.startswith("Auto-cooking"):
            if self._door_was_open_long_enough:
                logger.info(f"[{self.agent_id}_PERCEPTION] Microwave busy by other means; resetting _door_was_open_long_enough.")
                self._door_was_open_long_enough = False


        # --- 7. Update local_state with all determined values ---
        with self.state_lock:
            if call_full_reset_flag:
                self._reset_all_states_to_idle()
                # After _reset_all_states_to_idle, state is "idle", timers cleared, sequence flag false.
                # We need to re-apply current sensor readings for door and lux for this cycle.
                self.local_state["microwave/internal_light_lux"] = internal_lux
                self.local_state["microwave/door_status"] = newly_determined_door_status
                # Durations would have been reset by _reset_all_states_to_idle if it touched _door_status_last_changed_time
                # or we can set them based on current status after reset:
                if newly_determined_door_status == "open": current_door_open_duration = int(current_time - self._door_status_last_changed_time)
                elif newly_determined_door_status == "closed": current_door_closed_duration = int(current_time - self._door_status_last_changed_time)
            else:
                self.local_state["microwave/state"] = current_op_state
                self.local_state["display_message"] = new_display_message
                self.local_state["alert_status"] = new_alert_status
            
            # Always update these based on current perception cycle
            self.local_state["microwave/internal_light_lux"] = internal_lux
            self.local_state["microwave/door_status"] = newly_determined_door_status
            self.local_state["microwave/current_door_open_duration"] = current_door_open_duration
            self.local_state["microwave/current_door_closed_duration"] = current_door_closed_duration
            self.local_state["microwave/door_sequence_pending_closure"] = self._door_was_open_long_enough # Publish internal flag

            if self.local_state["microwave/state"] == "busy":
                # Ensure start_time_ts is the actual start time, not overwritten by stale rule data
                if self._actual_cooking_start_time > 0 :
                     self.local_state["microwave/cooking_start_time_ts"] = self._actual_cooking_start_time
                self.local_state["microwave/current_cooking_duration_seconds"] = current_cooking_duration_s
            elif not call_full_reset_flag : # If not busy and not just reset (which handles these)
                 self.local_state["microwave/cooking_start_time_ts"] = 0.0
                 self.local_state["microwave/current_cooking_duration_seconds"] = 0
                 # Internal trackers should also be clear if not busy
                 if self.local_state["microwave/state"] != "busy": # Double check
                    self._actual_cooking_start_time = 0 
                    self._simulated_cook_end_time = 0


        # --- Logging ---
        if current_time - self._last_perception_log_time > 1: 
            logger.info(
                f"[{self.agent_id}_PERCEPTION] State: MW_State='{self.local_state.get('microwave/state')}', "
                f"Door='{self.local_state.get('microwave/door_status')}' (Lux:{self.local_state.get('microwave/internal_light_lux',0.0):.2f}), "
                f"OpenSecs:{self.local_state.get('microwave/current_door_open_duration',0)}, ClosedSecs:{self.local_state.get('microwave/current_door_closed_duration',0)}, "
                f"SeqPendClose: {self.local_state.get('microwave/door_sequence_pending_closure')}, "
                f"CookTimeS={self.local_state.get('microwave/current_cooking_duration_seconds',0)}, "
                f"Alert='{self.local_state.get('alert_status', 'none')}', "
                f"Msg='{self.local_state.get('display_message', '')}'"
            )
            self._last_perception_log_time = current_time

    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic; payload_str = msg.payload.decode(); payload_data = json.loads(payload_str)
            
            if topic == "context_dist":
                if payload_data.get("agent_id") == self.agent_id and "state" in payload_data:
                    received_state_update = payload_data["state"].copy() 
                    logger.info(f"[{self.agent_id}_MQTT] Received state update from 'context_dist': {received_state_update}")
                    
                    with self.state_lock:
                        # Check for the special trigger from rules to start auto-cook
                        if received_state_update.get("microwave/trigger_auto_cook_7s") == True: # Rule sets this to true
                            # Agent makes final decision based on its current state
                            if self.local_state.get("microwave/state") == "idle" and \
                               self.local_state.get("microwave/door_status") == "closed" and \
                               self._door_was_open_long_enough: # Ensure sequence step 1 was completed
                                
                                logger.info(f"[{self.agent_id}] AUTO-COOK TRIGGERED by rule. Conditions met. Starting 7s cook.")
                                self.local_state["microwave/state"] = "busy"
                                start_time = time.time()
                                self.local_state["microwave/cooking_start_time_ts"] = start_time
                                self.local_state["microwave/current_cooking_duration_seconds"] = 0
                                self.local_state["display_message"] = "Auto-cooking (7s)..."
                                self.local_state["alert_status"] = "none"
                                self._actual_cooking_start_time = start_time 
                                self._simulated_cook_end_time = start_time + 7 
                                
                                self._door_was_open_long_enough = False # Reset sequence flag
                                self.local_state["microwave/door_sequence_pending_closure"] = False # Reflect in published state

                                # Remove keys handled by this specific logic from received_state_update
                                received_state_update.pop("microwave/trigger_auto_cook_7s", None)
                                received_state_update.pop("microwave/state", None) 
                                received_state_update.pop("microwave/door_sequence_pending_closure", None)
                            else:
                                logger.warning(f"[{self.agent_id}] Rule triggered auto-cook, but current conditions not met for start (state: {self.local_state.get('microwave/state')}, door: {self.local_state.get('microwave/door_status')}, seq_flag: {self._door_was_open_long_enough}). Resetting sequence flag.")
                                self._door_was_open_long_enough = False
                                self.local_state["microwave/door_sequence_pending_closure"] = False
                                received_state_update.pop("microwave/trigger_auto_cook_7s", None)
                        
                        # Apply other general state updates from rules
                        if received_state_update: 
                            # If a rule explicitly commands a reset to idle
                            if received_state_update.get("microwave/state") == "idle" and \
                               self.local_state.get("microwave/state") != "idle":
                                self._reset_all_states_to_idle() # Perform full reset
                                self.local_state.update(received_state_update) # Re-apply the "idle" and any other specific changes
                            else: # General update
                                self.local_state.update(received_state_update)
                        
                        logger.info(f"[{self.agent_id}_MQTT] Final local_state after 'context_dist': {self.local_state}")
            # ... (elif for lapras/action/{self.agent_id} as before - for manual commands) ...
            elif topic == f"lapras/action/{self.agent_id}": 
                logger.info(f"[{self.agent_id}_MQTT] Received direct command on '{topic}': {payload_data}")
                action_details = payload_data
                if action_details.get("device") == self.agent_id or action_details.get("device") == "microwave":
                    command = action_details.get("command")
                    parameter = action_details.get("parameter")
                    if command == "start":
                        try: 
                            with self.state_lock: 
                                self._door_was_open_long_enough = False # Manual start resets sequence
                                self.local_state["microwave/door_sequence_pending_closure"] = False
                                logger.info(f"[{self.agent_id}] Manual start cmd received, resetting door sequence flag.")
                            self.handle_microwave_start_command(int(parameter))
                        except (ValueError, TypeError) as e : logger.error(f"[{self.agent_id}_MQTT] Invalid duration '{parameter}' for start: {e}")
                    elif command == "reset_to_idle": 
                        logger.info(f"[{self.agent_id}_MQTT] Command: reset_to_idle received.")
                        with self.state_lock: self._reset_all_states_to_idle()
            
        except Exception as e: logger.error(f"[{self.agent_id}_MQTT] Error processing MQTT: {e}", exc_info=True)

    def handle_microwave_start_command(self, duration_seconds: int):
        # (As before, this is for direct external start commands)
        logger.info(f"[{self.agent_id}] CMD_HANDLER: Processing manual 'start' microwave for {duration_seconds}s.")
        with self.state_lock:
            if self.local_state.get("microwave/door_status") == "open":
                logger.warning(f"[{self.agent_id}] CMD_HANDLER DENIED: Door is open for manual start.")
                self.local_state["display_message"] = "Error: Close door to start!"; return
            if self.local_state.get("microwave/state") != "idle":
                logger.warning(f"[{self.agent_id}] CMD_HANDLER DENIED: Not idle for manual start. State: {self.local_state.get('microwave/state')}")
                self.local_state["display_message"] = f"Error: Microwave not idle."; return
            
            self.local_state["microwave/state"] = "busy"
            start_time = time.time()
            self.local_state["microwave/cooking_start_time_ts"] = start_time
            self.local_state["microwave/current_cooking_duration_seconds"] = 0
            self.local_state["display_message"] = f"Manual Cooking ({duration_seconds}s)..."
            self.local_state["alert_status"] = "none"
            self._actual_cooking_start_time = start_time 
            self._simulated_cook_end_time = start_time + duration_seconds
        logger.info(f"[{self.agent_id}] Microwave set to 'busy' (manual cmd), cooking until ~{time.ctime(self._simulated_cook_end_time)}")

    def stop(self) -> None:
        logger.info(f"[{self.agent_id}] Attempting to stop MicrowaveAgent...")
        if self.light_sensor:
            try:
                if self.light_sensor.getAttached(): self.light_sensor.close()
            except: pass # Ignore errors on close during shutdown
            self.light_sensor = None
        self._phidget_initialized_successfully = False
        
        with self.state_lock:
            self._reset_all_states_to_idle() 
            self.local_state["microwave/state"] = "offline" 
            self.local_state["microwave/door_status"] = "offline" 
            self.local_state["display_message"] = "Agent Offline"

        logger.info(f"[{self.agent_id}] Calling super().stop().")
        super().stop()
        logger.info(f"[{self.agent_id}] MicrowaveAgent definitively stopped.")