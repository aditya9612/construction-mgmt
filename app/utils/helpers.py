from dataclasses import dataclass
from typing import Optional


@dataclass
class AppError(Exception):
    status_code: int
    message: str


class NotFoundError(AppError):
    def __init__(self, message: str = "Not found"):
        super().__init__(status_code=404, message=message)


class ConflictError(AppError):
    def __init__(self, message: str = "Conflict"):
        super().__init__(status_code=409, message=message)


class PermissionDeniedError(AppError):
    def __init__(self, message: str = "Permission denied"):
        super().__init__(status_code=403, message=message)


class ValidationError(AppError):
    def __init__(self, message: str = "Validation error"):
        super().__init__(status_code=422, message=message)


class BadRequestError(AppError):
    def __init__(self, message: str = "Bad request"):
        super().__init__(status_code=400, message=message)


class AuthenticationError(AppError):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(status_code=401, message=message)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Access forbidden"):
        super().__init__(status_code=403, message=message)


class AlreadyExistsError(AppError):
    def __init__(self, message: str = "Resource already exists"):
        super().__init__(status_code=409, message=message)


class InvalidStateError(AppError):
    def __init__(self, message: str = "Invalid state for this operation"):
        super().__init__(status_code=400, message=message)


class RateLimitError(AppError):
    def __init__(self, message: str = "Too many requests"):
        super().__init__(status_code=429, message=message)


class ServiceUnavailableError(AppError):
    def __init__(self, message: str = "Service temporarily unavailable"):
        super().__init__(status_code=503, message=message)


class DataIntegrityError(AppError):
    def __init__(self, message: str = "Data integrity issue"):
        super().__init__(status_code=500, message=message)