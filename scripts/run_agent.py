import json
import os
from lapras_middleware.agent import Agent, AgentConfig
from lapras_agents.microwave_agent import MicrowaveAgent

# Repo root is the parent of this scripts/ directory.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    # Load configuration (config.json lives at the repo root)
    config_path = os.path.join(REPO_ROOT, 'config.json')
    with open(config_path, 'r') as f:
        config = AgentConfig.from_stream(f)
    
    # Create and start the agent
    agent = Agent(MicrowaveAgent, config)
    agent.start()
    
    try:
        # Keep the main thread alive
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")

if __name__ == "__main__":
    main()