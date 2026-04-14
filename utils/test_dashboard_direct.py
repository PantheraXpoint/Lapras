#!/usr/bin/env python3
"""
Direct test to publish to dashboard topic using exact ContextRuleManager format.
"""

import sys
import os
import time
import json

# Add the lapras_middleware to the path
sys.path.append('lapras_middleware')

from lapras_middleware.event import Event, EventMetadata, EntityInfo, MQTTMessage
import paho.mqtt.client as mqtt

def main():
    print("Testing direct dashboard publication...")
    
    # Create MQTT client
    client_id = f"TestDashboard-{int(time.time()*1000)}"
    mqtt_client = mqtt.Client(client_id=client_id)
    
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print("Connected to MQTT broker")
        else:
            print(f"Failed to connect: {rc}")
    
    def on_publish(client, userdata, mid):
        print(f"Message published (ID: {mid})")
    
    mqtt_client.on_connect = on_connect
    mqtt_client.on_publish = on_publish
    
    try:
        # Connect
        mqtt_client.connect("143.248.55.82", 1883)
        mqtt_client.loop_start()
        time.sleep(2)
        
        # Create dashboard state event exactly like ContextRuleManager does
        dashboard_state_event = Event(
            event=EventMetadata(
                id="",  # Auto-generated
                timestamp="",  # Auto-generated
                type="dashboardStateUpdate"
            ),
            source=EntityInfo(
                entityType="contextManager",
                entityId="CM-MainController"
            ),
            payload={
                "agents": {
                    "hue_light": {
                        "state": {
                            "power": "on", 
                            "brightness": 75,
                            "decision_state": "stable"
                        },
                        "agent_type": "light_controller",
                        "sensors": {
                            "infrared_1": "far",
                            "infrared_2": "near"
                        },
                        "timestamp": "2025-06-10T05:35:00",
                        "last_update": time.time(),
                        "is_responsive": True
                    }
                },
                "summary": {
                    "total_agents": 1,
                    "known_agents": ["hue_light"],
                    "last_update": time.time()
                }
            }
        )
        
        # Serialize using MQTTMessage.serialize (same as ContextRuleManager)
        dashboard_message = MQTTMessage.serialize(dashboard_state_event)
        
        print("Publishing dashboard state update...")
        print(f"Message size: {len(dashboard_message)} bytes")
        
        # Publish to same topic as ContextRuleManager
        result = mqtt_client.publish("dashboard/context/state", dashboard_message, qos=1)
        print(f"Publish result: {result}")
        
        # Wait for delivery
        time.sleep(3)
        print("Test complete!")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

if __name__ == "__main__":
    main() 