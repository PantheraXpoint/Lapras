#!/usr/bin/env python3
"""
Test script for Rule Agent - demonstrates dashboard rule management via rule agent
Enhanced with automated and interactive testing modes
"""

import time
import json
import sys
import os
import uuid
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import paho.mqtt.client as mqtt
from lapras_middleware.event import EventFactory, MQTTMessage, TopicManager, Event, EventMetadata, EntityInfo
import logging

# Set up logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class RuleAgentTester:
    """Enhanced test client for the Rule Agent service."""
    
    def __init__(self, mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        """Initialize the tester."""
        # Use unique client ID to prevent conflicts
        self.client_id = f"RuleAgentTester_{int(time.time())}_{str(uuid.uuid4())[:8]}"
        
        self.mqtt_client = mqtt.Client(client_id=self.client_id)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect
        
        # Disable automatic reconnect to prevent loops
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=60)
        
        # Track connection and subscription state
        self.is_connected = False
        self.is_subscribed = False
        self.connection_attempts = 0
        self.max_connection_attempts = 3
        
        # Track responses
        self.responses = {}
        
        # Connect to MQTT broker with retry logic
        self._connect_with_retry(mqtt_broker, mqtt_port)
        
        # Rule presets with descriptions
        self.presets = {
            "hue_motion_only": "Hue light - Motion sensor only",
            "hue_activity_only": "Hue light - Activity sensor only", 
            "hue_ir_only": "Hue light - IR proximity sensor only",
            "hue_ir_motion": "Hue light - IR + Motion (OR logic)",
            "hue_activity_motion": "Hue light - Activity + Motion (OR logic)",
            "hue_ir_activity": "Hue light - IR + Activity (OR logic)", 
            "hue_all_sensors": "Hue light - All sensors (OR logic)",
            "aircon_motion_only": "Aircon - Motion sensor only",
            "aircon_activity_only": "Aircon - Activity sensor only",
            "aircon_ir_only": "Aircon - IR proximity sensor only",
            "aircon_all_sensors": "Aircon - All sensors (OR logic)",
            "aircon_door_only": "Aircon - Door sensor only",
            "aircon_door_sensors": "Aircon - Door + IR sensors",
            "both_devices_motion": "Both devices - Motion only",
            "both_devices_all": "Both devices - All sensors",
            # ClubHouseAgent presets
            "back_read": "ClubHouse - Back area reading mode",
            "front_read": "ClubHouse - Front area reading mode", 
            "back_nap": "ClubHouse - Back area nap mode",
            "front_nap": "ClubHouse - Front area nap mode",
            "all_normal": "ClubHouse - All areas normal mode",
            "all_clean": "ClubHouse - All areas cleaning mode"
        }
        
        # Available rule files
        self.available_files = [
            "lapras_middleware/rules/hue_motion.ttl",
            "lapras_middleware/rules/hue_activity.ttl", 
            "lapras_middleware/rules/hue_ir.ttl",
            "lapras_middleware/rules/hue_ir_motion.ttl",
            "lapras_middleware/rules/hue_activity_motion.ttl",
            "lapras_middleware/rules/hue_ir_activity.ttl",
            "lapras_middleware/rules/hue_ir_activity_motion.ttl",
            "lapras_middleware/rules/aircon_motion.ttl",
            "lapras_middleware/rules/aircon_activity.ttl",
            "lapras_middleware/rules/aircon_ir.ttl",
            "lapras_middleware/rules/aircon_ir_motion.ttl",
            "lapras_middleware/rules/aircon_activity_motion.ttl",
            "lapras_middleware/rules/aircon_ir_activity.ttl",
            "lapras_middleware/rules/aircon_ir_acitivity_motion.ttl",
            "lapras_middleware/rules/aircon_door.ttl",
            "lapras_middleware/rules/aircon_ir_temperature.ttl",
            # ClubHouseAgent rule files
            "lapras_middleware/rules/back-read.ttl",
            "lapras_middleware/rules/front-read.ttl",
            "lapras_middleware/rules/back-nap.ttl", 
            "lapras_middleware/rules/front-nap.ttl",
            "lapras_middleware/rules/all-normal.ttl",
            "lapras_middleware/rules/all-clean.ttl"
        ]
    
    def _connect_with_retry(self, mqtt_broker: str, mqtt_port: int):
        """Connect to MQTT broker with retry logic and exponential backoff."""
        self.connection_attempts = 0
        
        while self.connection_attempts < self.max_connection_attempts:
            try:
                self.connection_attempts += 1
                print(f"🔌 Connecting to MQTT broker... (attempt {self.connection_attempts}/{self.max_connection_attempts})")
                
                # Try to connect
                result = self.mqtt_client.connect(mqtt_broker, mqtt_port, keepalive=60)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    # Start the loop
                    self.mqtt_client.loop_start()
                    
                    # Wait for connection with timeout
                    timeout = 10
                    start_time = time.time()
                    while not self.is_connected and (time.time() - start_time) < timeout:
                        time.sleep(0.1)
                    
                    if self.is_connected:
                        print(f"🤖 Rule Agent Tester initialized (connected to {mqtt_broker}:{mqtt_port})")
                        return  # Success!
                    else:
                        print(f"⏰ Connection timeout on attempt {self.connection_attempts}")
                        self.mqtt_client.loop_stop()
                        self.mqtt_client.disconnect()
                else:
                    print(f"❌ Connection failed with error code: {result}")
                    
            except Exception as e:
                print(f"❌ Connection error on attempt {self.connection_attempts}: {e}")
            
            # Exponential backoff delay before retry (unless it's the last attempt)
            if self.connection_attempts < self.max_connection_attempts:
                delay = 2 ** self.connection_attempts  # 2, 4, 8 seconds
                print(f"⏳ Waiting {delay} seconds before retry...")
                time.sleep(delay)
        
        print(f"❌ Failed to connect after {self.max_connection_attempts} attempts")
        print("💡 Tip: Check if MQTT broker is running and accessible")

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            self.is_connected = True
            self.connection_attempts = 0  # Reset attempts on successful connection
            
            # Only subscribe if not already subscribed
            if not self.is_subscribed:
                response_topic = TopicManager.dashboard_rules_response()
                result = client.subscribe(response_topic, qos=1)
                if result[0] == mqtt.MQTT_ERR_SUCCESS:
                    self.is_subscribed = True
                    print(f"✅ Connected and subscribed to rule responses")
                else:
                    print(f"❌ Failed to subscribe to {response_topic}")
        else:
            print(f"❌ Failed to connect to MQTT broker. Result code: {rc}")
            self.is_connected = False
    
    def _on_message(self, client, userdata, msg):
        """Callback when message is received."""
        try:
            if msg.topic == TopicManager.dashboard_rules_response():
                event = MQTTMessage.deserialize(msg.payload.decode())
                command_id = event.payload.get('command_id')
                success = event.payload.get('success')
                message = event.payload.get('message')
                action = event.payload.get('action')
                
                self.responses[command_id] = {
                    'success': success,
                    'message': message,
                    'action': action,
                    'timestamp': time.time()
                }
                
                status = "✅ SUCCESS" if success else "❌ FAILED"
                print(f"📥 Response: {status} - {action}")
                print(f"   💬 {message}")
                print(f"   🔗 ID: {command_id}")
                
        except Exception as e:
            print(f"❌ Error processing message: {e}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker."""
        self.is_connected = False
        self.is_subscribed = False
        
        # Only show warning for unexpected disconnections (not manual)
        if rc != 0:
            print(f"⚠️  Unexpected disconnection from MQTT broker (error code: {rc})")
            print(f"📡 Connection will not auto-reconnect to prevent loops")
            # Don't auto-reconnect - let user manually restart if needed
    
    def send_dashboard_rules_request(self, action: str, preset_name: str = None, rule_files: list = None):
        """Send a dashboard rules request via Rule Agent."""
        try:
            # Check if connected
            if not self.is_connected:
                print("❌ Cannot send request - not connected to MQTT broker")
                return None
                
            # Create dashboard rules request event
            event = EventFactory.create_dashboard_rules_request_event(
                action=action,
                preset_name=preset_name,
                rule_files=rule_files,
                source_entity_id=self.client_id
            )
            
            # Send to rule agent
            request_topic = TopicManager.dashboard_rules_request()
            message = MQTTMessage.serialize(event)
            result = self.mqtt_client.publish(request_topic, message, qos=1)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                print(f"📤 Sent request: {action}")
                if preset_name:
                    print(f"   📋 Preset: {preset_name}")
                if rule_files:
                    print(f"   📄 Files: {[f.split('/')[-1] for f in rule_files]}")
                print(f"   🔗 ID: {event.event.id}")
                return event.event.id
            else:
                print(f"❌ Failed to send request. MQTT error: {result.rc}")
                return None
            
        except Exception as e:
            print(f"❌ Error sending request: {e}")
            return None
    
    def reconnect_manually(self, mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        """Manually reconnect to MQTT broker."""
        print("🔄 Manual reconnection requested...")
        
        # Stop current connection if any
        if self.is_connected:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            time.sleep(1)  # Give time for clean disconnect
        
        self.is_connected = False
        self.is_subscribed = False
        
        # Retry connection
        self._connect_with_retry(mqtt_broker, mqtt_port)
    
    def wait_for_response(self, command_id: str, timeout: int = 10):
        """Wait for a response to a specific command."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if command_id in self.responses:
                return self.responses[command_id]
            time.sleep(0.1)
        return None
    
    def stop(self):
        """Stop the tester."""
        print("🛑 Stopping Rule Agent Tester...")
        
        # Clean disconnect
        self.is_connected = False
        self.is_subscribed = False
        
        try:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            print("✅ Disconnected cleanly")
        except Exception as e:
            print(f"⚠️  Error during disconnect: {e}")


def automated_test():
    """Automated test sequence."""
    tester = RuleAgentTester()
    
    print("🤖 Automated Rule Agent Testing")
    print("=" * 50)
    
    test_sequences = [
        ("list_presets", {}, "List available presets"),
        ("list", {}, "List currently loaded rules"),
        ("switch_preset", {"preset_name": "hue_motion_only"}, "Switch to hue_motion_only preset"),
        ("list", {}, "List rules after preset switch"),
        ("switch_preset", {"preset_name": "back_nap"}, "Switch to ClubHouse back_nap preset"),
        ("list", {}, "List rules after ClubHouse back_nap switch"),
        ("switch_preset", {"preset_name": "front_read"}, "Switch to ClubHouse front_read preset"),
        ("list", {}, "List rules after ClubHouse front_read switch"),
        ("switch", {"rule_files": ["lapras_middleware/rules/back-nap.ttl", "lapras_middleware/rules/front-read.ttl"]}, "Switch to ClubHouse custom rules"),
        ("list", {}, "List rules after ClubHouse custom switch"),
        ("switch_preset", {"preset_name": "all_normal"}, "Switch to ClubHouse all_normal preset"),
        ("list", {}, "List rules after all_normal switch"),
        ("clear", {}, "Clear all rules"),
        ("list", {}, "List rules after clear"),
        ("switch_preset", {"preset_name": "both_devices_motion"}, "Switch to both_devices_motion preset"),
        ("list", {}, "Final rules listing"),
    ]
    
    for i, (action, kwargs, description) in enumerate(test_sequences, 1):
        print(f"\n📋 Test {i}: {description}")
        print(f"   🎯 Action: {action}")
        
        cmd_id = tester.send_dashboard_rules_request(action, **kwargs)
        if cmd_id:
            response = tester.wait_for_response(cmd_id, timeout=10)
            if response:
                status = "✅" if response['success'] else "❌"
                print(f"   {status} Result: {response['message']}")
            else:
                print("   ⏰ Timeout waiting for response")
        
        time.sleep(2)  # Brief pause between tests
    
    print(f"\n🏁 Automated testing completed!")
    tester.stop()


def interactive_test():
    """Interactive test mode."""
    tester = RuleAgentTester()
    
    print("🎮 Interactive Rule Agent Testing")
    print("=" * 50)
    
    try:
        while True:
            # Show connection status
            status_icon = "🟢" if tester.is_connected else "🔴"
            status_text = "Connected" if tester.is_connected else "Disconnected"
            print(f"\n📡 MQTT Status: {status_icon} {status_text}")
            
            print("\n🎯 Available Commands:")
            print("1. List presets")
            print("2. List loaded rules")
            print("3. Switch to preset")
            print("4. Switch to custom files")
            print("5. Clear rules")
            print("6. Load additional rules")
            print("7. Reconnect to MQTT broker")
            print("8. Exit")
            
            choice = input("\nEnter your choice (1-8): ").strip()
            
            if not choice:  # Handle empty input
                continue
                
            if choice == "1":
                print("📋 Listing available presets...")
                cmd_id = tester.send_dashboard_rules_request("list_presets")
                response = tester.wait_for_response(cmd_id, timeout=10)
                if not response:
                    print("⏰ No response received within 10 seconds")
                    
            elif choice == "2":
                print("📋 Listing loaded rules...")
                cmd_id = tester.send_dashboard_rules_request("list")
                response = tester.wait_for_response(cmd_id, timeout=10)
                if not response:
                    print("⏰ No response received within 10 seconds")
                    
            elif choice == "3":
                print("\n📋 Available Presets:")
                preset_list = list(tester.presets.keys())
                for i, (preset_key, description) in enumerate(tester.presets.items(), 1):
                    print(f"  {i:2d}. {preset_key:<20} - {description}")
                
                try:
                    preset_choice = int(input(f"\nSelect preset (1-{len(tester.presets)}): ")) - 1
                    if 0 <= preset_choice < len(preset_list):
                        selected_preset = preset_list[preset_choice]
                        print(f"🔄 Switching to {selected_preset}...")
                        cmd_id = tester.send_dashboard_rules_request("switch_preset", preset_name=selected_preset)
                        response = tester.wait_for_response(cmd_id, timeout=10)
                        if not response:
                            print("⏰ No response received within 10 seconds")
                    else:
                        print("❌ Invalid preset number")
                except ValueError:
                    print("❌ Please enter a valid number")
                    
            elif choice == "4":
                print("\n📁 Custom Rule Files Selection")
                print("Available rule files:")
                
                for i, file in enumerate(tester.available_files, 1):
                    filename = file.split('/')[-1]
                    print(f"  {i:2d}. {filename}")
                
                print("\nEnter file numbers separated by spaces (e.g., '1 3 5'):")
                file_input = input("Selection: ").strip()
                
                try:
                    selected_indices = [int(x.strip()) - 1 for x in file_input.split()]
                    selected_files = [tester.available_files[i] for i in selected_indices if 0 <= i < len(tester.available_files)]
                    
                    if selected_files:
                        print(f"🔄 Switching to custom files: {[f.split('/')[-1] for f in selected_files]}")
                        cmd_id = tester.send_dashboard_rules_request("switch", rule_files=selected_files)
                        response = tester.wait_for_response(cmd_id, timeout=10)
                        if not response:
                            print("⏰ No response received within 10 seconds")
                    else:
                        print("❌ No valid files selected")
                        
                except (ValueError, IndexError):
                    print("❌ Invalid input format")
                    
            elif choice == "5":
                print("🗑️ Clearing all rules...")
                cmd_id = tester.send_dashboard_rules_request("clear")
                response = tester.wait_for_response(cmd_id, timeout=10)
                if not response:
                    print("⏰ No response received within 10 seconds")
                    
            elif choice == "6":
                print("\n📁 Load Additional Rule Files")
                print("Available rule files:")
                
                for i, file in enumerate(tester.available_files, 1):
                    filename = file.split('/')[-1]
                    print(f"  {i:2d}. {filename}")
                
                print("\nEnter file numbers separated by spaces (e.g., '1 3 5'):")
                file_input = input("Selection: ").strip()
                
                try:
                    selected_indices = [int(x.strip()) - 1 for x in file_input.split()]
                    selected_files = [tester.available_files[i] for i in selected_indices if 0 <= i < len(tester.available_files)]
                    
                    if selected_files:
                        print(f"📥 Loading additional files: {[f.split('/')[-1] for f in selected_files]}")
                        cmd_id = tester.send_dashboard_rules_request("load", rule_files=selected_files)
                        response = tester.wait_for_response(cmd_id, timeout=10)
                        if not response:
                            print("⏰ No response received within 10 seconds")
                    else:
                        print("❌ No valid files selected")
                        
                except (ValueError, IndexError):
                    print("❌ Invalid input format")
                    
            elif choice == "7":
                print("🔄 Reconnecting to MQTT broker...")
                tester.reconnect_manually()
                
            elif choice == "8":
                print("👋 Exiting...")
                break
                
            else:
                print(f"❌ Invalid choice: '{choice}'. Please enter a number between 1-8.")
            
            # Small delay to prevent rapid execution
            time.sleep(0.1)
    
    except KeyboardInterrupt:
        print("\n👋 Exiting...")
    
    finally:
        tester.stop()


def main():
    """Main entry point."""
    print("🎮 Rule Agent Tester")
    print("=" * 30)
    print("Choose testing mode:")
    print("1. Automated test sequence")
    print("2. Interactive mode")
    
    while True:
        mode = input("\nEnter your choice (1 or 2): ").strip()
        
        if mode == "1":
            automated_test()
            break
        elif mode == "2":
            interactive_test()
            break
        else:
            print("❌ Invalid choice. Please enter 1 or 2.")


if __name__ == "__main__":
    main() 