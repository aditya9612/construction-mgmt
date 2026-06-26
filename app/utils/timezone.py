from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Default local timezone for the application
LOCAL_TZ = ZoneInfo("Asia/Kolkata")

def get_utc_now() -> datetime:
    """Returns the current aware UTC datetime."""
    return datetime.now(timezone.utc)

def normalize_to_utc(dt: datetime) -> datetime:
    """
    Normalizes a datetime to an aware UTC datetime.
    If the datetime is naive, it assumes it's from the local timezone.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    # Naive datetimes are assumed to be in local time
    return dt.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)

def localize_datetime(dt: datetime) -> datetime:
    """
    Converts an aware datetime to the local timezone.
    If the datetime is naive, it assumes it's already local, but makes it aware.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(LOCAL_TZ)
    return dt.replace(tzinfo=LOCAL_TZ)

def make_naive_utc(dt: datetime) -> datetime:
    """
    Converts to UTC and returns a naive datetime (useful for DB saving if required).
    """
    return normalize_to_utc(dt).replace(tzinfo=None)

def get_naive_utc_now() -> datetime:
    """Returns the current naive UTC datetime."""
    return get_utc_now().replace(tzinfo=None)
