#!/usr/bin/env python3
import logging
import re
from typing import Optional, Any, Dict

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lapras_agents.monnit_sensor_agent import MonnitSensorAgent

logger = logging.getLogger(__name__)

class TiltSensorAgent(MonnitSensorAgent):
    """Tilt sensor agent using Monnit tilt sensors."""
    
    def __init__(self, virtual_agent_id: str = "any", 
                 tilt_threshold: float = 30.0,
                 mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        
        # Tilt threshold in degrees (configurable)
        self.tilt_threshold = tilt_threshold
        
        # Initialize parent class
        super().__init__("tilt", virtual_agent_id, mqtt_broker, mqtt_port)
        
        # Start sensor discovery and initialization
        self.start_sensor()
        
        logger.info(f"[{self.agent_id}] TiltSensorAgent initialized with tilt threshold: {tilt_threshold}°")
    
    def process_sensor_reading(self, result: Dict, sensor_name: str, sensor_id: int) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """Process tilt sensor reading from Monnit API result."""
        try:
            # Get the raw reading
            raw_reading = result.get('CurrentReading', '')
            
            if not raw_reading or raw_reading == 'No data':
                return None, None, None
            
            logger.info(f"[{self.agent_id}] Processing tilt sensor reading: '{raw_reading}' for sensor {sensor_name}")
            
            # Initialize variables
            tilt_angle = None
            tilt_status = "unknown"
            is_tilted = False
            unit = "degrees"
            
            # Try to parse different possible formats
            raw_reading_str = str(raw_reading).strip()
            
            # Format 1: Angle in degrees (e.g., "45.2°", "45.2 degrees", "45.2 deg")
            angle_patterns = [
                r'([+-]?\d+\.?\d*)\s*°\s*',
                r'([+-]?\d+\.?\d*)\s*degrees?\s*',
                r'([+-]?\d+\.?\d*)\s*deg\s*'
            ]
            
            for pattern in angle_patterns:
                match = re.search(pattern, raw_reading_str, re.IGNORECASE)
                if match:
                    tilt_angle = float(match.group(1))
                    unit = "degrees"
                    break
            
            # Format 2: Status-based (e.g., "Tilted", "Not Tilted", "Upright", "Horizontal")
            if tilt_angle is None:
                raw_reading_lower = raw_reading_str.lower()
                
                if "tilted" in raw_reading_lower and "not" not in raw_reading_lower:
                    is_tilted = True
                    tilt_status = "tilted"
                    tilt_angle = None  # No specific angle provided
                    unit = "boolean"
                elif "not tilted" in raw_reading_lower or "upright" in raw_reading_lower:
                    is_tilted = False
                    tilt_status = "upright"
                    tilt_angle = None
                    unit = "boolean"
                elif "horizontal" in raw_reading_lower:
                    is_tilted = True
                    tilt_status = "horizontal"
                    tilt_angle = None
                    unit = "boolean"
                elif "vertical" in raw_reading_lower:
                    is_tilted = False
                    tilt_status = "vertical"
                    tilt_angle = None
                    unit = "boolean"
            
            # Format 3: Try to extract any numeric value as potential angle
            if tilt_angle is None:
                numeric_match = re.search(r'([+-]?\d+\.?\d*)', raw_reading_str)
                if numeric_match:
                    potential_angle = float(numeric_match.group(1))
                    # Reasonable range check for tilt angles
                    if -180 <= potential_angle <= 180:
                        tilt_angle = potential_angle
                        unit = "degrees"
                        logger.info(f"[{self.agent_id}] Extracted potential tilt angle: {potential_angle}° from '{raw_reading}'")
            
            # Determine tilt status based on angle (if we have one)
            if tilt_angle is not None:
                abs_angle = abs(tilt_angle)
                if abs_angle >= self.tilt_threshold:
                    is_tilted = True
                    tilt_status = "tilted"
                else:
                    is_tilted = False
                    tilt_status = "upright"
            
            # If we still couldn't parse anything meaningful, log for debugging
            if tilt_angle is None and tilt_status == "unknown":
                logger.warning(f"[{self.agent_id}] Could not parse tilt data from: '{raw_reading}'. Please check the format.")
                return None, None, None
            
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
                "tilt_status": tilt_status,
                "is_tilted": is_tilted,
                "tilt_angle": tilt_angle,
                "tilt_threshold": self.tilt_threshold,
                "battery_level": battery_level,
                "signal_strength": signal_strength,
                "status": status,
                "last_communication": last_comm_formatted
            }
            
            # Return the most appropriate value based on what we parsed
            if unit == "degrees" and tilt_angle is not None:
                return round(tilt_angle, 1), unit, metadata
            else:
                return is_tilted, "boolean", metadata
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error processing tilt reading for {sensor_name}: {e}")
            return None, None, None
    
    def set_tilt_threshold(self, threshold: float):
        """Update tilt threshold in degrees."""
        self.tilt_threshold = threshold
        logger.info(f"[{self.agent_id}] Tilt threshold updated to: {threshold}°") 