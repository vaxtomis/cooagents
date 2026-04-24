class NotFoundError(Exception):
    """Raised when a resource is not found (404)."""


class ConflictError(Exception):
    """Raised when operation conflicts with current state (409)."""

    def __init__(self, message: str, current_stage: str = None):
        super().__init__(message)
        self.current_stage = current_stage


class BadRequestError(Exception):
    """Raised when request input is invalid (400)."""


class IndexConvergenceError(Exception):
    """Raised when regenerate_workspace_md exhausts its CAS retry budget."""


class RecoveryScanError(Exception):
    """Raised when startup_recovery_scan hits an unrecoverable per-workspace
    failure after all per-row best-effort paths have been exhausted.

    Surfaces to operators via the lifespan try/except — does NOT block boot.
    """
