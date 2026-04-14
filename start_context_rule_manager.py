#!/usr/bin/env python3

import logging
import sys
import os
import signal
import time
import threading
import argparse
import glob

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lapras_middleware.context_rule_manager import ContextRuleManager

class ContextRuleService:
    """Service that runs the combined context rule manager."""
    
    def __init__(self, rule_files=None):
        self.context_rule_manager = None
        self.running = False
        self._shutdown_event = threading.Event()
        self.logger = logging.getLogger(__name__)
        self.rule_files = rule_files or []
        
    def start(self):
        """Start the context rule service."""
        try:
            # Initialize context rule manager with rule files
            self.context_rule_manager = ContextRuleManager(
                mqtt_broker="143.248.55.82",
                mqtt_port=1883,
                rule_files=self.rule_files
            )
            self.logger.info("Context rule manager initialized")
            
            # Log loaded rules summary
            loaded_files = self.context_rule_manager.get_loaded_rule_files()
            if loaded_files:
                self.logger.info(f"Loaded {len(loaded_files)} rule files: {loaded_files}")
            else:
                self.logger.warning("No rule files loaded")
            
            self.running = True
            self.logger.info("Context Rule service started and monitoring agent state updates")
            
        except Exception as e:
            self.logger.error(f"Error starting Context Rule service: {str(e)}", exc_info=True)
            self.stop()
            raise
            
    def stop(self):
        """Stop the context rule service."""
        if not self.running:
            return
            
        self.running = False
        self._shutdown_event.set()
        
        if self.context_rule_manager:
            try:
                self.logger.info("Stopping context rule manager...")
                self.context_rule_manager.stop()
                self.logger.info("Context rule manager stopped")
            except Exception as e:
                self.logger.error(f"Error stopping context rule manager: {e}")
                
        self.logger.info("Context Rule service stopped")
        
    def run(self):
        """Run the service until shutdown signal is received."""
        try:
            self.start()
            
            # Keep the main thread alive until shutdown signal
            while not self._shutdown_event.is_set():
                time.sleep(1)
                
        except KeyboardInterrupt:
            self.logger.info("Received shutdown signal")
        except Exception as e:
            self.logger.error(f"Error in Context Rule service: {str(e)}", exc_info=True)
        finally:
            self.stop()

def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger = logging.getLogger(__name__)
    logger.info("Received shutdown signal")
    if 'service' in globals():
        service.stop()
    sys.exit(0)

def parse_rule_files(rule_args):
    """Parse rule file arguments, supporting wildcards and multiple files."""
    rule_files = []
    for arg in rule_args:
        if '*' in arg or '?' in arg:
            # Handle wildcards
            expanded_files = glob.glob(arg)
            if expanded_files:
                rule_files.extend(expanded_files)
            else:
                print(f"Warning: No files found matching pattern: {arg}")
        else:
            # Check if file exists
            if os.path.exists(arg):
                rule_files.append(arg)
            else:
                print(f"Warning: Rule file not found: {arg}")
    return rule_files

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Start the Context Rule Manager with flexible rule loading')
    parser.add_argument('--rules', '-r', nargs='*', 
                       help='Rule files to load (supports wildcards). Example: --rules lapras_middleware/rules/*.ttl')
    parser.add_argument('--rules-dir', 
                       help='Directory to load all .ttl files from')
    parser.add_argument('--mqtt-broker', default="143.248.55.82",
                       help='MQTT broker address (default: 143.248.55.82)')
    parser.add_argument('--mqtt-port', type=int, default=1883,
                       help='MQTT broker port (default: 1883)')
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(module)s] - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Parse rule files
    rule_files = []
    
    if args.rules:
        rule_files.extend(parse_rule_files(args.rules))
    
    if args.rules_dir:
        if os.path.isdir(args.rules_dir):
            pattern = os.path.join(args.rules_dir, "*.ttl")
            rule_files.extend(glob.glob(pattern))
        else:
            logger.error(f"Rules directory not found: {args.rules_dir}")
            sys.exit(1)
    
    # Default fallback if no rules specified
    if not rule_files:
        logger.info("No rule files specified, looking for default rules...")
        default_patterns = [
            "lapras_middleware/rules/*.ttl",
            "rules/*.ttl"
        ]
        for pattern in default_patterns:
            found_files = glob.glob(pattern)
            if found_files:
                rule_files.extend(found_files)
                logger.info(f"Found {len(found_files)} default rule files using pattern: {pattern}")
                break
        
        if not rule_files:
            logger.warning("No rule files found. Context manager will start without rules.")
        
    # Remove duplicates and sort
    rule_files = sorted(list(set(rule_files)))
    
    if rule_files:
        logger.info(f"[CONTEXT_RULE_MANAGER] Starting with {len(rule_files)} rule files:")
        for i, rule_file in enumerate(rule_files, 1):
            logger.info(f"[CONTEXT_RULE_MANAGER]   {i}. {rule_file}")
    else:
        logger.info("[CONTEXT_RULE_MANAGER] Starting without rule files")
    
    try:
        # Initialize and run context rule manager with proper MQTT configuration
        context_rule_manager = ContextRuleManager(
            mqtt_broker=args.mqtt_broker,
            mqtt_port=args.mqtt_port,
            rule_files=rule_files
        )
        
        logger.info("[CONTEXT_RULE_MANAGER] Context rule manager started")
        
        # Log final status
        loaded_files = context_rule_manager.get_loaded_rule_files()
        logger.info(f"[CONTEXT_RULE_MANAGER] Successfully loaded {len(loaded_files)} rule files")
        
        # Keep the main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("[CONTEXT_RULE_MANAGER] Received keyboard interrupt")
    except Exception as e:
        logger.error(f"[CONTEXT_RULE_MANAGER] Error in context rule manager: {e}")
    finally:
        if 'context_rule_manager' in locals():
            context_rule_manager.stop()
        logger.info("[CONTEXT_RULE_MANAGER] Context rule manager stopped")

if __name__ == "__main__":
    main() 