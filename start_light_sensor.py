#!/usr/bin/env python3
import logging
import time
import sys
import os
import argparse

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lapras_agents.light_sensor_agent import LightSensorAgent

def main():
    # Set up logging
    parser = argparse.ArgumentParser(description='Start the light sensor agent for all Monnit light sensors')
    parser.add_argument('--virtual_agent_id', type=str, default="any", 
                       help='Virtual agent association (optional - sensor broadcasts to all agents anyway)')
    parser.add_argument('--sunlight_threshold', type=float, default=1000.0,
                       help='Light threshold above which status is "sunlight" (default: 1000.0 lux)')
    parser.add_argument('--indoor_threshold', type=float, default=10.0,
                       help='Light threshold above which status is "indoor_light" (default: 10.0 lux)')
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(module)s] - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    logger.info(f"[LIGHT_SENSOR] Starting light sensor agent")
    logger.info(f"[LIGHT_SENSOR] Light thresholds: no_light<{args.indoor_threshold}lux<indoor_light<{args.sunlight_threshold}lux<sunlight")
    logger.info(f"[LIGHT_SENSOR] Note: Sensor will broadcast to ALL virtual agents, regardless of virtual_agent_id setting")
    
    agent = None
    try:
        # Initialize light sensor agent
        agent = LightSensorAgent(
            virtual_agent_id=args.virtual_agent_id,
            sunlight_threshold=args.sunlight_threshold,
            indoor_threshold=args.indoor_threshold
        )
        logger.info(f"[LIGHT_SENSOR] Light sensor agent initialized")
        
        # Log sensor readings periodically for debugging
        last_log_time = 0
        
        # Keep the main thread alive
        while True:
            time.sleep(1)
            
            # Log sensor readings every 10 seconds for debugging
            current_time = time.time()
            if current_time - last_log_time > 10:
                try:
                    # Test read all sensors to show current values
                    value, unit, metadata = agent.read_sensor()
                    if metadata and "readings" in metadata:
                        readings_summary = []
                        total_sensors = metadata.get("total_sensors", 0)
                        successful_readings = len(metadata["readings"])
                        failed_readings = len(metadata.get("failed_readings", {}))
                        sunlight_count = 0
                        indoor_count = 0
                        no_light_count = 0
                        for sensor_key, reading_data in metadata["readings"].items():
                            sensor_name = reading_data.get("name", "Unknown")
                            lux_value = reading_data["value"]
                            light_status = reading_data["metadata"].get("light_status", "unknown")
                            if light_status == "sunlight":
                                sunlight_count += 1
                            elif light_status == "indoor_light":
                                indoor_count += 1
                            elif light_status == "no_light":
                                no_light_count += 1
                            readings_summary.append(f"{sensor_name}: {lux_value}lux ({light_status})")
                        
                        logger.info(f"[LIGHT_SENSOR] Status: {successful_readings}/{total_sensors} sensors successful, {failed_readings} failed")
                        logger.info(f"[LIGHT_SENSOR] Light levels: {sunlight_count} sunlight, {indoor_count} indoor, {no_light_count} no_light")
                        if successful_readings > 0:
                            logger.info(f"[LIGHT_SENSOR] Sample successful readings: {' | '.join(readings_summary[:3])}")
                            if len(readings_summary) > 3:
                                logger.info(f"[LIGHT_SENSOR] ... and {len(readings_summary) - 3} more successful sensors")
                        
                        # Show info about failed sensors
                        if failed_readings > 0:
                            failed_summary = []
                            for sensor_key, failed_data in metadata.get("failed_readings", {}).items():
                                failed_name = failed_data.get("name", "Unknown")
                                failed_reason = failed_data.get("reason", "unknown")
                                failed_reading = failed_data.get("raw_reading", "N/A")
                                failed_summary.append(f"{failed_name}: {failed_reason} (reading: '{failed_reading}')")
                            
                            logger.warning(f"[LIGHT_SENSOR] Failed sensors sample: {' | '.join(failed_summary[:3])}")
                            if len(failed_summary) > 3:
                                logger.warning(f"[LIGHT_SENSOR] ... and {len(failed_summary) - 3} more failed sensors")
                    else:
                        logger.warning(f"[LIGHT_SENSOR] No sensor readings available")
                except Exception as e:
                    logger.error(f"[LIGHT_SENSOR] Error reading sensors for logging: {e}")
                
                last_log_time = current_time
            
    except KeyboardInterrupt:
        logger.info("[LIGHT_SENSOR] Received keyboard interrupt")
    except Exception as e:
        logger.error(f"[LIGHT_SENSOR] Error in light sensor agent: {e}")
        import traceback
        logger.error(f"[LIGHT_SENSOR] Traceback: {traceback.format_exc()}")
    finally:
        if agent:
            logger.info("[LIGHT_SENSOR] Stopping agent...")
            agent.stop()
        logger.info("[LIGHT_SENSOR] Light sensor agent stopped")

if __name__ == "__main__":
    main() 