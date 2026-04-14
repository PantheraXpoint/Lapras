#!/usr/bin/env python3
"""
Monitor rule changes by listening to MQTT rule management result messages
"""

import json
import time
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import paho.mqtt.client as mqtt
from lapras_middleware.event import MQTTMessage, TopicManager
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class RulesMonitor:
    """Monitor rule management commands and results."""
    
    def __init__(self, mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        """Initialize the rules monitor."""
        self.client_id = "RulesMonitor"
        self.mqtt_client = mqtt.Client(client_id=self.client_id)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.connect(mqtt_broker, mqtt_port)
        logger.info(f"[{self.client_id}] Connected to MQTT broker")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            # Subscribe to rules management topics
            command_topic = TopicManager.rules_management_command()
            result_topic = TopicManager.rules_management_result()
            
            client.subscribe(command_topic, qos=1)
            client.subscribe(result_topic, qos=1)
            
            print(f"🎧 Monitoring rule management on:")
            print(f"   📤 Commands: {command_topic}")
            print(f"   📥 Results:  {result_topic}")
            print("=" * 60)
            logger.info(f"[{self.client_id}] Subscribed to rule management topics")
        else:
            logger.error(f"[{self.client_id}] Failed to connect. Result code: {rc}")
    
    def _on_message(self, client, userdata, msg):
        """Callback when message is received."""
        try:
            topic = msg.topic
            
            # Parse the event
            event = MQTTMessage.deserialize(msg.payload.decode())
            
            current_time = time.strftime('%H:%M:%S')
            
            if "command" in topic:
                # This is a rules command
                action = event.payload.get('action', 'unknown')
                rule_files = event.payload.get('rule_files', [])
                preset_name = event.payload.get('preset_name')
                source = event.source.entityId
                
                print(f"\n📤 [{current_time}] COMMAND from {source}")
                print(f"   🎯 Action: {action}")
                if preset_name:
                    print(f"   📋 Preset: {preset_name}")
                if rule_files:
                    print(f"   📄 Files: {rule_files}")
                print(f"   🔗 Event ID: {event.event.id}")
                
            elif "result" in topic:
                # This is a rules command result
                success = event.payload.get('success', False)
                message = event.payload.get('message', 'No message')
                action = event.payload.get('action', 'unknown')
                loaded_files = event.payload.get('loaded_files', [])
                
                status_icon = "✅" if success else "❌"
                print(f"\n📥 [{current_time}] RESULT")
                print(f"   {status_icon} Status: {'SUCCESS' if success else 'FAILED'}")
                print(f"   🎯 Action: {action}")
                print(f"   💬 Message: {message}")
                if loaded_files:
                    print(f"   📚 Loaded: {loaded_files}")
                print(f"   🔗 Command ID: {event.payload.get('command_id', 'unknown')}")
                
        except Exception as e:
            logger.error(f"[{self.client_id}] Error processing message: {e}")
            print(f"❌ Error processing message from {msg.topic}: {e}")
    
    def start_monitoring(self):
        """Start monitoring rule changes."""
        print("🚀 Starting Rules Monitor...")
        print("Press Ctrl+C to stop monitoring")
        
        try:
            self.mqtt_client.loop_forever()
        except KeyboardInterrupt:
            print("\n⏹️ Monitoring stopped by user")
        finally:
            self.mqtt_client.disconnect()
            print("🛑 Monitor disconnected")

if __name__ == "__main__":
    monitor = RulesMonitor()
    monitor.start_monitoring() 