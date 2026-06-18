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
├── scripts/                  # Runnable entry points (run via `python -m scripts.<name>`)
│   ├── start_aircon_agent.py     # Start AirconAgent
│   ├── start_infrared_sensor.py  # Start InfraredSensorAgent
│   ├── start_context_rule_manager.py  # Start ContextRuleManager
│   └── ...                        # other start_*.py + run_agent.py
└── dashboard_interface/      # Streamlit dashboard + its backend modules
    ├── dashboard_app.py          # `streamlit run dashboard_interface/dashboard_app.py`
    └── ...
```

> **Running an agent:** entry-point scripts now live in `scripts/` and are launched
> as modules from the repo root, e.g. `python -m scripts.start_aircon_agent`.
