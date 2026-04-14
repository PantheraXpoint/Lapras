#!/usr/bin/env python3
import logging
import time
import sys
import os
import argparse
import json

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lapras_agents.aircon_agent import AirconAgent

def parse_sensor_config(sensor_args):
    """Parse sensor configuration from command line arguments."""
    sensor_config = {}
    
    if not sensor_args:
        return None  # Use default configuration
    
    for arg in sensor_args:
        if ":" in arg:
            # Format: sensor_type:sensor1,sensor2
            sensor_type, sensor_list = arg.split(":", 1)
            sensor_ids = [s.strip() for s in sensor_list.split(",") if s.strip()]
            if sensor_ids:
                sensor_config[sensor_type] = sensor_ids
        else:
            logging.warning(f"Invalid sensor format: {arg}. Expected format: sensor_type:sensor1,sensor2")
    
    return sensor_config if sensor_config else None

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Start the Aircon Agent with configurable sensors and IR controller')
    parser.add_argument('--agent-id', default="aircon", 
                       help='Agent ID for the aircon agent (default: aircon)')
    parser.add_argument('--transmission-interval', type=float, default=0.5,
                       help='Transmission interval to context manager in seconds (default: 0.5)')
    parser.add_argument('--mqtt-broker', default="143.248.55.82",
                       help='MQTT broker address (default: 143.248.55.82)')
    parser.add_argument('--mqtt-port', type=int, default=1883,
                       help='MQTT broker port (default: 1883)')
    parser.add_argument('--sensors', '-s', nargs='*',
                       help='Sensor configuration. Format: sensor_type:sensor1,sensor2. ' + 
                            'Examples: infrared:infrared_1,infrared_2 motion:motion_01,motion_02 activity:activity_s1a,activity_s2a')
    parser.add_argument('--preset', choices=['infrared-only', 'motion-only', 'activity-only', 'all-sensors'],
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
        if args.preset == 'infrared-only':
            sensor_config = {
                "infrared": ["infrared_1", "infrared_2"]
            }
        elif args.preset == 'motion-only':
            sensor_config = {
                "motion": ["motion_01", "motion_02", "motion_03", "motion_04", "motion_05", "motion_06", "motion_07", "motion_08", "motion_mp1", "motion_mf1","motion_mb1"]
            }
        elif args.preset == 'activity-only':
            sensor_config = {
                "activity": ["activity_s1b", "activity_s2a", "activity_s3a", "activity_s4a","activity_s5a","activity_s6a","activity_s7a","activity_s2b","activity_s3b","activity_s4b","activity_s5b","activity_s6b","activity_s7b"]
            }
        elif args.preset == 'all-sensors':
            sensor_config = {
                "infrared": ["infrared_1", "infrared_2", "infrared_3", "infrared_4"],
                "distance": ["distance_1", "distance_2", "distance_3", "distance_4"],
                "temperature": ["temperature_3"],
                "motion": ["motion_01", "motion_02", "motion_03", "motion_04", "motion_05", "motion_06", "motion_07", "motion_08", "motion_mp1", "motion_mf1","motion_mb1"],
                "activity": ["activity_s1b", "activity_s2a", "activity_s3a", "activity_s4a","activity_s5a","activity_s6a","activity_s2b","activity_s3b","activity_s4b","activity_s5b","activity_s6b","activity_s7b"]
            }
        logger.info(f"[AIRCON] Using preset '{args.preset}' with sensors: {sensor_config}")
    elif args.sensors:
        sensor_config = parse_sensor_config(args.sensors)
        if sensor_config:
            logger.info(f"[AIRCON] Using custom sensor configuration: {sensor_config}")
        else:
            logger.warning(f"[AIRCON] Invalid sensor configuration, using defaults")
    else:
        logger.info(f"[AIRCON] No sensor configuration specified, using defaults")
    
    try:
        # Initialize aircon virtual agent
        agent = AirconAgent(
            agent_id=args.agent_id,
            mqtt_broker=args.mqtt_broker,
            mqtt_port=args.mqtt_port,
            sensor_config=sensor_config,
            transmission_interval=args.transmission_interval
        )
        logger.info("[AIRCON] Aircon virtual agent initialized and started")
        
        # Log final configuration
        logger.info(f"[AIRCON] Agent configuration:")
        logger.info(f"[AIRCON]   Agent ID: {args.agent_id}")
        logger.info(f"[AIRCON]   Transmission interval: {args.transmission_interval}s")
        logger.info(f"[AIRCON]   MQTT: {args.mqtt_broker}:{args.mqtt_port}")
        logger.info(f"[AIRCON]   IR Devices: 322207, 164793 (hardcoded)")
        logger.info(f"[AIRCON]   Sensor types: {list(agent.sensor_config.keys())}")
        for sensor_type, sensor_ids in agent.sensor_config.items():
            logger.info(f"[AIRCON]     {sensor_type}: {sensor_ids}")
        
        # Keep the main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("[AIRCON] Received keyboard interrupt")
    except Exception as e:
        logger.error(f"[AIRCON] Error in aircon virtual agent: {e}")
        import traceback
        logger.error(f"[AIRCON] Traceback: {traceback.format_exc()}")
    finally:
        if 'agent' in locals():
            agent.stop()
        logger.info("[AIRCON] Aircon virtual agent stopped")

if __name__ == "__main__":
    main()