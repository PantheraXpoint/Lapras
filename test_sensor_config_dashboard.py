#!/usr/bin/env python3
"""
Interactive Dashboard Program for sensor configuration commands.
This program provides a terminal interface to send sensor configuration commands to virtual agents.
"""

import json
import logging
import sys
import time
import os
import threading
from typing import Dict, Any, Optional

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import paho.mqtt.client as mqtt
from lapras_middleware.event import EventFactory, MQTTMessage, TopicManager

logger = logging.getLogger(__name__)

class InteractiveSensorConfigDashboard:
    def __init__(self, mqtt_broker="143.248.55.82", mqtt_port=1883):
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.running = True
        self.pending_commands = {}  # Track pending commands for responses
        self.response_received = threading.Event()
        self.last_response = None
        
        # Set up MQTT client
        self.mqtt_client = mqtt.Client(client_id="InteractiveSensorConfigDashboard", clean_session=True)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        
        # Connect to MQTT broker
        try:
            self.mqtt_client.connect(mqtt_broker, mqtt_port)
            self.mqtt_client.loop_start()
            logger.info(f"Connected to MQTT broker at {mqtt_broker}:{mqtt_port}")
            time.sleep(1)  # Wait for connection
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            raise
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            logger.info("Successfully connected to MQTT broker")
            # Subscribe to sensor config results
            result_topic = TopicManager.dashboard_sensor_config_result()
            client.subscribe(result_topic, qos=1)
            logger.info(f"Subscribed to results topic: {result_topic}")
        else:
            logger.error(f"Failed to connect to MQTT broker. Result code: {rc}")
    
    def _on_message(self, client, userdata, msg):
        """Callback when message is received."""
        try:
            if msg.topic == TopicManager.dashboard_sensor_config_result():
                event = MQTTMessage.deserialize(msg.payload.decode())
                payload = event.payload
                
                # Store the response and signal that it was received
                self.last_response = payload
                self.response_received.set()
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def send_sensor_config_command(self, agent_id: str, action: str, sensor_config: dict = None) -> bool:
        """Send a sensor configuration command and wait for response."""
        try:
            # Reset response event
            self.response_received.clear()
            self.last_response = None
            
            # Create sensor config event
            config_event = EventFactory.create_sensor_config_event(
                target_agent_id=agent_id,
                action=action,
                sensor_config=sensor_config or {},
                source_entity_id="InteractiveDashboard"
            )
            
            # Send to context manager
            command_topic = TopicManager.dashboard_sensor_config_command()
            command_message = MQTTMessage.serialize(config_event)
            self.mqtt_client.publish(command_topic, command_message, qos=1)
            
            print(f"\n📤 Sending command to agent '{agent_id}': {action}")
            if sensor_config:
                print(f"   Sensor config: {sensor_config}")
            print("   Waiting for response...")
            
            # Wait for response (timeout after 10 seconds)
            if self.response_received.wait(timeout=10):
                return self._display_response()
            else:
                print("❌ Timeout: No response received within 10 seconds")
                return False
            
        except Exception as e:
            logger.error(f"Error sending sensor config command: {e}")
            print(f"❌ Error sending command: {e}")
            return False
    
    def _display_response(self) -> bool:
        """Display the response from the virtual agent."""
        if not self.last_response:
            print("❌ No response data available")
            return False
        
        success = self.last_response.get('success', False)
        message = self.last_response.get('message', 'No message')
        current_sensors = self.last_response.get('current_sensors', [])
        
        print(f"\n📥 Response received:")
        if success:
            print(f"✅ Success: {message}")
            if current_sensors:
                print(f"   Current sensors: {', '.join(current_sensors)}")
        else:
            print(f"❌ Failed: {message}")
        
        return success
    
    def show_main_menu(self):
        """Display the main menu options."""
        print("\n" + "="*60)
        print("🔧 SENSOR CONFIGURATION DASHBOARD")
        print("="*60)
        print("1. 📋 List current sensor configuration")
        print("2. 🔄 Configure sensors (replace all)")
        print("3. ➕ Add sensors to existing configuration")
        print("4. ➖ Remove sensors from configuration")
        print("5. ❓ Help - Show examples")
        print("6. 🚪 Quit")
        print("="*60)
    
    def get_user_choice(self) -> str:
        """Get user's menu choice."""
        while True:
            try:
                choice = input("\nEnter your choice (1-6): ").strip()
                if choice in ['1', '2', '3', '4', '5', '6']:
                    return choice
                else:
                    print("❌ Invalid choice. Please enter a number between 1-6.")
            except (EOFError, KeyboardInterrupt):
                return '6'  # Quit on Ctrl+C or EOF
    
    def get_agent_id(self) -> Optional[str]:
        """Get target agent ID from user."""
        while True:
            try:
                agent_id = input("\nEnter target agent ID (e.g., 'hue_light', 'dashboard'): ").strip()
                if agent_id:
                    return agent_id
                else:
                    print("❌ Agent ID cannot be empty. Please try again.")
            except (EOFError, KeyboardInterrupt):
                return None
    
    def get_sensor_config(self, action_name: str) -> Optional[Dict[str, list]]:
        """Get sensor configuration from user."""
        print(f"\n📝 Enter sensor configuration for {action_name}:")
        print("Format: sensor_type:sensor1,sensor2,sensor3")
        print("Example: infrared:infrared_1,infrared_2 motion:motion_01,motion_02")
        print("Enter multiple sensor types on separate lines, or press Enter when done.")
        print("Type 'cancel' to cancel this operation.")
        
        sensor_config = {}
        
        while True:
            try:
                line = input("Sensor config: ").strip()
                
                if line.lower() == 'cancel':
                    return None
                
                if not line:  # Empty line, done entering
                    break
                
                if ':' not in line:
                    print("❌ Invalid format. Use: sensor_type:sensor1,sensor2")
                    continue
                
                sensor_type, sensors_str = line.split(':', 1)
                sensor_type = sensor_type.strip()
                sensor_ids = [s.strip() for s in sensors_str.split(',') if s.strip()]
                
                if not sensor_type or not sensor_ids:
                    print("❌ Invalid format. Both sensor type and sensor IDs are required.")
                    continue
                
                sensor_config[sensor_type] = sensor_ids
                print(f"✅ Added {sensor_type}: {sensor_ids}")
                
            except (EOFError, KeyboardInterrupt):
                return None
        
        if not sensor_config:
            print("⚠️  No sensor configuration provided.")
            return {}
        
        return sensor_config
    
    def show_help(self):
        """Show help and examples."""
        print("\n" + "="*60)
        print("📖 HELP - SENSOR CONFIGURATION EXAMPLES")
        print("="*60)
        
        print("\n🏷️  Common Agent IDs:")
        print("   • hue_light     - Hue light controller")
        print("   • dashboard     - Dashboard monitoring agent")
        print("   • aircon        - Air conditioning controller")
        
        print("\n🔧 Common Sensor Types:")
        print("   • infrared      - Infrared proximity sensors")
        print("   • motion        - Motion detection sensors")
        print("   • activity      - Activity monitoring sensors")
        print("   • temperature   - Temperature sensors")
        print("   • door          - Door sensors")
        print("   • light         - Light sensors")
        
        print("\n📝 Sensor Configuration Examples:")
        print("   Single type:")
        print("     infrared:infrared_1,infrared_2")
        print("   ")
        print("   Multiple types:")
        print("     infrared:infrared_1,infrared_2")
        print("     motion:motion_01,motion_02,motion_03")
        print("     activity:activity_s1b,activity_s2a")
        
        print("\n🔄 Operation Types:")
        print("   • List: Shows current sensor configuration")
        print("   • Configure: Replaces ALL sensors with new configuration")
        print("   • Add: Adds new sensors to existing configuration")
        print("   • Remove: Removes specific sensors from configuration")
        
        input("\nPress Enter to continue...")
    
    def handle_list_sensors(self):
        """Handle listing current sensors."""
        agent_id = self.get_agent_id()
        if not agent_id:
            return
        
        print(f"\n📋 Listing sensors for agent: {agent_id}")
        self.send_sensor_config_command(agent_id, "list")
    
    def handle_configure_sensors(self):
        """Handle configuring (replacing) sensors."""
        agent_id = self.get_agent_id()
        if not agent_id:
            return
        
        print(f"\n⚠️  This will REPLACE ALL current sensors for agent: {agent_id}")
        confirm = input("Are you sure? (y/N): ").strip().lower()
        if confirm != 'y':
            print("Operation cancelled.")
            return
        
        sensor_config = self.get_sensor_config("CONFIGURE")
        if sensor_config is None:
            print("Operation cancelled.")
            return
        
        print(f"\n🔄 Configuring sensors for agent: {agent_id}")
        self.send_sensor_config_command(agent_id, "configure", sensor_config)
    
    def handle_add_sensors(self):
        """Handle adding sensors."""
        agent_id = self.get_agent_id()
        if not agent_id:
            return
        
        sensor_config = self.get_sensor_config("ADD")
        if sensor_config is None:
            print("Operation cancelled.")
            return
        
        print(f"\n➕ Adding sensors to agent: {agent_id}")
        self.send_sensor_config_command(agent_id, "add", sensor_config)
    
    def handle_remove_sensors(self):
        """Handle removing sensors."""
        agent_id = self.get_agent_id()
        if not agent_id:
            return
        
        sensor_config = self.get_sensor_config("REMOVE")
        if sensor_config is None:
            print("Operation cancelled.")
            return
        
        print(f"\n➖ Removing sensors from agent: {agent_id}")
        self.send_sensor_config_command(agent_id, "remove", sensor_config)
    
    def run(self):
        """Run the interactive dashboard."""
        print("🚀 Starting Interactive Sensor Configuration Dashboard...")
        print("💡 Make sure the Context Rule Manager and target virtual agents are running!")
        
        while self.running:
            try:
                self.show_main_menu()
                choice = self.get_user_choice()
                
                if choice == '1':
                    self.handle_list_sensors()
                elif choice == '2':
                    self.handle_configure_sensors()
                elif choice == '3':
                    self.handle_add_sensors()
                elif choice == '4':
                    self.handle_remove_sensors()
                elif choice == '5':
                    self.show_help()
                elif choice == '6':
                    self.running = False
                    
            except KeyboardInterrupt:
                print("\n\n🛑 Interrupted by user")
                self.running = False
            except Exception as e:
                print(f"\n❌ Error: {e}")
                logger.error(f"Error in main loop: {e}")
        
        print("\n👋 Goodbye!")
    
    def stop(self):
        """Stop the dashboard."""
        self.running = False
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        logger.info("Dashboard stopped")

def main():
    # Set up logging
    logging.basicConfig(
        level=logging.WARNING,  # Reduce log noise in interactive mode
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    try:
        # Create and run interactive dashboard
        dashboard = InteractiveSensorConfigDashboard()
        dashboard.run()
        dashboard.stop()
        return 0
        
    except Exception as e:
        print(f"❌ Error starting dashboard: {e}")
        logger.error(f"Error in main: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main()) 