#!/usr/bin/env python3
"""
Enhanced Dashboard Subscriber - Monitors both context manager state and dashboard agent sensor data.
Subscribes to:
- 'dashboard/context/state' - Context manager agent states
- Dashboard agent updates - Aggregated sensor data
"""

import json
import logging
import time
import sys
import os
import paho.mqtt.client as mqtt
from datetime import datetime
from collections import defaultdict

# Add the project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)
from lapras_middleware.event import MQTTMessage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EnhancedDashboardSubscriber:
    def __init__(self, mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        """Initialize the enhanced dashboard subscriber."""
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        
        # Topics to subscribe to
        self.context_topic = "dashboard/context/state"
        self.dashboard_agent_topic = "virtual/dashboard/to/context/updateContext"
        
        # Store data from both sources
        self.context_data = {}
        self.sensor_data = {}
        self.last_context_update = 0
        self.last_sensor_update = 0
        
        # Sensor type emojis for display
        self.sensor_icons = {
            "infrared": "🔴",
            "motion": "🏃", 
            "temperature": "🌡️",
            "door": "🚪",
            "activity": "⚡"
        }
        
        # Set up MQTT client
        client_id = f"EnhancedDashboardSubscriber-{int(time.time()*1000)}"
        self.mqtt_client = mqtt.Client(client_id=client_id)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect
        
        logger.info(f"Initialized enhanced dashboard subscriber with client ID: {client_id}")

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            logger.info(f"Successfully connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
            
            # Subscribe to context manager dashboard updates
            result1 = self.mqtt_client.subscribe(self.context_topic, qos=1)
            logger.info(f"Subscribed to context manager topic: {self.context_topic} (result: {result1})")
            
            # Subscribe to dashboard agent updates
            result2 = self.mqtt_client.subscribe(self.dashboard_agent_topic, qos=1)
            logger.info(f"Subscribed to dashboard agent topic: {self.dashboard_agent_topic} (result: {result2})")
            
        else:
            logger.error(f"Failed to connect to MQTT broker. Result code: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker."""
        if rc != 0:
            logger.warning(f"Unexpected disconnection from MQTT broker. Result code: {rc}")
        else:
            logger.info("Disconnected from MQTT broker")

    def _on_message(self, client, userdata, msg):
        """Callback when message is received."""
        try:
            # Just print the raw JSON message
            message_str = msg.payload.decode('utf-8')
            print(f"\n=== RAW JSON MESSAGE ===")
            print(f"Topic: {msg.topic}")
            print(f"JSON: {message_str}")
            print("=" * 50)
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            logger.error(f"Raw message: {msg.payload.decode('utf-8', errors='ignore')[:200]}...")

    def _handle_context_manager_update(self, event):
        """Handle updates from the context manager (agent states)."""
        try:
            self.context_data = event.payload
            self.last_context_update = time.time()
            
            # Display context manager update with more details
            self._display_context_update()
            
        except Exception as e:
            logger.error(f"Error handling context manager update: {e}")

    def _handle_dashboard_agent_update(self, event):
        """Handle updates from the dashboard agent (sensor data)."""
        try:
            payload = event.payload
            state = payload.get("state", {})
            
            self.sensor_data = {
                "dashboard_status": state.get("dashboard_status", "unknown"),
                "total_sensors": state.get("total_sensors", 0),
                "sensors_online": state.get("sensors_online", 0),
                "sensors_offline": state.get("sensors_offline", 0),
                "last_activity_time": state.get("last_activity_time", 0),
                "activity_summary": state.get("activity_summary", {}),
                "individual_sensors": {},
                "timestamp": event.event.timestamp,
                "raw_state": state  # Keep the raw state for debugging
            }
            
            # Extract individual sensor data
            for key, value in state.items():
                if '_' in key and not key.startswith('dashboard') and not key.startswith('total') and not key.startswith('sensors') and not key.startswith('last') and not key.startswith('activity'):
                    # Parse sensor data fields like "infrared_1_distance", "motion_01_status", etc.
                    parts = key.split('_')
                    if len(parts) >= 3:
                        sensor_type = parts[0]
                        sensor_num = parts[1]
                        field_name = '_'.join(parts[2:])
                        sensor_id = f"{sensor_type}_{sensor_num}"
                        
                        if sensor_id not in self.sensor_data["individual_sensors"]:
                            self.sensor_data["individual_sensors"][sensor_id] = {"type": sensor_type}
                        
                        self.sensor_data["individual_sensors"][sensor_id][field_name] = value
            
            self.last_sensor_update = time.time()
            
            # Display sensor update
            self._display_sensor_update()
            
        except Exception as e:
            logger.error(f"Error handling dashboard agent update: {e}")

    def _display_context_update(self):
        """Display context manager update (agent states) with full details."""
        current_time = datetime.now().strftime("%H:%M:%S")
        
        print(f"\n[{current_time}] 📊 CONTEXT MANAGER UPDATE")
        print("─" * 70)
        
        # Display summary
        if 'summary' in self.context_data:
            summary = self.context_data['summary']
            print(f"Total Agents: {summary.get('total_agents', 0)}")
            print(f"Known Agents: {summary.get('known_agents', [])}")
            print(f"Last Update: {datetime.fromtimestamp(summary.get('last_update', 0)).strftime('%H:%M:%S')}")
        
        # Display detailed agent information
        if 'agents' in self.context_data and self.context_data['agents']:
            print("\nDETAILED AGENT STATES:")
            for agent_id, agent_info in self.context_data['agents'].items():
                status = "🟢 ONLINE" if agent_info.get('is_responsive', False) else "🔴 OFFLINE"
                print(f"\n  {status} {agent_id.upper()}")
                print(f"    Type: {agent_info.get('agent_type', 'unknown')}")
                print(f"    Last Seen: {datetime.fromtimestamp(agent_info.get('last_update', 0)).strftime('%H:%M:%S')}")
                
                # Display agent state in detail
                state = agent_info.get('state', {})
                if state:
                    print(f"    State ({len(state)} fields):")
                    
                    # Group state fields for better display
                    core_fields = ['power', 'dashboard_status', 'activity_detected']
                    summary_fields = ['total_sensors', 'sensors_online', 'sensors_offline']
                    status_fields = [k for k in state.keys() if k.endswith('_status') or k.endswith('_proximity')]
                    sensor_fields = [k for k in state.keys() if any(k.startswith(f"{t}_") for t in ['infrared', 'motion', 'temperature', 'door', 'activity']) and k not in status_fields]
                    other_fields = [k for k in state.keys() if k not in core_fields + summary_fields + status_fields + sensor_fields]
                    
                    # Display core fields
                    for field in core_fields:
                        if field in state:
                            print(f"      {field}: {state[field]}")
                    
                    # Display summary fields
                    for field in summary_fields:
                        if field in state:
                            print(f"      {field}: {state[field]}")
                    
                    # Display activity summary if it exists
                    if 'activity_summary' in state:
                        activity = state['activity_summary']
                        print(f"      activity_summary: IR:{activity.get('infrared_active',0)} Motion:{activity.get('motion_active',0)} Temp:{activity.get('temperature_alerts',0)} Door:{activity.get('doors_open',0)} Activity:{activity.get('activity_detected',0)}")
                    
                    # Display status fields (grouped)
                    if status_fields:
                        print(f"      Status Fields ({len(status_fields)}):")
                        for field in sorted(status_fields):
                            print(f"        {field}: {state[field]}")
                    
                    # Display sensor value fields (grouped and limited)
                    if sensor_fields:
                        displayed_sensors = 0
                        print(f"      Sensor Fields ({len(sensor_fields)} total, showing first 10):")
                        for field in sorted(sensor_fields):
                            if displayed_sensors < 10:
                                print(f"        {field}: {state[field]}")
                                displayed_sensors += 1
                            else:
                                break
                        if len(sensor_fields) > 10:
                            print(f"        ... and {len(sensor_fields) - 10} more sensor fields")
                    
                    # Display other fields
                    if other_fields:
                        print(f"      Other Fields ({len(other_fields)}):")
                        for field in sorted(other_fields)[:5]:  # Show max 5
                            print(f"        {field}: {state[field]}")
                        if len(other_fields) > 5:
                            print(f"        ... and {len(other_fields) - 5} more fields")
                
                # Display sensors
                sensors = agent_info.get('sensors', {})
                if sensors:
                    print(f"    Sensors ({len(sensors)}):")
                    for sensor_key, sensor_value in list(sensors.items())[:5]:  # Show first 5
                        print(f"      {sensor_key}: {sensor_value}")
                    if len(sensors) > 5:
                        print(f"      ... and {len(sensors) - 5} more sensors")
        else:
            print("No agents found in context data")
        
        print()

    def _display_sensor_update(self):
        """Display dashboard agent sensor update with enhanced details."""
        current_time = datetime.now().strftime("%H:%M:%S")
        
        print(f"\n[{current_time}] 📡 DASHBOARD AGENT SENSOR UPDATE")
        print("─" * 70)
        
        # Display summary
        status = self.sensor_data.get("dashboard_status", "unknown")
        total = self.sensor_data.get("total_sensors", 0)
        online = self.sensor_data.get("sensors_online", 0)
        offline = self.sensor_data.get("sensors_offline", 0)
        
        print(f"Dashboard Status: {status}")
        print(f"Sensors: {online}/{total} online, {offline} offline")
        
        # Display activity summary
        activity = self.sensor_data.get("activity_summary", {})
        if activity:
            print(f"Activity Summary:")
            print(f"  🔴 Infrared Active: {activity.get('infrared_active',0)}")
            print(f"  🏃 Motion Active: {activity.get('motion_active',0)}")
            print(f"  🌡️ Temperature Alerts: {activity.get('temperature_alerts',0)}")
            print(f"  🚪 Doors Open: {activity.get('doors_open',0)}")
            print(f"  ⚡ Activity Detected: {activity.get('activity_detected',0)}")
        
        # Display last activity
        last_activity = self.sensor_data.get("last_activity_time", 0)
        if last_activity > 0:
            time_ago = time.time() - last_activity
            print(f"Last Activity: {time_ago:.1f}s ago ({datetime.fromtimestamp(last_activity).strftime('%H:%M:%S')})")
        
        # Display individual sensors with detailed information
        sensors = self.sensor_data.get("individual_sensors", {})
        if sensors:
            print(f"\nINDIVIDUAL SENSOR READINGS ({len(sensors)} sensors):")
            
            # Group sensors by type
            by_type = {}
            for sensor_id, sensor_info in sensors.items():
                sensor_type = sensor_info.get("type", "unknown")
                if sensor_type not in by_type:
                    by_type[sensor_type] = []
                by_type[sensor_type].append((sensor_id, sensor_info))
            
            for sensor_type, sensor_list in sorted(by_type.items()):
                icon = self.sensor_icons.get(sensor_type, "📡")
                print(f"\n  {icon} {sensor_type.upper()} SENSORS ({len(sensor_list)}):")
                
                for sensor_id, sensor_info in sorted(sensor_list):
                    # Format based on sensor type
                    if sensor_type == "infrared":
                        distance = sensor_info.get("distance", "?")
                        proximity = sensor_info.get("proximity", "?")
                        unit = sensor_info.get("unit", "")
                        print(f"    {sensor_id}: {distance}{unit} (status: {proximity})")
                    elif sensor_type == "motion":
                        motion = sensor_info.get("motion", "?")
                        status = sensor_info.get("status", "?")
                        print(f"    {sensor_id}: motion={motion} (status: {status})")
                    elif sensor_type == "temperature":
                        temp = sensor_info.get("temperature", "?")
                        unit = sensor_info.get("unit", "")
                        status = sensor_info.get("status", "?")
                        print(f"    {sensor_id}: {temp}{unit} (status: {status})")
                    elif sensor_type == "door":
                        door_open = sensor_info.get("open", "?")
                        status = sensor_info.get("status", "?")
                        print(f"    {sensor_id}: open={door_open} (status: {status})")
                    elif sensor_type == "activity":
                        active = sensor_info.get("active", "?")
                        status = sensor_info.get("status", "?")
                        print(f"    {sensor_id}: active={active} (status: {status})")
                    else:
                        # Generic display for unknown sensor types
                        fields = [f"{k}={v}" for k, v in sensor_info.items() if k != "type"]
                        print(f"    {sensor_id}: {', '.join(fields[:3])}")
        else:
            print("\nNo individual sensor data received yet.")
            
            # Debug: show raw state data
            raw_state = self.sensor_data.get("raw_state", {})
            if raw_state:
                print(f"\nRAW STATE DEBUG (showing first 10 fields):")
                for i, (key, value) in enumerate(sorted(raw_state.items())):
                    if i >= 10:
                        print(f"  ... and {len(raw_state) - 10} more fields")
                        break
                    print(f"  {key}: {value}")
        
        print()

    def _display_combined_summary(self):
        """Display combined summary of both context and sensor data."""
        print("\n" + "="*80)
        print(f"COMBINED DASHBOARD SUMMARY - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)
        
        # Context manager summary
        context_age = time.time() - self.last_context_update if self.last_context_update > 0 else float('inf')
        context_status = "🟢 CURRENT" if context_age < 30 else "🔴 STALE"
        
        print(f"Context Manager: {context_status} (last update: {context_age:.1f}s ago)")
        if self.context_data.get('summary'):
            summary = self.context_data['summary']
            print(f"  Total Agents: {summary.get('total_agents', 0)}")
            print(f"  Known Agents: {summary.get('known_agents', [])}")
        
        # Sensor dashboard summary
        sensor_age = time.time() - self.last_sensor_update if self.last_sensor_update > 0 else float('inf')
        sensor_status = "🟢 CURRENT" if sensor_age < 30 else "🔴 STALE"
        
        print(f"\nSensor Dashboard: {sensor_status} (last update: {sensor_age:.1f}s ago)")
        if self.sensor_data:
            total = self.sensor_data.get("total_sensors", 0)
            online = self.sensor_data.get("sensors_online", 0)
            offline = self.sensor_data.get("sensors_offline", 0)
            print(f"  Sensors: {online}/{total} online, {offline} offline")
            
            activity = self.sensor_data.get("activity_summary", {})
            if activity:
                print(f"  Activity: IR={activity.get('infrared_active',0)}, Motion={activity.get('motion_active',0)}, " +
                      f"Temp alerts={activity.get('temperature_alerts',0)}, Doors open={activity.get('doors_open',0)}")
        
        print("="*80 + "\n")

    def start(self):
        """Start the enhanced dashboard subscriber."""
        try:
            print("ENHANCED SENSOR DASHBOARD")
            print("=" * 50)
            print("Monitoring both:")
            print("• Context Manager (agent states)")
            print("• Dashboard Agent (sensor data)")
            print("\nPress Ctrl+C to stop.\n")
            
            logger.info(f"Connecting to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}...")
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port)
            logger.info("Starting MQTT client loop...")
            
            # Start background MQTT loop
            self.mqtt_client.loop_start()
            
            # Main loop - display combined summary every 30 seconds
            last_summary = 0
            try:
                while True:
                    time.sleep(1)
                    current_time = time.time()
                    
                    # Show combined summary every 30 seconds
                    if current_time - last_summary > 30:
                        self._display_combined_summary()
                        last_summary = current_time
                        
            except KeyboardInterrupt:
                logger.info("Dashboard subscriber interrupted by user")
                
        except Exception as e:
            logger.error(f"Error starting dashboard subscriber: {e}")
        finally:
            self.stop()

    def stop(self):
        """Stop the enhanced dashboard subscriber."""
        logger.info("Stopping enhanced dashboard subscriber...")
        if hasattr(self, 'mqtt_client') and self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        logger.info("Enhanced dashboard subscriber stopped.")

def main():
    """Main function to run the enhanced dashboard subscriber."""
    subscriber = EnhancedDashboardSubscriber()
    subscriber.start()

if __name__ == "__main__":
    main() 