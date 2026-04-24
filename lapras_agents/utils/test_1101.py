import time
import math # Only needed if you add manual formula calculations later
from Phidget22.Phidget import Phidget
from Phidget22.PhidgetException import PhidgetException, ErrorCode
from Phidget22.Devices.VoltageRatioInput import VoltageRatioInput
from Phidget22.VoltageRatioSensorType import VoltageRatioSensorType
from Phidget22.Unit import Unit

# --- Configuration ---
# YOU HAVE IDENTIFIED THE IR SENSOR (Phidget 1101 Adapter) IS ON CHANNEL 2
IR_SENSOR_CHANNEL = 3

# We will read the raw voltage ratio as the specific Sharp sensor model
# connected to your 1101 adapter is not specified to this script.
# If you later identify your Sharp sensor (e.g., it's a GP2Y0A21YK0F),
# you could change this to a more specific type like:
# VoltageRatioSensorType.SENSOR_TYPE_3521
# or VoltageRatioSensorType.SENSOR_TYPE_1101_SHARP_2Y0A21
# to potentially get direct distance readings in centimeters.
IR_SENSOR_TYPE_TO_SET = VoltageRatioSensorType.SENSOR_TYPE_VOLTAGERATIO

# Set to your InterfaceKit's serial number if known and needed.
# You can find this from previous script runs or on the device itself.
# Example: SERIAL_NUMBER = 455869
SERIAL_NUMBER = 455869 # Use -1 if it's the only Phidget or to auto-detect
# SERIAL_NUMBER = 455252 run this command to see the serial number "phidget22admin -d"

DATA_INTERVAL_MS = 200 # How often to try to get a new value (in milliseconds)
# --- End Configuration ---

def on_ir_attach(ph):
    print(f"IR Sensor (Channel {ph.getChannel()}) Attached! Serial: {ph.getDeviceSerialNumber()}")
    try:
        ph.setSensorType(IR_SENSOR_TYPE_TO_SET)
        current_set_type = ph.getSensorType()
        unit_info = ph.getSensorUnit()
        unit_name = "V/V (Ratio)" # Default for SENSOR_TYPE_VOLTAGERATIO
        
        # For SENSOR_TYPE_VOLTAGERATIO, unit_info.name might be "Unitless"
        # We'll use our explicit label for clarity.
        if current_set_type == VoltageRatioSensorType.SENSOR_TYPE_VOLTAGERATIO:
            actual_unit_display = "V/V (Ratio)"
        elif unit_info and unit_info.name and unit_info.name != "Unitless":
            actual_unit_display = unit_info.name
        else:
            actual_unit_display = "N/A"
            
        print(f"  SensorType currently set to: {current_set_type.name}. Reporting values as: {actual_unit_display}")
        ph.setDataInterval(DATA_INTERVAL_MS)
        print(f"  DataInterval set to: {ph.getDataInterval()}ms")
    except PhidgetException as e:
        print(f"  Error during attach configuration: {e.code} - {e.details}")

def on_ir_detach(ph):
    print(f"IR Sensor (Channel {ph.getChannel()}) Detached.")

def on_ir_error(ph, code, description):
    ch_num_str = str(ph.getChannel()) if ph and hasattr(ph, 'getChannel') else 'N/A'
    print(f"Phidget Error on IR Sensor (Channel {ch_num_str}): {code} - {description}")

def read_phidget1101_ir_sensor_on_channel_2():
    ir_sensor = None # Define for access in finally block
    print(f"--- Phidget 1101 IR Sensor Reader (Channel {IR_SENSOR_CHANNEL}) ---")
    print(f"Attempting to read: Raw Voltage Ratio")
    print("This script assumes your Phidget 1101 Adapter is on the channel specified above.")
    print("--------------------------------------------------------------------")

    try:
        ir_sensor = VoltageRatioInput()

        ir_sensor.setOnAttachHandler(on_ir_attach)
        ir_sensor.setOnDetachHandler(on_ir_detach)
        ir_sensor.setOnErrorHandler(on_ir_error)

        if SERIAL_NUMBER != -1:
            ir_sensor.setDeviceSerialNumber(SERIAL_NUMBER)
        ir_sensor.setChannel(IR_SENSOR_CHANNEL)
        ir_sensor.setIsHubPortDevice(False)

        print(f"Opening IR Sensor on channel {IR_SENSOR_CHANNEL}...")
        ir_sensor.openWaitForAttachment(5000)

        if not ir_sensor.getAttached():
            print("Failed to attach to the IR Sensor on the specified channel. Exiting.")
            return

        print(f"\nSuccessfully connected to IR Sensor on channel {IR_SENSOR_CHANNEL}.")
        print("Reading data (Press Ctrl+C to exit)...")
        print("----------------------------------------")

        while True:
            try:
                # Since IR_SENSOR_TYPE_TO_SET is SENSOR_TYPE_VOLTAGERATIO,
                # we should use getVoltageRatio().
                voltage_ratio = ir_sensor.getVoltageRatio()
                print(f"Channel {IR_SENSOR_CHANNEL}: VoltageRatio = {voltage_ratio:.4f}")

                # --- Optional: Manual Distance Calculation ---
                # To get actual distance, you first need to identify the specific
                # Sharp IR sensor model connected to your 1101 adapter.
                # Then, find its formula (usually in 1101_User_Guide.pdf or Sharp datasheet).
                # Most formulas use a "SensorValue" from 0-1000.
                # You can calculate this from the voltage_ratio:
                # sensor_value_for_formula = voltage_ratio * 1000

                # Example for a Sharp GP2Y0A21YK0F (10-80cm), whose formula is:
                # Distance (cm) = 4800 / (SensorValue - 20)
                # Valid for SensorValue range ~80 to ~500.
                #
                # if True: # Set to True to enable this example calculation
                #     sensor_val_calc = voltage_ratio * 1000
                #     # Check if within a plausible operating range for the formula before applying
                #     if sensor_val_calc > 25 and sensor_val_calc < 700: # Broader check
                #         # Avoid division by zero or near-zero if sensor_val_calc is around 20
                #         if (sensor_val_calc - 20) > 0.001: # Check divisor
                #             distance_cm_example = 4800 / (sensor_val_calc - 20)
                #             # Clamp to typical sensor range to avoid extreme values from formula outside its valid input
                #             if 5 < distance_cm_example < 150: # Plausible range for many Sharp sensors
                #                 print(f"    -> Example Distance (GP2Y0A21YK0F formula): {distance_cm_example:.2f} cm")
                #             else:
                #                 print(f"    -> Example Distance (GP2Y0A21YK0F formula): {distance_cm_example:.2f} cm (Note: May be outside sensor's reliable 10-80cm range)")
                #         else:
                #             print(f"    -> SensorValue ({sensor_val_calc:.1f}) too close to 20 for reliable GP2Y0A21YK0F formula.")
                #     else:
                #         print(f"    -> SensorValue ({sensor_val_calc:.1f}) potentially out of typical range for GP2Y0A21YK0F formula.")
                # --- End Optional Distance Calculation ---

            except PhidgetException as e:
                if e.code == ErrorCode.EPHIDGET_UNKNOWNVAL:
                    print(f"Channel {IR_SENSOR_CHANNEL}: Sensor value not yet known, waiting...")
                else:
                    # The onError handler should also catch this.
                    print(f"Runtime PhidgetException on channel {IR_SENSOR_CHANNEL}: {e.code} - {e.details}")
                    # Potentially add a short sleep or break if errors persist
                    time.sleep(1) # Wait a bit if there was an error
            except Exception as ex_loop:
                print(f"Unexpected error in main loop for channel {IR_SENSOR_CHANNEL}: {str(ex_loop)}")
                break # Exit loop on unexpected error
            
            time.sleep(DATA_INTERVAL_MS / 1000.0)

    except PhidgetException as e:
        print(f"Phidget Initialization/Open Exception: {e.code} - {e.details}")
    except KeyboardInterrupt:
        print("\nExiting program due to Ctrl+C.")
    except Exception as ex_main:
        print(f"Unexpected error in main execution: {str(ex_main)}")
    finally:
        if ir_sensor and ir_sensor.getAttached():
            try:
                ir_sensor.close()
                print(f"IR Sensor (Channel {IR_SENSOR_CHANNEL}) closed.")
            except PhidgetException as e_close:
                print(f"Error closing IR sensor: {e_close.details}")
        
        try:
            Phidget.finalize(0)
            print("Phidget library finalized.")
        except PhidgetException as fe:
            print(f"Error finalizing Phidget library: {fe.details}")
        except Exception as e_finalize_gen:
            print(f"Generic error finalizing Phidget library: {str(e_finalize_gen)}")

if __name__ == "__main__":
    # Update SERIAL_NUMBER at the top if you know your InterfaceKit's specific serial number
    read_phidget1101_ir_sensor_on_channel_2()

