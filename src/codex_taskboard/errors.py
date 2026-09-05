"""Application errors translated by the HTTP layer."""


class TaskboardError(Exception):
    """Base class for expected, user-facing errors."""

    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, details: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(TaskboardError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(TaskboardError):
    status_code = 409
    code = "VERSION_CONFLICT"


class ValidationError(TaskboardError):
    status_code = 422
    code = "VALIDATION_ERROR"


class UnsupportedError(TaskboardError):
    status_code = 501
    code = "NOT_IMPLEMENTED"


class AppServerError(TaskboardError):
    status_code = 502
    code = "CODEX_APP_SERVER_ERROR"


class UsageLimitExceeded(AppServerError):
    status_code = 429
    code = "USAGE_LIMIT_EXCEEDED"

    def __init__(
        self,
        message: str = "Codex usage limit exceeded",
        *,
        error: object | None = None,
        resets_at: str | float | int | None = None,
    ) -> None:
        super().__init__(message, details=error)
        self.error = error
        self.resets_at = resets_at
