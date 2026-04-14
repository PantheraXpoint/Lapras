#!/usr/bin/env python3
"""
Verification script to monitor all sensor topics and display available sensor IDs.
This helps verify that all sensor agents are publishing data correctly.
"""

import paho.mqtt.client as mqtt
import json
import time
import sys
import argparse
from collections import defaultdict
from datetime import datetime

class SensorTopicMonitor:
    def __init__(self, mqtt_broker="143.248.55.82", mqtt_port=1883):
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.sensor_data = defaultdict(lambda: {"last_seen": None, "count": 0, "latest_value": None})
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"✓ Connected to MQTT broker {self.mqtt_broker}:{self.mqtt_port}")
            # Subscribe to all sensor topics
            client.subscribe("sensor/+/readSensor")
            print("📡 Monitoring all sensor topics: sensor/+/readSensor")
            print("=" * 80)
        else:
            print(f"✗ Failed to connect to MQTT broker (code: {rc})")
    
    def on_message(self, client, userdata, msg):
        try:
            # Parse the topic to extract sensor ID
            topic_parts = msg.topic.split('/')
            if len(topic_parts) >= 2:
                sensor_id = topic_parts[1]
                
                # Parse the message
                data = json.loads(msg.payload.decode())
                
                # Extract sensor information
                sensor_type = data.get('payload', {}).get('sensor_type', 'unknown')
                value = data.get('payload', {}).get('value', 'N/A')
                unit = data.get('payload', {}).get('unit', '')
                metadata = data.get('payload', {}).get('metadata', {})
                
                # Update tracking
                self.sensor_data[sensor_id]["last_seen"] = datetime.now()
                self.sensor_data[sensor_id]["count"] += 1
                self.sensor_data[sensor_id]["latest_value"] = f"{value} {unit}".strip()
                self.sensor_data[sensor_id]["sensor_type"] = sensor_type
                
                # Extract status information based on sensor type
                status_info = "N/A"
                if sensor_type == "infrared":
                    status_info = metadata.get('proximity_status', 'N/A')
                elif sensor_type == "motion":
                    status_info = metadata.get('motion_status', 'N/A')
                elif sensor_type == "activity":
                    status_info = metadata.get('activity_status', 'N/A')
                elif sensor_type == "temperature":
                    status_info = metadata.get('temperature_status', 'N/A')
                elif sensor_type == "door":
                    status_info = metadata.get('door_status', 'N/A')
                
                self.sensor_data[sensor_id]["status"] = status_info
                
                # Print real-time update
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] {sensor_id:15} | {sensor_type:10} | {value:8} {unit:5} | {status_info:10} | #{self.sensor_data[sensor_id]['count']}")
                
        except Exception as e:
            print(f"Error processing message from {msg.topic}: {e}")
    
    def display_summary(self):
        """Display a summary of all discovered sensors."""
        print("\n" + "=" * 80)
        print("📊 SENSOR DISCOVERY SUMMARY")
        print("=" * 80)
        
        if not self.sensor_data:
            print("❌ No sensors discovered yet. Make sure sensor agents are running.")
            return
        
        # Group by sensor type
        by_type = defaultdict(list)
        for sensor_id, info in self.sensor_data.items():
            sensor_type = info.get('sensor_type', 'unknown')
            by_type[sensor_type].append(sensor_id)
        
        total_sensors = len(self.sensor_data)
        print(f"🔍 Discovered {total_sensors} active sensors:")
        print()
        
        for sensor_type, sensor_ids in sorted(by_type.items()):
            print(f"🔸 {sensor_type.upper()} SENSORS ({len(sensor_ids)} found):")
            for sensor_id in sorted(sensor_ids):
                info = self.sensor_data[sensor_id]
                last_seen = info['last_seen'].strftime("%H:%M:%S") if info['last_seen'] else "Never"
                status = info.get('status', 'N/A')
                value = info.get('latest_value', 'N/A')
                count = info['count']
                print(f"   • {sensor_id:20} | Last: {last_seen} | Status: {status:10} | Value: {value:15} | Messages: {count}")
            print()
        
        # Configuration examples
        print("🔧 CONFIGURATION EXAMPLES:")
        print("=" * 40)
        
        for sensor_type, sensor_ids in sorted(by_type.items()):
            if len(sensor_ids) > 0:
                # Show example with first few sensors
                example_sensors = sensor_ids[:3]
                example_str = ",".join(example_sensors)
                print(f"# Use {sensor_type} sensors:")
                print(f"python start_hue_light_agent.py --sensors \"{sensor_type}:{example_str}\"")
                print()
    
    def run(self, duration=None):
        """Run the monitor for a specified duration or indefinitely."""
        try:
            print("🔍 Sensor Topic Monitor")
            print(f"🎯 Target: {self.mqtt_broker}:{self.mqtt_port}")
            print(f"⏱️  Duration: {'Indefinite' if duration is None else f'{duration} seconds'}")
            print()
            
            self.client.connect(self.mqtt_broker, self.mqtt_port, 60)
            
            if duration:
                # Run for specified duration
                self.client.loop_start()
                time.sleep(duration)
                self.client.loop_stop()
            else:
                # Run indefinitely
                self.client.loop_forever()
                
        except KeyboardInterrupt:
            print("\n🛑 Monitoring stopped by user")
        except Exception as e:
            print(f"❌ Error: {e}")
        finally:
            self.display_summary()
            self.client.disconnect()

def main():
    parser = argparse.ArgumentParser(description='Monitor sensor MQTT topics to verify publishing')
    parser.add_argument('--mqtt-broker', default="143.248.55.82", help='MQTT broker address')
    parser.add_argument('--mqtt-port', type=int, default=1883, help='MQTT broker port')
    parser.add_argument('--duration', type=int, help='Monitor duration in seconds (default: indefinite)')
    parser.add_argument('--summary-only', action='store_true', help='Show summary after monitoring')
    
    args = parser.parse_args()
    
    monitor = SensorTopicMonitor(args.mqtt_broker, args.mqtt_port)
    monitor.run(args.duration)

if __name__ == "__main__":
    main() 