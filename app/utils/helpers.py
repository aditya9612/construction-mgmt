from dataclasses import dataclass
from typing import Optional


@dataclass
class AppError(Exception):
    status_code: int
    message: str

    def __str__(self):
        return self.message


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

import math

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance in meters between two points
    on the earth (specified in decimal degrees)
    """
    if None in (lat1, lon1, lat2, lon2):
        return float('inf')

    # Convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])

    # Haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    r = 6371 # Radius of earth in kilometers
    return c * r * 1000 # Convert to meters

def safe_divide(numerator, denominator):
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)

def validate_percentage(value):
    if value < 0:
        return 0.0
    if value > 100:
        return 100.0
    return float(value)