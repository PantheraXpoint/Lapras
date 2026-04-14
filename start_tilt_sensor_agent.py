#!/usr/bin/env python3
import logging
import argparse
import signal
import sys

from lapras_agents.tilt_sensor_agent import TiltSensorAgent

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def signal_handler(sig, frame):
    logger.info("Received interrupt signal. Shutting down tilt sensor agent...")
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description='Start Monnit Tilt Sensor Agent')
    parser.add_argument('--virtual-agent-id', type=str, default="any",
                        help='Virtual agent ID to publish to (default: any)')
    parser.add_argument('--tilt-threshold', type=float, default=30.0,
                        help='Tilt threshold in degrees (default: 30.0)')
    parser.add_argument('--mqtt-broker', type=str, default="143.248.55.82",
                        help='MQTT broker address (default: 143.248.55.82)')
    parser.add_argument('--mqtt-port', type=int, default=1883,
                        help='MQTT broker port (default: 1883)')
    
    args = parser.parse_args()
    
    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("Starting Monnit Tilt Sensor Agent...")
    logger.info(f"  Virtual Agent ID: {args.virtual_agent_id}")
    logger.info(f"  Tilt threshold: {args.tilt_threshold}°")
    logger.info(f"  MQTT broker: {args.mqtt_broker}:{args.mqtt_port}")
    
    try:
        # Create and start the tilt sensor agent
        agent = TiltSensorAgent(
            virtual_agent_id=args.virtual_agent_id,
            tilt_threshold=args.tilt_threshold,
            mqtt_broker=args.mqtt_broker,
            mqtt_port=args.mqtt_port
        )
        
        logger.info("Tilt sensor agent started successfully!")
        logger.info("Monitoring for tilt sensor data...")
        logger.info("Press Ctrl+C to stop the agent")
        
        # Keep the agent running
        while True:
            import time
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Tilt sensor agent stopped by user")
    except Exception as e:
        logger.error(f"Error running tilt sensor agent: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 