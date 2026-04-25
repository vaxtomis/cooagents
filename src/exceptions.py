class NotFoundError(Exception):
    """Raised when a resource is not found (404)."""


class ConflictError(Exception):
    """Raised when operation conflicts with current state (409)."""

    def __init__(self, message: str, current_stage: str | None = None):
        super().__init__(message)
        self.current_stage = current_stage


class BadRequestError(Exception):
    """Raised when request input is invalid (400)."""


class EtagMismatch(BadRequestError):
    """Raised when a conditional write precondition fails (HTTP 412).

    Phase 8b CAS path. ``current_hash`` is the value the server sees right
    now (None if the path does not exist); ``expected_hash`` is what the
    caller asserted via ``expected_prior_hash`` (None if they expected the
    path to not exist yet).
    """

    def __init__(
        self,
        message: str,
        *,
        current_hash: str | None = None,
        expected_hash: str | None = None,
    ) -> None:
        super().__init__(message)
        self.current_hash = current_hash
        self.expected_hash = expected_hash
