import time
import math
from Phidget22.Phidget import Phidget
from Phidget22.PhidgetException import PhidgetException
from Phidget22.Devices.VoltageRatioInput import VoltageRatioInput
from Phidget22.VoltageRatioSensorType import VoltageRatioSensorType
from Phidget22.Unit import Unit # Import the Unit enum

# Configuration
CHANNEL_FOR_LIGHT_SENSOR = 2  # Your script was using channel 2, keep if correct
SERIAL_NUMBER = 455869 # Your script showed serial 455869
# Use -1 if you want to connect to any Phidget, or set specific serial.

def on_attach(ph):
    print(f"Light Sensor Attached! Serial: {ph.getDeviceSerialNumber()}, Channel: {ph.getChannel()}")
    try:
        ph.setSensorType(VoltageRatioSensorType.SENSOR_TYPE_1105)
        print(f"SensorType set to SENSOR_TYPE_1105 for channel {ph.getChannel()}")
        
        # Set data interval (e.g., every 500ms, your log showed ~504ms)
        ph.setDataInterval(500) 
        print(f"DataInterval: {ph.getDataInterval()}ms")
        
    except PhidgetException as e:
        print(f"Phidget Exception on Attach: {e.code} - {e.details}")

def on_detach(ph):
    print(f"Light Sensor Detached! Serial: {ph.getDeviceSerialNumber()}, Channel: {ph.getChannel()}")

def on_error(ph, code, description):
    print(f"Phidget Error {code}: {description}")

def main_light_sensor():
    light_sensor = None # Define here for access in finally
    try:
        light_sensor = VoltageRatioInput()

        light_sensor.setOnAttachHandler(on_attach)
        light_sensor.setOnDetachHandler(on_detach)
        light_sensor.setOnErrorHandler(on_error)

        if SERIAL_NUMBER != -1:
            light_sensor.setDeviceSerialNumber(SERIAL_NUMBER)
        light_sensor.setChannel(CHANNEL_FOR_LIGHT_SENSOR)

        print("Opening Light Sensor channel...")
        light_sensor.openWaitForAttachment(5000) # Wait 5 seconds for attachment

        if not light_sensor.getAttached():
            print("Failed to attach to Light Sensor. Exiting.")
            return

        print("Reading data from Light Sensor (Press Ctrl+C to exit)...")
        while True:
            try:
                # This value is 'illuminance' if unit is Lux, otherwise likely ratiometric
                raw_value = light_sensor.getSensorValue() 
                sensor_unit_info = light_sensor.getSensorUnit()
                
                # Check if the unit is Lux
                if sensor_unit_info and sensor_unit_info.unit == Unit.PHIDUNIT_LUX:
                    print(f"Illuminance: {raw_value:.2f} {sensor_unit_info.name}")
                # Check if the unit is PHIDUNIT_NONE (Unitless)
                elif sensor_unit_info and sensor_unit_info.unit == Unit.PHIDUNIT_NONE:
                    # Assume the value is ratiometric (0.0 to 1.0) as per your output
                    # Scale to 0-1000 range for a generic brightness indicator
                    brightness_0_1000 = raw_value * 1000
                    unit_name_str = sensor_unit_info.name if sensor_unit_info.name else "none"
                    print(f"Brightness (0-1000 scale): {brightness_0_1000:.1f} (Raw: {raw_value:.4f}, Unit: {unit_name_str})")
                else:
                    # Fallback for other unexpected units or if sensor_unit_info is None
                    unit_name_str = sensor_unit_info.name if sensor_unit_info and sensor_unit_info.name else "Unknown"
                    print(f"Sensor Value: {raw_value:.4f} (Unit: {unit_name_str})")

            except PhidgetException as e:
                if e.code == PhidgetException.EPHIDGET_UNKNOWNVAL:
                    print("Sensor value not yet known, waiting...")
                else:
                    print(f"Phidget runtime exception: {e.code} - {e.details}")
            time.sleep(0.5) # Reading interval based on your data interval

    except PhidgetException as e:
        print(f"Phidget Initialization/Open Exception: {e.code} - {e.details}")
    except KeyboardInterrupt:
        print("\nExiting program.")
    finally:
        if light_sensor and light_sensor.getAttached():
            light_sensor.close()
            print("Light Sensor channel closed.")
        # else:
            # print("Light Sensor channel was not attached or already closed.")
        
        # Finalize the Phidget library. Moved outside the specific sensor's try/finally 
        # if multiple scripts might run or for general cleanup.
        # However, for a single script, it's fine here too.
        try:
            Phidget.finalize(0)
            print("Phidget library finalized.")
        except PhidgetException as fe:
            print(f"Error finalizing Phidget library: {fe.details}")


if __name__ == "__main__":
    # You can set the channel and serial number directly here if you prefer
    CHANNEL_FOR_LIGHT_SENSOR = 2
    SERIAL_NUMBER = 455869 
    if CHANNEL_FOR_LIGHT_SENSOR == "YOUR_CHANNEL_NUMBER_HERE": # Default placeholder check
        print("Please set 'CHANNEL_FOR_LIGHT_SENSOR' in the script if not hardcoded above.")
    else:
        main_light_sensor()