from Phidget22.Phidget import *
from Phidget22.Devices.IR import *
import time
import logging

# Logger will use the configuration set by the startup script
logger = logging.getLogger(__name__)

class PhidgetIRController:
    """Base class for PhidgetIR control functionality"""
    
    def __init__(self, serial=164793, code_handler=None):
        """
        Initialize PhidgetIR controller
        
        Args:
            serial (int): Device serial number, -1 for any device
            code_handler: Optional code event handler function
        """
        logger.debug("Initializing PhidgetIR controller")
        
        self.ir_phidget = IR()
        
        # Set up event handlers
        self.ir_phidget.setOnAttachHandler(self._on_attach)
        self.ir_phidget.setOnDetachHandler(self._on_detach)
        self.ir_phidget.setOnErrorHandler(self._on_error)
        
        if code_handler:
            self.ir_phidget.setOnCodeHandler(code_handler)
        
        self._open_ir_phidget(serial)
    
    def _open_ir_phidget(self, serial):
        """Open the IR Phidget device"""
        try:
            logger.debug(f"Attempting to open IRPhidget with serial: {serial}")
            
            if serial >= 0:
                self.ir_phidget.setDeviceSerialNumber(serial)
            
            # Open and wait for attachment
            logger.debug("Waiting for attachment...")
            self.ir_phidget.openWaitForAttachment(5000)  # 5 second timeout
            
        except PhidgetException as e:
            logger.error(f"Failed to open IRPhidget: {e}")
            raise
    
    def transmit_raw(self, raw_data):
        """
        Transmit raw IR data
        
        Args:
            raw_data (list): List of pulse/space times in microseconds
            
        Returns:
            bool: True if transmission successful, False otherwise
        """
        try:
            if not self.ir_phidget.getAttached():
                logger.error("IRPhidget not attached")
                return False
            
            self.ir_phidget.transmitRaw(raw_data, 0, 0, 100000)  # carrier=0, duty=0, gap=100ms
            return True
            
        except PhidgetException as e:
            logger.error(f"Transmission failed: {e}")
            return False
    
    def close(self):
        """Close the IR Phidget device"""
        try:
            if self.ir_phidget.getIsOpen():
                self.ir_phidget.close()
                logger.debug("IRPhidget closed")
        except PhidgetException as e:
            logger.error(f"Error closing IRPhidget: {e}")
    
    def _on_attach(self, phidget):
        """Handle attach event"""
        logger.debug("IRPhidget attached")
    
    def _on_detach(self, phidget):
        """Handle detach event"""
        logger.debug("IRPhidget detached")
    
    def _on_error(self, phidget, code, description):
        """Handle error event"""
        logger.debug(f"IRPhidget error: {code} - {description}")


class AirConditionerController(PhidgetIRController):
    """Air Conditioner control using PhidgetIR"""
    
    # IR command data (converted from Java arrays)
    TEMP_UP = [4430, 4480, 540, 1710, 490, 590, 540, 590, 510, 590, 540, 590, 540, 590, 510,
               590, 540, 590, 510, 620, 520, 1720, 510, 590, 540, 590, 510, 620, 510, 1750, 480, 590, 540, 590, 510, 1720,
               540, 1720, 520, 590, 510, 590, 540, 22150, 4450, 4480, 520, 1710, 520, 610, 490, 590, 540, 590, 510, 620,
               520, 580, 540, 590, 520, 580, 540, 590, 540, 1700, 530, 600, 510, 590, 530, 600, 530, 1700, 540, 590, 520,
               580, 540, 1720, 520, 1720, 510, 590, 530, 600, 510, 22210, 4420, 4480, 540, 1690, 520, 590, 540, 580, 520,
               590, 540, 610, 510, 620, 490, 590, 530, 630, 480, 650, 490, 1710, 520, 590, 530, 620, 490, 610, 520, 1740,
               490, 620, 500, 630, 490, 1740, 510, 1720, 510, 620, 490, 610, 520]
    
    TEMP_DOWN = [4430, 4480, 540, 1690, 510, 590, 540, 590, 510, 590, 540, 590, 540, 590,
                 510, 600, 540, 610, 490, 620, 510, 1720, 510, 590, 540, 590, 510, 1730, 530, 600, 510, 590, 540, 590, 510,
                 1720, 540, 1690, 540, 590, 510, 600, 530, 22160, 4450, 4480, 520, 1690, 540, 590, 510, 590, 540, 590, 520,
                 610, 520, 580, 550, 610, 490, 590, 540, 590, 540, 1690, 540, 590, 510, 590, 540, 1720, 520, 590, 540, 610,
                 490, 590, 540, 1720, 510, 1720, 510, 590, 540, 620, 490]
    
    TURN_ON = [460, 530, 470, 520, 470, 520, 480, 510, 480, 520, 460, 530, 470, 520, 470,
               520, 470, 520, 480, 520, 470, 520, 470, 3010, 2940, 8960, 530, 1490, 500, 490, 500, 490, 440, 550, 460, 530,
               470, 530, 460, 530, 470, 520, 470, 520, 470, 1540, 500, 470, 460, 530, 470, 1540, 500, 460, 480, 1510, 520,
               1460, 530, 490, 440, 1570, 420, 1540, 470, 1510, 480, 1500, 470, 1520, 470, 1510, 480, 1500, 480, 1510, 470,
               520, 470, 520, 470, 550, 450, 1590, 390, 1540, 450, 1530, 470, 520, 470, 530, 470, 520, 470, 520, 470, 520,
               480, 510, 480, 510, 480, 520, 460, 1520, 470, 1540, 450, 510, 480, 1510, 470, 550, 440, 1570, 460, 500, 500,
               490, 500, 490, 480, 520, 470, 520, 470, 520, 470, 520, 470, 1540, 450, 1560, 430, 1500, 470, 1510, 530]
    
    TURN_OFF = [530, 460, 530, 3000, 2890, 8960, 480, 1550, 430, 510, 470, 550, 510, 480,
                500, 490, 500, 490, 450, 540, 450, 540, 480, 510, 480, 1510, 480, 510, 530, 460, 530, 460, 540, 450, 530,
                1450, 530, 470, 530, 1450, 480, 1500, 480, 1560, 420, 1560, 420, 1560, 500, 1480, 510, 1480, 440, 1540, 450,
                1530, 450, 520, 470, 520, 470, 520, 480, 510, 480, 540, 450, 540, 440, 550, 440, 550, 450, 540, 450, 540,
                500, 490, 470, 520, 480, 510, 480, 520, 480, 1500, 530, 1480, 500, 460, 480, 510, 480, 510, 480, 510, 480,
                510, 490, 530, 450, 540, 450, 540, 450, 550, 450, 540, 450, 540, 500, 490, 500, 490, 500, 1480, 490, 1520,
                450]
    
    def __init__(self, phidget_serial=-1):
        """
        Initialize Air Conditioner Controller
        
        Args:
            phidget_serial (int): Phidget device serial number, -1 for any device
        """
        super().__init__(phidget_serial)
    
    def temp_down(self):
        """Decrease temperature"""
        return self.transmit_raw(self.TEMP_DOWN)
    
    def temp_up(self):
        """Increase temperature"""
        return self.transmit_raw(self.TEMP_UP)
    
    def turn_on(self):
        """Turn on air conditioner"""
        return self.transmit_raw(self.TURN_ON)
    
    def turn_off(self):
        """Turn off air conditioner"""
        return self.transmit_raw(self.TURN_OFF)


# Command line interface
import argparse
import sys

def main():
    """Main function for individual device control"""
    parser = argparse.ArgumentParser(
        description='Control individual air conditioner using PhidgetIR',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --device 164793 on       # Turn on device 164793
  %(prog)s --device 322207 off      # Turn off device 322207
  %(prog)s --device 164793 temp_up  # Increase temp on device 164793
  %(prog)s --device 322207 temp_down # Decrease temp on device 322207
  %(prog)s on                       # Use any available device
        """
    )
    
    parser.add_argument(
        'command',
        choices=['on', 'off', 'temp_up', 'temp_down', 'up', 'down'],
        help='Command to execute'
    )
    
    parser.add_argument(
        '--device', '-dev',
        type=int,
        help='Device serial number (164793 or 322207)'
    )
    
    parser.add_argument(
        '--serial', '-s',
        type=int,
        default=-1,
        help='PhidgetIR device serial number (default: any device)'
    )
    
    args = parser.parse_args()
    
    # Determine which device to use
    if args.device:
        device_serial = args.device
        print(f"Using device {device_serial}")
    else:
        device_serial = args.serial
        print(f"Using device {device_serial if device_serial >= 0 else 'any available'}")
    
    try:
        # Create controller for the specified device
        ac_controller = AirConditionerController(device_serial)
        print("Air Conditioner Controller ready!")
        
        # Execute the command
        command_map = {
            'on': ('Turning ON air conditioner', ac_controller.turn_on),
            'off': ('Turning OFF air conditioner', ac_controller.turn_off),
            'temp_up': ('Increasing temperature', ac_controller.temp_up),
            'temp_down': ('Decreasing temperature', ac_controller.temp_down),
            'up': ('Increasing temperature', ac_controller.temp_up),
            'down': ('Decreasing temperature', ac_controller.temp_down)
        }
        
        description, func = command_map[args.command]
        print(f"{description}...")
        
        success = func()
        if success:
            print(f"✓ Command '{args.command}' sent successfully")
            result_code = 0
        else:
            print(f"✗ Command '{args.command}' failed")
            result_code = 1
        
        # Close controller
        ac_controller.close()
        print("Controller closed.")
        
        sys.exit(result_code)
        
    except PhidgetException as e:
        print(f"Phidget error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()