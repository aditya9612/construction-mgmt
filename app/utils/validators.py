import re
import magic
from datetime import date
from app.core.errors import BadRequestError


# -----------------------------------------
#  GENERIC VALIDATORS
# -----------------------------------------

def validate_positive(value, field_name: str = "Value"):
    if value is None or value <= 0:
        raise BadRequestError(f"{field_name} must be positive")


def validate_non_negative(value, field_name: str = "Value"):
    if value is None or value < 0:
        raise BadRequestError(f"{field_name} cannot be negative")


def validate_less_than(value1, value2, message: str):
    if value1 > value2:
        raise BadRequestError(message)


def validate_required_if(condition, value, message: str):
    if condition and not value:
        raise BadRequestError(message)


# -----------------------------------------
#  DATE VALIDATORS
# -----------------------------------------

def validate_not_future(input_date: date, field_name: str = "Date"):
    if input_date > date.today():
        raise BadRequestError(f"{field_name} cannot be in the future")


def validate_date_range(start_date: date, end_date: date):
    if end_date < start_date:
        raise BadRequestError("End date must be greater than start date")


# -----------------------------------------
#  STRING / FORMAT VALIDATORS
# -----------------------------------------

def validate_mobile(mobile: str):
    if not re.fullmatch(r"[6-9]\d{9}", mobile):
        raise BadRequestError("Invalid mobile number")


def validate_pan(pan: str):
    pan = pan.upper()
    if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", pan):
        raise BadRequestError("Invalid PAN format")
    return pan


def validate_aadhaar(aadhaar: str):
    if not re.fullmatch(r"\d{12}", aadhaar):
        raise BadRequestError("Aadhaar must be 12 digits")


def validate_gst(gst: str):
    gst = gst.upper()
    if not re.fullmatch(r"\d{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gst):
        raise BadRequestError("Invalid GST format")
    return gst


def validate_name(name: str, field_name="Name"):
    if not re.fullmatch(r"[A-Za-z ]{3,100}", name):
        raise BadRequestError(f"{field_name} must contain only alphabets (3–100 chars)")


def validate_enum(value: str, allowed: list, field_name="Field"):
    if value not in allowed:
        raise BadRequestError(f"{field_name} must be one of: {', '.join(allowed)}")


# -----------------------------------------
#  BUSINESS VALIDATORS
# -----------------------------------------

def validate_quantity(used, purchased):
    if used > purchased:
        raise BadRequestError("Used quantity cannot exceed purchased quantity")


def validate_payment(payment_given, total_amount):
    if payment_given > total_amount:
        raise BadRequestError("Payment cannot exceed total amount")


# -----------------------------------------
#  FILE VALIDATION (SECURITY)
# -----------------------------------------

def validate_file_size(contents: bytes, max_size_mb: int = 5):
    if len(contents) > max_size_mb * 1024 * 1024:
        raise BadRequestError(f"File must be less than {max_size_mb}MB")


def validate_file_extension(filename: str, allowed: list):
    ext = filename.split(".")[-1].lower()
    if ext not in allowed:
        raise BadRequestError("Invalid file type")


def validate_file_mime(contents: bytes, allowed_types: list):
    mime = magic.from_buffer(contents, mime=True)

    if not any(a in mime for a in allowed_types):
        raise BadRequestError("Invalid file content (MIME mismatch)")


# -----------------------------------------
#  SANITIZATION
# -----------------------------------------

def sanitize_string(value: str) -> str:
    if not value:
        return value
    return value.strip()


def sanitize_filename(filename: str) -> str:
    filename = filename.strip()
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)