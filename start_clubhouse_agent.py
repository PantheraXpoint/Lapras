#!/usr/bin/env python3
import logging
import time
import sys
import os
import argparse
import json

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lapras_agents.clubhouse_agent import ClubHouseAgent

def parse_sensor_config(sensor_args):
    """Parse sensor configuration from command line arguments."""
    sensor_config = {}
    
    if not sensor_args:
        return None  # Use default configuration
    
    for arg in sensor_args:
        if ":" in arg:
            # Format: sensor_type:sensor1,sensor2,sensor3
            sensor_type, sensor_list = arg.split(":", 1)
            sensor_ids = [s.strip() for s in sensor_list.split(",") if s.strip()]
            if sensor_ids:
                sensor_config[sensor_type] = sensor_ids
        else:
            logging.warning(f"Invalid sensor format: {arg}. Expected format: sensor_type:sensor1,sensor2")
    
    return sensor_config if sensor_config else None

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Start the Event Venue Agent with configurable sensors and preset modes')
    parser.add_argument('--agent-id', default=None, 
                       help='Agent ID for the event venue agent (default: auto-determined by preset)')
    parser.add_argument('--transmission-interval', type=float, default=0.5,
                       help='Transmission interval to context manager in seconds (default: 0.5)')
    parser.add_argument('--mqtt-broker', default="143.248.55.82",
                       help='MQTT broker address (default: 143.248.55.82)')
    parser.add_argument('--mqtt-port', type=int, default=1883,
                       help='MQTT broker port (default: 1883)')
    parser.add_argument('--sensors', '-s', nargs='*',
                       help='Sensor configuration. Format: sensor_type:sensor1,sensor2. ' + 
                            'Examples: infrared:infrared_1,infrared_2 distance:distance_1,distance_2')
    parser.add_argument('--preset', choices=['back', 'front', 'all'],
                       help='Use preset configurations for different venue areas (mode determined by loaded rules)')
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(module)s] - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    # Determine sensor configuration and preset mode
    sensor_config = None
    preset_mode = "back-read"  # Default preset (position-mode, will be overridden by rules)
    
    if args.preset:
        # Position-only presets with default mode (read for back/front, normal for all)
        if args.preset == 'back':
            preset_mode = "back-read"  # Default mode, can be changed by rules
            sensor_config = {
                "infrared": ["infrared_1", "infrared_2"],
                "distance": ["distance_1", "distance_2"]
            }
        elif args.preset == 'front':
            preset_mode = "front-read"  # Default mode, can be changed by rules
            sensor_config = {
                "infrared": ["infrared_3", "infrared_4"],
                "distance": ["distance_3", "distance_4"]
            }
        elif args.preset == 'all':
            preset_mode = "all-normal"  # Default mode, can be changed by rules
            sensor_config = {
                "infrared": ["infrared_1", "infrared_2", "infrared_3", "infrared_4"],
                "distance": ["distance_1", "distance_2", "distance_3", "distance_4"]
            }
        logger.info(f"[VENUE] Using position preset '{args.preset}' with default mode, sensors: {sensor_config}")
    elif args.sensors:
        sensor_config = parse_sensor_config(args.sensors)
        if sensor_config:
            logger.info(f"[VENUE] Using custom sensor configuration: {sensor_config}")
        else:
            logger.warning(f"[VENUE] Invalid sensor configuration, using defaults")
    else:
        logger.info(f"[VENUE] No preset or sensor configuration specified, using default back position")
    
    try:
        # Initialize event venue virtual agent
        agent = ClubHouseAgent(
            agent_id=args.agent_id,
            mqtt_broker=args.mqtt_broker,
            mqtt_port=args.mqtt_port,
            sensor_config=sensor_config,
            transmission_interval=args.transmission_interval,
            preset_mode=preset_mode
        )
        logger.info("[VENUE] Event Venue virtual agent initialized and started")
        
        # Log final configuration
        logger.info(f"[VENUE] Agent configuration:")
        logger.info(f"[VENUE]   Agent ID: {args.agent_id}")
        logger.info(f"[VENUE]   Preset mode: {preset_mode}")
        logger.info(f"[VENUE]   Transmission interval: {args.transmission_interval}s")
        logger.info(f"[VENUE]   MQTT: {args.mqtt_broker}:{args.mqtt_port}")
        logger.info(f"[VENUE]   Sensor types: {list(agent.sensor_config.keys())}")
        for sensor_type, sensor_ids in agent.sensor_config.items():
            logger.info(f"[VENUE]     {sensor_type}: {sensor_ids}")
        
        # Log preset-specific settings
        logger.info(f"[VENUE] Preset '{preset_mode}' configuration:")
        logger.info(f"[VENUE]   Light group: {agent.light_group}")
        logger.info(f"[VENUE]   Light settings: brightness={agent.light_settings['bri']}, hue={agent.light_settings['hue']}, sat={agent.light_settings['sat']}")
        
        # Handle different attribute structures for 'all' vs single presets
        if hasattr(agent, 'ir_device_serials') and isinstance(agent.ir_device_serials, list):
            logger.info(f"[VENUE]   IR devices: {agent.ir_device_serials}")
            logger.info(f"[VENUE]   Aircon mode: {agent.aircon_mode}")
        else:
            logger.info(f"[VENUE]   IR device: {agent.ir_device_serial}")
            logger.info(f"[VENUE]   Aircon temp command: {agent.aircon_temp_command}")
        
        # Keep the main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("[VENUE] Received keyboard interrupt")
    except Exception as e:
        logger.error(f"[VENUE] Error in Event Venue virtual agent: {e}")
        import traceback
        logger.error(f"[VENUE] Traceback: {traceback.format_exc()}")
    finally:
        if 'agent' in locals():
            agent.stop()
        logger.info("[VENUE] Event Venue virtual agent stopped")

if __name__ == "__main__":
    main() 