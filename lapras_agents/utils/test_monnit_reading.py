#!/usr/bin/env python3
"""
Real-time Monnit Sensor Monitor
Monitors specific types of sensors in real-time with configurable intervals
Uses API-level filtering for efficient data retrieval
"""

import requests
import time
import json
import argparse
from datetime import datetime
from collections import defaultdict

class MonnitSensorMonitor:
    def __init__(self, api_key_id, api_secret_key):
        self.api_key_id = api_key_id
        self.api_secret_key = api_secret_key
        self.base_url = "https://www.imonnit.com/json"
        self.headers = {
            "APIKeyID": api_key_id,
            "APISecretKey": api_secret_key,
            "Content-Type": "application/json"
        }
        self.sensor_cache = {}
        
    def get_sensor_list(self, name=None, network_id=None, application_id=None, status=None, account_id=None):
        """
        Get list of sensors with optional filtering
        
        Parameters:
        - name: Filter by sensor name (string)
        - network_id: Filter by network ID (integer) 
        - application_id: Filter by application ID (integer)
        - status: Filter by status (integer)
        - account_id: Filter by account ID (integer)
        """
        url = f"{self.base_url}/SensorList"
        
        # Build parameters (only include non-None values)
        params = {}
        if name is not None:
            params["Name"] = name
        if network_id is not None:
            params["NetworkID"] = network_id
        if application_id is not None:
            params["ApplicationID"] = application_id
        if status is not None:
            params["Status"] = status
        if account_id is not None:
            params["accountID"] = account_id
        
        try:
            response = requests.post(url, headers=self.headers, json=params)
            if response.status_code == 200:
                response_data = response.json()
                # Check if there's an error in the response
                if isinstance(response_data.get('Result'), str):
                    print(f"❌ API Error: {response_data.get('Result')}")
                    return None
                return response_data
            else:
                print(f"❌ HTTP Error: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            print(f"❌ Exception getting sensor list: {e}")
            return None
    
    def get_single_sensor_data(self, sensor_id):
        """Get data for a specific sensor using SensorGet API"""
        url = f"{self.base_url}/SensorGet"
        params = {"sensorID": sensor_id}
        
        try:
            response = requests.post(url, headers=self.headers, json=params)
            if response.status_code == 200:
                response_data = response.json()
                # Check if there's an error in the response
                if isinstance(response_data.get('Result'), str):
                    print(f"❌ Sensor API Error: {response_data.get('Result')}")
                    return None
                return response_data
            else:
                print(f"❌ HTTP Error getting sensor {sensor_id}: {response.status_code}")
                return None
        except Exception as e:
            print(f"❌ Exception getting sensor {sensor_id}: {e}")
            return None

    def get_sensor_type_from_app_id(self, app_id):
        """Map ApplicationID to sensor type"""
        app_id_mapping = {
            2: 'temperature',
            5: 'activity', 
            9: 'door',
            75: 'tilt',
            101: 'motion',
            107: 'light'
        }
        return app_id_mapping.get(app_id, 'unknown')

    def get_sensors_by_type(self, sensor_type, target_network_id=None):
        """
        Get sensors filtered by type using API-level filtering
        
        Parameters:
        - sensor_type: Type of sensor ('temperature', 'activity', 'door', 'motion', 'light')
        - target_network_id: Optional network ID to filter by
        
        Returns: list of sensor dictionaries
        """
        # Map sensor type to application ID
        type_to_app_id = {
            'temperature': 2,
            'activity': 5,
            'door': 9,
            'tilt': 75,
            'motion': 101,
            'light': 107
        }
        
        app_id = type_to_app_id.get(sensor_type)
        if app_id is None:
            print(f"❌ Unknown sensor type: {sensor_type}")
            print(f"Available types: {list(type_to_app_id.keys())}")
            return []
        
        print(f"🔍 Fetching {sensor_type} sensors (ApplicationID: {app_id})...")
        
        # Use API filtering to get only the sensors we want
        sensor_list = self.get_sensor_list(
            application_id=app_id,
            network_id=target_network_id
        )
        
        if not sensor_list or 'Result' not in sensor_list:
            print("❌ Failed to get sensor list")
            return []
        
        all_sensors = sensor_list['Result']
        
        # Check if Result is an error message (string) instead of sensor list
        if isinstance(all_sensors, str):
            print(f"❌ API Error: {all_sensors}")
            return []
        
        # Ensure we have a list of sensors
        if not isinstance(all_sensors, list):
            print(f"❌ Unexpected response format. Expected list, got: {type(all_sensors)}")
            return []
        
        # Cache sensor info for later use
        for sensor in all_sensors:
            if isinstance(sensor, dict):
                sensor_id = sensor.get('SensorID')
                if sensor_id:
                    self.sensor_cache[sensor_id] = {
                        'name': sensor.get('SensorName', 'Unknown'),
                        'type': sensor_type,
                        'network_id': sensor.get('CSNetID'),
                        'app_id': sensor.get('ApplicationID')
                    }
        
        return all_sensors

    def print_sensor_summary(self, sensors, sensor_type, target_network_id):
        """Print summary of available sensors"""
        print(f"\n📊 {sensor_type.capitalize()} Sensor Summary:")
        if target_network_id:
            print(f"   Network ID: {target_network_id}")
        print("=" * 50)
        print(f"  Found: {len(sensors)} sensors")
        print("=" * 50)

    def monitor_sensors(self, sensor_type, target_network_id=None, interval_ms=500):
        """
        Monitor specific type of sensors in real-time
        """
        print(f"🚀 Starting real-time monitoring...")
        print(f"   Sensor Type: {sensor_type}")
        if target_network_id:
            print(f"   Network ID: {target_network_id}")
        print(f"   Interval: {interval_ms}ms")
        print(f"   Press Ctrl+C to stop\n")
        
        # Get sensors of the specified type using API filtering
        sensors = self.get_sensors_by_type(sensor_type, target_network_id)
        
        if not sensors:
            print(f"❌ No {sensor_type} sensors found")
            if target_network_id:
                print(f"   (searched in Network ID: {target_network_id})")
            return
        
        print(f"✅ Found {len(sensors)} {sensor_type} sensors")
        
        # Print sensor names
        print(f"\n📡 Monitoring sensors:")
        for i, sensor in enumerate(sensors, 1):
            sensor_id = sensor.get('SensorID')
            sensor_name = sensor.get('SensorName', 'Unknown')
            print(f"  [{i}] {sensor_name} (ID: {sensor_id})")
        
        print(f"\n{'='*80}")
        print(f"🔄 REAL-TIME MONITORING STARTED")
        print(f"{'='*80}\n")
        
        try:
            iteration = 1
            while True:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                print(f"[{iteration}] 📅 {timestamp}")
                print("-" * 80)
                
                for i, sensor in enumerate(sensors, 1):
                    sensor_id = sensor.get('SensorID')
                    sensor_name = sensor.get('SensorName', 'Unknown')
                    
                    # Get real-time data
                    sensor_data = self.get_single_sensor_data(sensor_id)
                    
                    if sensor_data and 'Result' in sensor_data:
                        result = sensor_data['Result']
                        
                        print(f"  [{i}] {sensor_name}")
                        print(f"      🔢 ID: {sensor_id}")
                        print(f"      📊 Reading: {result.get('CurrentReading', 'No data')}")
                        print(f"      🔋 Battery: {result.get('BatteryLevel', 'Unknown')}%")
                        print(f"      📶 Signal: {result.get('SignalStrength', 'Unknown')}%")
                        print(f"      ⚡ Status: {result.get('Status', 'Unknown')}")
                        
                        # Format last communication time
                        last_comm = result.get('LastCommunicationDate', '')
                        if last_comm and '/Date(' in last_comm:
                            try:
                                timestamp_ms = int(last_comm.split('(')[1].split(')')[0])
                                last_comm_formatted = datetime.fromtimestamp(timestamp_ms/1000).strftime("%H:%M:%S")
                                print(f"      🕐 Last Comm: {last_comm_formatted}")
                            except:
                                print(f"      🕐 Last Comm: {last_comm}")
                        
                        print()
                    else:
                        print(f"  [{i}] {sensor_name} - ❌ Failed to get data")
                        print()
                
                print(f"{'='*80}")
                
                # Wait for next iteration
                time.sleep(interval_ms / 1000.0)
                iteration += 1
                
        except KeyboardInterrupt:
            print(f"\n\n🛑 Monitoring stopped by user")
        except Exception as e:
            print(f"\n❌ Error during monitoring: {e}")

    def list_all_sensor_types(self, target_network_id=None):
        """List sensors grouped by type"""
        print("🔍 Discovering all sensor types...")
        
        # Get all sensors (no filtering)
        sensor_list = self.get_sensor_list(network_id=target_network_id)
        
        if not sensor_list or 'Result' not in sensor_list:
            print("❌ Failed to get sensor list")
            return
        
        all_sensors = sensor_list['Result']
        
        if isinstance(all_sensors, str):
            print(f"❌ API Error: {all_sensors}")
            return
        
        if not isinstance(all_sensors, list):
            print(f"❌ Unexpected response format. Expected list, got: {type(all_sensors)}")
            return
        
        # Group by type
        categorized = defaultdict(list)
        for sensor in all_sensors:
            if isinstance(sensor, dict):
                app_id = sensor.get('ApplicationID')
                sensor_type = self.get_sensor_type_from_app_id(app_id)
                sensor_id = sensor.get('SensorID')
                sensor_name = sensor.get('SensorName', 'Unknown')
                
                categorized[sensor_type].append({
                    'id': sensor_id,
                    'name': sensor_name,
                    'app_id': app_id
                })
        
        # Print summary
        print(f"\n📊 Sensor Types Summary:")
        if target_network_id:
            print(f"   Network ID: {target_network_id}")
        print("=" * 60)
        
        total_sensors = 0
        for sensor_type, sensor_list in sorted(categorized.items()):
            count = len(sensor_list)
            total_sensors += count
            print(f"  {sensor_type.capitalize()}: {count} sensors")
            
            # Show first few sensors as examples
            for sensor in sensor_list[:3]:
                print(f"    • {sensor['name']} (ID: {sensor['id']})")
            if len(sensor_list) > 3:
                print(f"    • ... and {len(sensor_list) - 3} more")
            print()
            
        print(f"Total: {total_sensors} sensors")
        print("=" * 60)

def main():
    parser = argparse.ArgumentParser(
        description='Real-time Monnit Sensor Monitor with API-level filtering',
        epilog="""
Examples:
  python %(prog)s --api-key-id YOUR_KEY --api-secret-key YOUR_SECRET --type temperature
  python %(prog)s --api-key-id YOUR_KEY --api-secret-key YOUR_SECRET --type activity --interval 1000
  python %(prog)s --api-key-id YOUR_KEY --api-secret-key YOUR_SECRET --type motion --network-id 61731
  python %(prog)s --api-key-id YOUR_KEY --api-secret-key YOUR_SECRET --list-all
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--api-key-id', help='Monnit API Key ID', 
                       default="kO1L9ova4KSD")
    parser.add_argument('--api-secret-key', help='Monnit API Secret Key',
                       default="Het79frgls3qdGbh9wsju5hOVSQF5AW3")
    parser.add_argument('--type', 
                       choices=['temperature', 'activity', 'door', 'motion', 'light', 'tilt'],
                       help='Type of sensor to monitor')
    parser.add_argument('--network-id', type=int, default=61731,
                       help='Network ID to filter by (e.g., 61731 for seminar room)')
    parser.add_argument('--interval', type=int, default=500,
                       help='Monitoring interval in milliseconds (default: 500ms)')
    parser.add_argument('--list-all', action='store_true',
                       help='List all sensor types and exit')
    
    args = parser.parse_args()
    
    print(f"🔐 Connecting with API Key ID: {args.api_key_id}")
    
    # Create monitor instance
    monitor = MonnitSensorMonitor(args.api_key_id, args.api_secret_key)
    
    if args.list_all:
        # Show all sensor types
        monitor.list_all_sensor_types(args.network_id)
    elif args.type:
        # Monitor specific sensor type
        monitor.monitor_sensors(args.type, args.network_id, args.interval)
    else:
        print("❌ Please specify either --type or --list-all")
        parser.print_help()

if __name__ == "__main__":
    main()