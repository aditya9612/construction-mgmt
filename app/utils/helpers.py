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

