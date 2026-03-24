from dataclasses import dataclass
from typing import Optional, Any


# -------------------------
# BASE ERROR
# -------------------------
@dataclass
class AppError(Exception):
    status_code: int
    message: str
    error_code: str
    details: Optional[Any] = None


# -------------------------
# ERROR CODES
# -------------------------
NOT_FOUND = "NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"
UNAUTHORIZED = "UNAUTHORIZED"
FORBIDDEN = "FORBIDDEN"
INTERNAL_ERROR = "INTERNAL_ERROR"
BAD_REQUEST = "BAD_REQUEST"
CONFLICT = "CONFLICT"


# -------------------------
# SPECIFIC ERRORS
# -------------------------
class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found", details=None):
        super().__init__(404, message, NOT_FOUND, details)


class BadRequestError(AppError):
    def __init__(self, message: str = "Bad request", details=None):
        super().__init__(400, message, BAD_REQUEST, details)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Unauthorized", details=None):
        super().__init__(401, message, UNAUTHORIZED, details)


class PermissionDeniedError(AppError):
    def __init__(self, message: str = "Access denied", details=None):
        super().__init__(403, message, FORBIDDEN, details)


class ValidationError(AppError):
    def __init__(self, message: str = "Validation error", details=None):
        super().__init__(422, message, VALIDATION_ERROR, details)


class ConflictError(AppError):
    def __init__(self, message: str = "Conflict", details=None):
        super().__init__(409, message, CONFLICT, details)


class InternalServerError(AppError):
    def __init__(self, message: str = "Internal server error", details=None):
        super().__init__(500, message, INTERNAL_ERROR, details)