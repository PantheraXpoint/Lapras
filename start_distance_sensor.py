#!/usr/bin/env python3
import logging
import time
import sys
import os
import argparse

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lapras_agents.distance_sensor_agent import DistanceSensorAgent

def main():
    # Set up logging
    parser = argparse.ArgumentParser(description='Start the distance sensor agent with dynamic channel discovery')
    parser.add_argument('--channel', type=int, default=0, 
                       help='Starting channel number (used as a hint for channel discovery)')
    parser.add_argument('--sensor_id', type=str, default="distance", 
                       help='Sensor ID for the multi-channel distance sensor')
    parser.add_argument('--virtual_agent_id', type=str, default="any", 
                       help='Virtual agent association (optional - sensor broadcasts to all agents anyway)')
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(module)s] - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    logger.info(f"[DISTANCE_SENSOR] Starting distance sensor agent with channel hint: {args.channel}")
    logger.info(f"[DISTANCE_SENSOR] Note: Sensor will broadcast to ALL virtual agents, regardless of virtual_agent_id setting")
    
    agent = None
    try:
        # Initialize distance sensor agent
        agent = DistanceSensorAgent(
            sensor_id=args.sensor_id,
            virtual_agent_id=args.virtual_agent_id,
            channel=args.channel
        )
        
        # Log sensor readings periodically for debugging
        last_log_time = 0
        
        # Keep the main thread alive
        while True:
            time.sleep(1)
            
            # Log sensor readings every 5 seconds for debugging
            current_time = time.time()
            if current_time - last_log_time > 5:
                try:
                    # Test read all sensors to show current values
                    value, unit, metadata = agent.read_sensor()
                    if metadata and "readings" in metadata:
                        readings_summary = []
                        total_channels = len(agent.channels)
                        successful_readings = len(metadata["readings"])
                        near_count = 0
                        
                        for channel_key, reading_data in metadata["readings"].items():
                            channel_num = channel_key.replace("channel_", "")
                            proximity = reading_data["metadata"].get("proximity_status", "unknown")
                            distance = reading_data["value"]
                            if proximity == "near":
                                near_count += 1
                            readings_summary.append(f"Ch{channel_num}: {distance}cm ({proximity})")
                        
                        logger.info(f"[DISTANCE_SENSOR] Status: {successful_readings}/{total_channels} channels successful, {near_count} near")
                        if successful_readings > 0:
                            logger.info(f"[DISTANCE_SENSOR] Sample readings: {' | '.join(readings_summary[:3])}")
                            if len(readings_summary) > 3:
                                logger.info(f"[DISTANCE_SENSOR] ... and {len(readings_summary) - 3} more channels")
                        
                        # Show info about failed channels (if any)
                        failed_channels = total_channels - successful_readings
                        if failed_channels > 0:
                            logger.warning(f"[DISTANCE_SENSOR] {failed_channels} channels failed to read")
                            
                    else:
                        logger.warning(f"[DISTANCE_SENSOR] No sensor readings available")
                except Exception as e:
                    logger.error(f"[DISTANCE_SENSOR] Error reading sensors for logging: {e}")
                
                last_log_time = current_time
            
    except KeyboardInterrupt:
        logger.info("[DISTANCE_SENSOR] Received keyboard interrupt")
    except Exception as e:
        logger.error(f"[DISTANCE_SENSOR] Error in distance sensor agent: {e}")
        import traceback
        logger.error(f"[DISTANCE_SENSOR] Traceback: {traceback.format_exc()}")
    finally:
        if agent is not None:
            logger.info("[DISTANCE_SENSOR] Stopping agent...")
            try:
                agent.stop()
            except Exception as e:
                logger.error(f"[DISTANCE_SENSOR] Error stopping agent: {e}")
        logger.info("[DISTANCE_SENSOR] Distance sensor agent stopped")

if __name__ == "__main__":
    main() 