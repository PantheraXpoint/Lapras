class LaprasException(Exception):
    """Base exception class for LAPRAS system errors."""
    
    def __init__(self, message: str, cause: Exception = None):
        """Initialize the exception with a message and optional cause."""
        super().__init__(message)
        self.cause = cause 