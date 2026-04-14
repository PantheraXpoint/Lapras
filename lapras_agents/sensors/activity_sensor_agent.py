#!/usr/bin/env python3
import logging
from typing import Optional, Any, Dict

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lapras_agents.monnit_sensor_agent import MonnitSensorAgent

logger = logging.getLogger(__name__)

class ActivitySensorAgent(MonnitSensorAgent):
    """Activity sensor agent using Monnit activity sensors."""
    
    def __init__(self, virtual_agent_id: str = "any",
                 mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        
        # Initialize parent class
        super().__init__("activity", virtual_agent_id, mqtt_broker, mqtt_port)
        
        # Start sensor discovery and initialization
        self.start_sensor()
        
        logger.info(f"[{self.agent_id}] ActivitySensorAgent initialized")
    
    def process_sensor_reading(self, result: Dict, sensor_name: str, sensor_id: int) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """Process activity sensor reading from Monnit API result."""
        try:
            # Get the raw reading (e.g., "Motion Detected" or "No Motion Detected")
            raw_reading = result.get('CurrentReading', '')
            
            if not raw_reading or raw_reading == 'No data':
                return None, None, None
            
            # Parse the exact activity sensor readings
            raw_reading_stripped = str(raw_reading).strip()
            
            if raw_reading_stripped == "Motion Detected":
                activity_detected = True
                activity_status = "active"
            elif raw_reading_stripped == "No Motion Detected":
                activity_detected = False
                activity_status = "inactive"
            else:
                # Log unexpected format but try to handle it
                logger.warning(f"[{self.agent_id}] Unexpected activity reading format: '{raw_reading}', attempting fallback parsing")
                raw_reading_lower = raw_reading_stripped.lower()
                if "motion detected" in raw_reading_lower and "no motion" not in raw_reading_lower:
                    activity_detected = True
                    activity_status = "active"
                else:
                    activity_detected = False
                    activity_status = "inactive"
            
            # Extract additional sensor information
            battery_level = result.get('BatteryLevel', 'Unknown')
            signal_strength = result.get('SignalStrength', 'Unknown')
            status = result.get('Status', 'Unknown')
            
            # Format last communication time
            last_comm = result.get('LastCommunicationDate', '')
            last_comm_formatted = "Unknown"
            if last_comm and '/Date(' in last_comm:
                try:
                    timestamp_ms = int(last_comm.split('(')[1].split(')')[0])
                    from datetime import datetime
                    last_comm_formatted = datetime.fromtimestamp(timestamp_ms/1000).strftime("%Y-%m-%d %H:%M:%S")
                except:
                    last_comm_formatted = last_comm
            
            # Prepare metadata
            metadata = {
                "sensor_name": sensor_name,
                "sensor_id": sensor_id,
                "raw_reading": raw_reading,
                "activity_status": activity_status,
                "activity_detected": activity_detected,
                "battery_level": battery_level,
                "signal_strength": signal_strength,
                "status": status,
                "last_communication": last_comm_formatted
            }
            
            return activity_detected, "boolean", metadata
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error processing activity reading for {sensor_name}: {e}")
            return None, None, None 