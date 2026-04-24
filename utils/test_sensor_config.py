#!/usr/bin/env python3
"""
Test script for dynamic sensor configuration via MQTT events.
This script allows you to send sensor configuration commands to virtual agents.
"""

import json
import sys
import os
import argparse
import paho.mqtt.client as mqtt
import time

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lapras_middleware.event import EventFactory, MQTTMessage

def send_sensor_config_command(agent_id, action, sensor_config=None, mqtt_broker="143.248.55.82", mqtt_port=1883):
    """Send a sensor configuration command to a virtual agent."""
    
    # Create the event
    event = EventFactory.create_sensor_config_event(
        target_agent_id=agent_id,
        action=action,
        sensor_config=sensor_config,
        source_entity_id="TestScript"
    )
    
    # Serialize to MQTT message
    message = MQTTMessage.serialize(event)
    
    # Send via MQTT
    topic = f"agent/{agent_id}/sensorConfig"
    
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print(f"✓ Connected to MQTT broker")
            client.publish(topic, message)
            print(f"✓ Sent {action} command to {agent_id}")
            print(f"  Topic: {topic}")
            if sensor_config:
                print(f"  Config: {sensor_config}")
            time.sleep(1)  # Give time for message to be sent
            client.disconnect()
        else:
            print(f"✗ Failed to connect to MQTT broker (code: {rc})")
    
    def on_publish(client, userdata, mid):
        print(f"✓ Message published (mid: {mid})")
    
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_publish = on_publish
    
    try:
        print(f"Connecting to MQTT broker {mqtt_broker}:{mqtt_port}...")
        client.connect(mqtt_broker, mqtt_port, 60)
        client.loop_forever()
    except Exception as e:
        print(f"✗ Error: {e}")

def main():
    parser = argparse.ArgumentParser(description='Send sensor configuration commands to virtual agents')
    parser.add_argument('--agent-id', required=True, help='Target agent ID (e.g., hue_light)')
    parser.add_argument('--action', required=True, choices=['configure', 'add', 'remove', 'list'],
                       help='Configuration action to perform')
    parser.add_argument('--sensors', nargs='*',
                       help='Sensor configuration. Format: sensor_type:sensor1,sensor2')
    parser.add_argument('--mqtt-broker', default="143.248.55.82",
                       help='MQTT broker address')
    parser.add_argument('--mqtt-port', type=int, default=1883,
                       help='MQTT broker port')
    
    args = parser.parse_args()
    
    # Parse sensor configuration
    sensor_config = {}
    if args.sensors:
        for sensor_spec in args.sensors:
            if ':' in sensor_spec:
                sensor_type, sensor_list = sensor_spec.split(':', 1)
                sensor_ids = [s.strip() for s in sensor_list.split(',') if s.strip()]
                if sensor_ids:
                    sensor_config[sensor_type] = sensor_ids
            else:
                print(f"Warning: Invalid sensor format: {sensor_spec}")
    
    # For 'list' action, we don't need sensor config
    if args.action == 'list':
        sensor_config = None
    elif args.action != 'list' and not sensor_config:
        print("Error: Sensor configuration required for this action")
        return
    
    print(f"🔧 Sending sensor configuration command:")
    print(f"   Agent: {args.agent_id}")
    print(f"   Action: {args.action}")
    if sensor_config:
        print(f"   Config: {sensor_config}")
    print()
    
    send_sensor_config_command(
        agent_id=args.agent_id,
        action=args.action,
        sensor_config=sensor_config,
        mqtt_broker=args.mqtt_broker,
        mqtt_port=args.mqtt_port
    )

if __name__ == "__main__":
    # Example usage:
    print("Dynamic Sensor Configuration Test Script")
    print("=" * 50)
    print()
    print("Examples:")
    print("  # Configure hue_light to use motion sensors")
    print("  python test_sensor_config.py --agent-id hue_light --action configure --sensors motion:motion_01,motion_02,motion_03")
    print()
    print("  # Add activity sensors to existing configuration")
    print("  python test_sensor_config.py --agent-id hue_light --action add --sensors activity:activity_s1b,activity_s2a")
    print()
    print("  # List current sensor configuration")
    print("  python test_sensor_config.py --agent-id hue_light --action list")
    print()
    print("  # Configure with multiple sensor types")
    print("  python test_sensor_config.py --agent-id hue_light --action configure --sensors infrared:infrared_1,infrared_2 motion:motion_01,motion_02 activity:activity_s1b")
    print()
    
    main() 