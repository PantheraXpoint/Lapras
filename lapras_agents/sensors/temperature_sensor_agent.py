#!/usr/bin/env python3
import logging
import re
from typing import Optional, Any, Dict

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lapras_agents.monnit_sensor_agent import MonnitSensorAgent

logger = logging.getLogger(__name__)

class TemperatureSensorAgent(MonnitSensorAgent):
    """Temperature sensor agent using Monnit temperature sensors."""
    
    def __init__(self, virtual_agent_id: str = "any", 
                 hot_threshold: float = 28.0, cold_threshold: float = 22.0,
                 mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883):
        
        # Temperature thresholds (configurable)
        self.hot_threshold = hot_threshold
        self.cold_threshold = cold_threshold
        
        # Initialize parent class
        super().__init__("temperature", virtual_agent_id, mqtt_broker, mqtt_port)
        
        # Start sensor discovery and initialization
        self.start_sensor()
        
        logger.info(f"[{self.agent_id}] TemperatureSensorAgent initialized with thresholds: cold<{cold_threshold}°C<normal<{hot_threshold}°C<hot")
    
    def process_sensor_reading(self, result: Dict, sensor_name: str, sensor_id: int) -> tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """Process temperature sensor reading from Monnit API result."""
        try:
            # Get the raw reading (e.g., "26.9° C")
            raw_reading = result.get('CurrentReading', '')
            
            if not raw_reading or raw_reading == 'No data':
                return None, None, None
            
            # Extract numeric temperature value using regex
            # Pattern matches: "26.9° C" format specifically
            temp_pattern = r'([+-]?\d+\.?\d*)\s*°\s*C'
            match = re.search(temp_pattern, str(raw_reading))
            
            if not match:
                # Fallback to more flexible pattern
                logger.warning(f"[{self.agent_id}] Temperature reading '{raw_reading}' doesn't match expected format 'XX.X° C', trying fallback")
                temp_pattern_fallback = r'([+-]?\d+\.?\d*)\s*°?\s*C'
                match = re.search(temp_pattern_fallback, str(raw_reading), re.IGNORECASE)
                
                if not match:
                    logger.warning(f"[{self.agent_id}] Could not parse temperature from: {raw_reading}")
                    return None, None, None
            
            temperature = float(match.group(1))
            
            # Determine temperature status based on thresholds
            if temperature >= self.hot_threshold:
                temp_status = "hot"
            elif temperature <= self.cold_threshold:
                temp_status = "cold"
            else:
                temp_status = "normal"
            
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
                "temperature_status": temp_status,
                "hot_threshold": self.hot_threshold,
                "cold_threshold": self.cold_threshold,
                "battery_level": battery_level,
                "signal_strength": signal_strength,
                "status": status,
                "last_communication": last_comm_formatted
            }
            
            return round(temperature, 1), "°C", metadata
            
        except Exception as e:
            logger.error(f"[{self.agent_id}] Error processing temperature reading for {sensor_name}: {e}")
            return None, None, None
    
    def set_thresholds(self, hot_threshold: float, cold_threshold: float):
        """Update temperature thresholds."""
        self.hot_threshold = hot_threshold
        self.cold_threshold = cold_threshold
        logger.info(f"[{self.agent_id}] Temperature thresholds updated: cold<{cold_threshold}°C<normal<{hot_threshold}°C<hot") 