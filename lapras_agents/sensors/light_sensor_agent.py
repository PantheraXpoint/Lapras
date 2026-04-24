#!/usr/bin/env python3
import logging
import re
from typing import Optional, Any, Dict

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lapras_agents.monnit_sensor_agent import MonnitSensorAgent

logger = logging.getLogger(__name__)

class LightSensorAgent(MonnitSensorAgent):
    """Light sensor agent using Monnit light sensors."""
    
    def __init__(self, virtual_agent_id: str = "any",
                 sunlight_threshold: float = 1000.0, indoor_threshold: float = 10.0,
                 mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        
        # Light thresholds (configurable)
        # sunlight_threshold: lux level above which it's considered sunlight
        # indoor_threshold: lux level above which it's considered indoor light
        # Below indoor_threshold is considered "no light"
        self.sunlight_threshold = sunlight_threshold
        self.indoor_threshold = indoor_threshold
        
        # Initialize parent class
        super().__init__("light", virtual_agent_id, mqtt_broker, mqtt_port)
        
        # Start sensor discovery and initialization
        self.start_sensor()
        
        logger.info(f"[{self.agent_id}] LightSensorAgent initialized with thresholds: no_light<{indoor_threshold}lux<indoor_light<{sunlight_threshold}lux<sunlight")
    
    def process_sensor_reading(self, result: Dict, sensor_name: str, sensor_id: int) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """Process light sensor reading from Monnit API result."""
        try:
            # Get the raw reading (e.g., "27.78 lux, Light")
            raw_reading = result.get('CurrentReading', '')
            
            if not raw_reading or raw_reading == 'No data':
                return None, None, None
            
            # Extract numeric lux value using regex
            # Pattern matches: "27.78 lux, Light" format specifically
            lux_pattern = r'([+-]?\d+\.?\d*)\s*lux,?\s*Light'
            match = re.search(lux_pattern, str(raw_reading), re.IGNORECASE)
            
            if not match:
                # Fallback to more flexible pattern
                logger.warning(f"[{self.agent_id}] Light reading '{raw_reading}' doesn't match expected format 'XX.XX lux, Light', trying fallback")
                lux_pattern_fallback = r'([+-]?\d+\.?\d*)\s*lux'
                match = re.search(lux_pattern_fallback, str(raw_reading).lower())
                
                if not match:
                    logger.warning(f"[{self.agent_id}] Could not parse lux value from: {raw_reading}")
                    return None, None, None
            
            lux_value = float(match.group(1))
            
            # Determine light status based on thresholds
            if lux_value >= self.sunlight_threshold:
                light_status = "sunlight"
            elif lux_value >= self.indoor_threshold:
                light_status = "indoor_light"
            else:
                light_status = "no_light"
            
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
                "light_status": light_status,
                "sunlight_threshold": self.sunlight_threshold,
                "indoor_threshold": self.indoor_threshold,
                "battery_level": battery_level,
                "signal_strength": signal_strength,
                "status": status,
                "last_communication": last_comm_formatted
            }
            
            return round(lux_value, 2), "lux", metadata
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error processing light reading for {sensor_name}: {e}")
            return None, None, None
    
    def set_thresholds(self, sunlight_threshold: float, indoor_threshold: float):
        """Update light thresholds."""
        self.sunlight_threshold = sunlight_threshold
        self.indoor_threshold = indoor_threshold
        logger.info(f"[{self.agent_id}] Light thresholds updated: no_light<{indoor_threshold}lux<indoor_light<{sunlight_threshold}lux<sunlight") 