#!/usr/bin/env python3
"""
Start script for Dashboard Agent - Real-time sensor data aggregation for monitoring.
"""

import argparse
import logging
import time
import sys
import os

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lapras_agents.dashboard_agent import DashboardAgent

def parse_sensor_config(sensor_args):
    """Parse sensor configuration from command line arguments."""
    sensor_config = {}
    
    for sensor_arg in sensor_args:
        try:
            # Format: sensor_type:sensor1,sensor2,sensor3
            if ':' not in sensor_arg:
                print(f"Invalid sensor format: {sensor_arg}. Expected format: sensor_type:sensor1,sensor2")
                continue
                
            sensor_type, sensors_str = sensor_arg.split(':', 1)
            sensor_ids = [s.strip() for s in sensors_str.split(',')]
            sensor_config[sensor_type] = sensor_ids
            
        except Exception as e:
            print(f"Error parsing sensor config '{sensor_arg}': {e}")
            
    return sensor_config

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Start the Dashboard Agent for sensor monitoring')
    parser.add_argument('--agent-id', default="dashboard", 
                       help='Agent ID for the dashboard agent (default: dashboard)')
    parser.add_argument('--transmission-interval', type=float, default=2.0,
                       help='Transmission interval to context manager in seconds (default: 2.0)')
    parser.add_argument('--mqtt-broker', default="143.248.55.82",
                       help='MQTT broker address (default: 143.248.55.82)')
    parser.add_argument('--mqtt-port', type=int, default=1883,
                       help='MQTT broker port (default: 1883)')
    parser.add_argument('--sensors', '-s', nargs='*',
                       help='Sensor configuration. Format: sensor_type:sensor1,sensor2. ' + 
                            'Examples: infrared:infrared_1,infrared_2 motion:motion_01,motion_02')
    parser.add_argument('--preset', choices=['all', 'infrared-only', 'motion-only', 'temperature-only', 'minimal'],
                       help='Use preset sensor configurations')
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(module)s] - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    # Determine sensor configuration
    sensor_config = None
    
    if args.preset:
        if args.preset == 'all':
            sensor_config = {
                "light": ["light_1"],
                "infrared": ["infrared_1", "infrared_2", "infrared_3", "infrared_4"],
                "distance": ["distance_1", "distance_2", "distance_3", "distance_4"],
                "motion": ["motion_01", "motion_02", "motion_03", "motion_04", "motion_05", "motion_06", "motion_07", "motion_08", "motion_mf1","motion_mb1"],
                "temperature": ["temperature_1","temperature_2","temperature_3"],
                "door": ["door_01"],
                "tilt": ["tilt_1", "tilt_2"],
                "activity": ["activity_s1b", "activity_s2a", "activity_s3a", "activity_s4a","activity_s5a","activity_s6a","activity_s2b","activity_s3b","activity_s4b","activity_s5b","activity_s6b","activity_s7b"]
            }
        elif args.preset == 'infrared-only':
            sensor_config = {
                "infrared": ["infrared_1", "infrared_2", "infrared_3", "infrared_4"]
            }
        elif args.preset == 'motion-only':
            sensor_config = {
                "motion": ["motion_01", "motion_02", "motion_03", "motion_04", "motion_05", "motion_06", "motion_07", "motion_08", "motion_mf1","motion_mb1"]
            }
        elif args.preset == 'temperature-only':
            sensor_config = {
                "temperature": ["temperature_1", "temperature_2","temperature_3"]
            }
        elif args.preset == 'minimal':
            sensor_config = {
                "infrared": ["infrared_1", "infrared_2"],
                "motion": ["motion_01", "motion_02"]
            }
        logger.info(f"[DASHBOARD] Using preset '{args.preset}' with sensors: {sensor_config}")
    elif args.sensors:
        sensor_config = parse_sensor_config(args.sensors)
        if sensor_config:
            logger.info(f"[DASHBOARD] Using custom sensor configuration: {sensor_config}")
        else:
            logger.warning(f"[DASHBOARD] Invalid sensor configuration, using defaults")
    else:
        logger.info(f"[DASHBOARD] No sensor configuration specified, using defaults")
    
    try:
        # Initialize dashboard agent
        agent = DashboardAgent(
            agent_id=args.agent_id,
            mqtt_broker=args.mqtt_broker,
            mqtt_port=args.mqtt_port,
            sensor_config=sensor_config,
            transmission_interval=args.transmission_interval
        )
        logger.info("[DASHBOARD] Dashboard agent initialized and started")
        
        # Log final configuration
        logger.info(f"[DASHBOARD] Agent configuration:")
        logger.info(f"[DASHBOARD]   Agent ID: {args.agent_id}")
        logger.info(f"[DASHBOARD]   Transmission interval: {args.transmission_interval}s")
        logger.info(f"[DASHBOARD]   MQTT: {args.mqtt_broker}:{args.mqtt_port}")
        logger.info(f"[DASHBOARD]   Total sensors: {agent.local_state['total_sensors']}")
        logger.info(f"[DASHBOARD]   Sensor types: {list(agent.sensor_config.keys())}")
        for sensor_type, sensor_ids in agent.sensor_config.items():
            logger.info(f"[DASHBOARD]     {sensor_type}: {sensor_ids}")
        
        print("\n" + "="*60)
        print("DASHBOARD AGENT STARTED")
        print("="*60)
        print(f"Agent ID: {args.agent_id}")
        print(f"Monitoring {agent.local_state['total_sensors']} sensors")
        print(f"Transmission interval: {args.transmission_interval}s")
        print(f"MQTT: {args.mqtt_broker}:{args.mqtt_port}")
        print("\nSensor types being monitored:")
        for sensor_type, sensor_ids in agent.sensor_config.items():
            print(f"  {sensor_type}: {len(sensor_ids)} sensors")
        print("\nPress Ctrl+C to stop.\n")
        print("="*60)
        
        # Keep the main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("[DASHBOARD] Received keyboard interrupt")
        print("\nShutting down dashboard agent...")
    except Exception as e:
        logger.error(f"[DASHBOARD] Error in dashboard agent: {e}")
        import traceback
        logger.error(f"[DASHBOARD] Traceback: {traceback.format_exc()}")
    finally:
        if 'agent' in locals():
            agent.stop()
        logger.info("[DASHBOARD] Dashboard agent stopped")

if __name__ == "__main__":
    main() 
