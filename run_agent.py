import json
from lapras_middleware.agent import Agent, AgentConfig
from lapras_agents.microwave_agent import MicrowaveAgent

def main():
    # Load configuration
    with open('config.json', 'r') as f:
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