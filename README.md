# LAPRAS: A Smart Middleware for IoT enabled Urban Spaces

For detailed information about Lapras, a developer documentation, as well as quick start guides, please consult the [Lapras Wiki](https://github.com/PantheraXpoint/lapras-py/wiki)!

## File Structure

```
lapras-py/
├── lapras_middleware/
│   ├── virtual_agent.py      # VirtualAgent base class
│   ├── sensor_agent.py       # SensorAgent base class
│   ├── event.py              # Message structures
│   └── ...
├── lapras_agents/
│   ├── aircon_agent.py       # AirconAgent (VirtualAgent)
│   ├── infrared_sensor_agent.py  # InfraredSensorAgent (SensorAgent)
│   └── ...
├── start_aircon_agent.py     # Start AirconAgent
├── start_infrared_sensor.py  # Start InfraredSensorAgent
├── start_context_rule_manager.py  # Start ContextRuleManager
└── test_new_architecture.py  # Test script
```
