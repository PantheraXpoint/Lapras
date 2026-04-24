#!/usr/bin/env python3
import logging
import time
import sys
import os
import argparse

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lapras_agents.activity_sensor_agent import ActivitySensorAgent

def main():
    # Set up logging
    parser = argparse.ArgumentParser(description='Start the activity sensor agent for all Monnit activity sensors')
    parser.add_argument('--virtual_agent_id', type=str, default="any", 
                       help='Virtual agent association (optional - sensor broadcasts to all agents anyway)')
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(module)s] - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    logger.info(f"[ACTIVITY_SENSOR] Starting activity sensor agent")
    logger.info(f"[ACTIVITY_SENSOR] Note: Sensor will broadcast to ALL virtual agents, regardless of virtual_agent_id setting")
    
    agent = None
    try:
        # Initialize activity sensor agent
        agent = ActivitySensorAgent(
            virtual_agent_id=args.virtual_agent_id
        )
        logger.info(f"[ACTIVITY_SENSOR] Activity sensor agent initialized")
        
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
                        active_count = 0
                        for sensor_key, reading_data in metadata["readings"].items():
                            sensor_name = reading_data.get("name", "Unknown")
                            activity_detected = reading_data["value"]
                            activity_status = reading_data["metadata"].get("activity_status", "unknown")
                            if activity_detected:
                                active_count += 1
                            readings_summary.append(f"{sensor_name}: {activity_status}")
                        
                        logger.info(f"[ACTIVITY_SENSOR] Status: {successful_readings}/{total_sensors} sensors successful, {failed_readings} failed, {active_count} active")
                        if successful_readings > 0:
                            logger.info(f"[ACTIVITY_SENSOR] Sample successful readings: {' | '.join(readings_summary[:3])}")
                            if len(readings_summary) > 3:
                                logger.info(f"[ACTIVITY_SENSOR] ... and {len(readings_summary) - 3} more successful sensors")
                        
                        # Show info about failed sensors
                        if failed_readings > 0:
                            failed_summary = []
                            for sensor_key, failed_data in metadata.get("failed_readings", {}).items():
                                failed_name = failed_data.get("name", "Unknown")
                                failed_reason = failed_data.get("reason", "unknown")
                                failed_reading = failed_data.get("raw_reading", "N/A")
                                failed_summary.append(f"{failed_name}: {failed_reason} (reading: '{failed_reading}')")
                            
                            logger.warning(f"[ACTIVITY_SENSOR] Failed sensors sample: {' | '.join(failed_summary[:3])}")
                            if len(failed_summary) > 3:
                                logger.warning(f"[ACTIVITY_SENSOR] ... and {len(failed_summary) - 3} more failed sensors")
                    else:
                        logger.warning(f"[ACTIVITY_SENSOR] No sensor readings available")
                except Exception as e:
                    logger.error(f"[ACTIVITY_SENSOR] Error reading sensors for logging: {e}")
                
                last_log_time = current_time
            
    except KeyboardInterrupt:
        logger.info("[ACTIVITY_SENSOR] Received keyboard interrupt")
    except Exception as e:
        logger.error(f"[ACTIVITY_SENSOR] Error in activity sensor agent: {e}")
        import traceback
        logger.error(f"[ACTIVITY_SENSOR] Traceback: {traceback.format_exc()}")
    finally:
        if agent:
            logger.info("[ACTIVITY_SENSOR] Stopping agent...")
            agent.stop()
        logger.info("[ACTIVITY_SENSOR] Activity sensor agent stopped")

if __name__ == "__main__":
    main() 