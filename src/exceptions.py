class NotFoundError(Exception):
    """Raised when a resource is not found (404)."""


class ConflictError(Exception):
    """Raised when operation conflicts with current state (409)."""

    def __init__(self, message: str, current_stage: str = None):
        super().__init__(message)
        self.current_stage = current_stage
