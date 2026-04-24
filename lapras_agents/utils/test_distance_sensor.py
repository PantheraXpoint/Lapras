#!/usr/bin/env python3
"""
Multi-Channel Distance Sensor Diagnostic Tool for Phidgets VINT Hub
Discovers and diagnoses connection issues with distance sensors.
"""

import time
import sys
from datetime import datetime
from Phidget22.Phidget import *
from Phidget22.Devices.DistanceSensor import *

# Configuration
HUB_SERIAL_NUMBER = 745871  # Your hub serial number
HUB_PORTS = [0, 1, 2, 3, 4, 5]  # All available hub ports to scan
DATA_INTERVAL_MS = 250  # Data reading interval in milliseconds

class MultiChannelSensorReader:
    def __init__(self):
        """Initialize the multi-channel sensor reader"""
        self.sensors = {}  # hub_port -> sensor object
        self.sensor_types = {}  # hub_port -> sensor type string
        
    def discover_sensors(self):
        """Discover all connected distance sensors on VINT hub ports"""
        print(f"Discovering sensors on hub {HUB_SERIAL_NUMBER}...")
        discovered_ports = []
        
        # Try each hub port for distance sensors
        for hub_port in HUB_PORTS:
            sensor = None
            try:
                sensor = DistanceSensor()
                
                # For VINT distance sensors, connect via hub port
                sensor.setDeviceSerialNumber(HUB_SERIAL_NUMBER)
                sensor.setHubPort(hub_port)
                sensor.setChannel(0)
                sensor.setIsHubPortDevice(False)  # VINT devices are not hub port devices
                
                # Try to open with timeout
                sensor.openWaitForAttachment(1000)  # Reduced timeout for faster discovery
                
                if sensor.getAttached():
                    print(f"✓ Found Distance Sensor on Hub Port {hub_port}")
                    
                    # Get sensor details
                    try:
                        device_name = sensor.getDeviceName()
                        device_serial = sensor.getDeviceSerialNumber()
                        print(f"  Device: {device_name} (Serial: {device_serial})")
                    except:
                        print(f"  Device: Could not get device details")
                    
                    discovered_ports.append(hub_port)
                    
                    # Configure the sensor with more aggressive settings
                    sensor.setDataInterval(DATA_INTERVAL_MS)
                    sensor.setDistanceChangeTrigger(0)  # Continuous readings
                    
                    # Quick sensor initialization check
                    try:
                        test_distance = sensor.getDistance()
                        print(f"  Initial reading: {test_distance/10:.1f} cm")
                    except:
                        print(f"  Sensor on port {hub_port} will initialize during streaming")
                    
                    self.sensors[hub_port] = sensor
                    self.sensor_types[hub_port] = "DistanceSensor"
                else:
                    sensor.close()
                    print(f"  Hub Port {hub_port}: No sensor attached")
                    
            except PhidgetException as e:
                if sensor:
                    try:
                        sensor.close()
                    except:
                        pass
                print(f"  Hub Port {hub_port}: No distance sensor ({e.details})")
            except Exception as e:
                if sensor:
                    try:
                        sensor.close()
                    except:
                        pass
                print(f"  Hub Port {hub_port}: Unexpected error - {str(e)}")
                        
        print(f"Discovery complete. Found sensors on hub ports: {discovered_ports}")
        return discovered_ports
    
    def test_individual_sensors(self, discovered_ports):
        """Test each sensor individually to diagnose issues"""
        print("\n=== Individual Sensor Testing ===")
        
        for hub_port in sorted(discovered_ports):
            if hub_port in self.sensors:
                sensor = self.sensors[hub_port]
                print(f"\nTesting Hub Port {hub_port}:")
                
                # Test multiple readings
                successful_readings = 0
                error_count = 0
                readings = []
                
                for i in range(10):
                    try:
                        distance = sensor.getDistance()
                        readings.append(distance)
                        successful_readings += 1
                        print(f"  Reading {i+1}: {distance/10:.1f} cm")
                    except Exception as e:
                        error_count += 1
                        if "0x33" not in str(e):
                            print(f"  Reading {i+1}: ERROR - {str(e)}")
                        else:
                            print(f"  Reading {i+1}: Initializing...")
                
                print(f"  Success rate: {successful_readings}/10")
                if readings:
                    avg_distance = sum(readings) / len(readings)
                    print(f"  Average distance: {avg_distance/10:.1f} cm")
    
    def log_sensor_data(self, discovered_ports):
        """Continuously log data from all discovered sensors"""
        print("\n=== Starting Continuous Data Logging ===")
        print(f"Data interval: {DATA_INTERVAL_MS}ms")
        print("Press Ctrl+C to stop")
        print("-" * 80)
        
        try:
            reading_count = 0
            while True:
                reading_count += 1
                timestamp = datetime.now().strftime("%H:%M:%S")
                
                # Create a single line showing all sensors
                sensor_readings = []
                for hub_port in sorted(discovered_ports):
                    if hub_port in self.sensors:
                        try:
                            distance_value = self.sensors[hub_port].getDistance()
                            
                            # Check if distance reading is valid
                            if distance_value < 0 or distance_value > 1300:
                                distance_str = "130cm"  # Max range when out of range
                            else:
                                distance_str = f"{distance_value/10:.0f}cm"
                            
                            sensor_readings.append(f"P{hub_port}:{distance_str}")
                        except Exception as e:
                            # More detailed error reporting
                            error_msg = str(e)
                            if "0x33" in error_msg or "Unknown or Invalid Value" in error_msg:
                                sensor_readings.append(f"P{hub_port}:130cm")
                            elif "timeout" in error_msg.lower():
                                sensor_readings.append(f"P{hub_port}:TIMEOUT")
                            elif "not attached" in error_msg.lower():
                                sensor_readings.append(f"P{hub_port}:DETACHED")
                            else:
                                sensor_readings.append(f"P{hub_port}:ERR")
                
                # Print all sensors on one line
                readings_line = " | ".join(sensor_readings)
                print(f"[{timestamp}] #{reading_count:04d} - {readings_line}")
                
                time.sleep(DATA_INTERVAL_MS / 1000.0)
                
        except KeyboardInterrupt:
            print("\nStopping data logging...")
        except Exception as e:
            print(f"\nUnexpected error in main loop: {str(e)}")
    
    def cleanup(self):
        """Clean up all sensor connections"""
        print("Cleaning up sensors...")
        for hub_port, sensor in self.sensors.items():
            try:
                sensor.close()
                print(f"  Hub Port {hub_port}: Closed")
            except Exception as e:
                print(f"  Hub Port {hub_port}: Error closing - {str(e)}")
        
        # Finalize Phidget library
        try:
            Phidget.finalize(0)
            print("Phidget library finalized.")
        except Exception as e:
            print(f"Error finalizing Phidget library: {str(e)}")

def main():
    """Main function to run the multi-channel sensor reader"""
    print("=== Multi-Channel Distance Sensor Diagnostic Tool ===")
    print(f"Hub Serial Number: {HUB_SERIAL_NUMBER}")
    print(f"Scanning all {len(HUB_PORTS)} hub ports {HUB_PORTS} for distance sensors...")
    print("=" * 60)
    
    reader = MultiChannelSensorReader()
    
    try:
        # Discover all available sensors
        discovered_ports = reader.discover_sensors()
        
        if not discovered_ports:
            print("No distance sensors found. Please check connections and try again.")
            return
        
        # Test individual sensors
        reader.test_individual_sensors(discovered_ports)
        
        # Ask user if they want to continue with streaming
        print("\n" + "="*60)
        choice = input("Do you want to start continuous streaming? (y/n): ").lower().strip()
        
        if choice == 'y' or choice == 'yes':
            # Start logging data from discovered sensors
            reader.log_sensor_data(discovered_ports)
        else:
            print("Skipping continuous streaming.")
        
    except Exception as e:
        print(f"Error in main: {str(e)}")
    finally:
        reader.cleanup()

if __name__ == "__main__":
    main()