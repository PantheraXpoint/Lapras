from typing import Optional, TYPE_CHECKING

# if TYPE_CHECKING:
#     from .event import EventDispatcher
#     from .agent import Agent

class Component:
    """Base class for all components in the LAPRAS system."""
    
    def __init__(self, event_dispatcher, agent):
        """Initialize a component with an event dispatcher and agent reference."""
        self.event_dispatcher = event_dispatcher
        self.agent = agent
        
    def start(self) -> None:
        """Start the component. Override this method in subclasses."""
        pass
        
    def stop(self) -> None:
        """Stop the component. Override this method in subclasses."""
        pass
        
    def handle_event(self, event) -> bool:
        """Handle an event. Override this method in subclasses."""
        return True

# class AgentComponent(Component):
#     """Base class for all agent components in the LAPRAS system."""
    
#     def __init__(self, context_manager, event_dispatcher):
#         """Initialize an agent component with context manager and event dispatcher."""
#         super().__init__(event_dispatcher, None)  # agent is None as it's not needed for agent components
#         self.context_manager = context_manager
        
#     def start(self) -> None:
#         """Start the agent component. Override this method in subclasses."""
#         pass
        
#     def stop(self) -> None:
#         """Stop the agent component. Override this method in subclasses."""
#         pass
        
#     def handle_event(self, event) -> bool:
#         """Handle an event. Override this method in subclasses."""
#         return True 