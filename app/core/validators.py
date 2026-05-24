import re
from datetime import date
import os
import shutil
import uuid
from fastapi import (
    HTTPException,
    UploadFile,
)

# ================= CREATE USER =================


def validate_pan(v):
    if v is None:
        return v

    v = v.strip().upper()

    if not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", v):
        raise ValueError("Invalid PAN format (e.g., ABCDE1234F)")

    return v


def validate_aadhaar(v):
    if v is None:
        return v

    v = v.replace(" ", "").strip()

    if not re.match(r"^[0-9]{12}$", v):
        raise ValueError("Aadhaar must be 12 digits")

    return v


def validate_mobile(v):
    if v is None:
        return v

    digits = "".join(c for c in v if c.isdigit())

    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]

    if not re.match(r"^[6-9][0-9]{9}$", digits):
        raise ValueError("Invalid Indian mobile number")

    return digits


def validate_full_name(v):
    if v is None or v.strip() == "":
        return v

    v = " ".join(v.strip().split())

    if not re.match(r"^[A-Za-z. ]+$", v):
        raise ValueError("Full name must contain only alphabets, dots and spaces")

    return v


def validate_joining_date(v):
    if v and v > date.today():
        raise ValueError("Joining date cannot be in future")

    return v


def validate_password(v):
    if v is None:
        return v

    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")

    if not re.search(r"[A-Z]", v):
        raise ValueError("Password must contain at least one uppercase letter")

    if not re.search(r"[a-z]", v):
        raise ValueError("Password must contain at least one lowercase letter")

    if not re.search(r"[0-9]", v):
        raise ValueError("Password must contain at least one number")

    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
        raise ValueError("Password must contain at least one special character")

    return v


# ================= BILLING =================


def validate_positive_required(v):
    if v <= 0:
        raise ValueError("Must be greater than 0")
    return v


def validate_non_negative(v):
    if v < 0:
        raise ValueError("Cannot be negative")
    return v


def validate_bill_date(v):
    if v and v > date.today():
        raise ValueError("Future bill date not allowed")
    return v


# ================= COMMON VALIDATORS =================


def validate_zero_or_positive(v):
    if v < 0:
        raise ValueError("Value cannot be negative")
    return v


def validate_non_empty_string(v):
    if v is None or not v.strip():
        raise ValueError("Field cannot be empty")
    return v.strip()


def validate_gst(v):
    if v is None:
        return v

    v = v.strip().upper()

    if not re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", v):
        raise ValueError("Invalid GST number")

    return v


def validate_ifsc(v):
    if v is None:
        return v

    v = v.strip().upper()

    if not re.match(r"^[A-Z]{4}0[A-Z0-9]{6}$", v):
        raise ValueError("Invalid IFSC code")

    return v


def validate_upi(v):
    if v is None:
        return v

    v = v.strip()

    if not re.match(r"^[\w.-]+@[\w.-]+$", v):
        raise ValueError("Invalid UPI ID")

    return v


def validate_account_number(v):

    if v is None:
        return v

    v = v.strip()

    if not re.match(r"^[0-9]{9,18}$", v):
        raise ValueError("Invalid account number")

    return v


async def validate_and_save_image(
    file: UploadFile,
    upload_dir: str,
    prefix: str,
) -> str:

    # =====================================
    # ENSURE DIRECTORY EXISTS
    # =====================================

    os.makedirs(upload_dir, exist_ok=True)

    # =====================================
    # ALLOWED EXTENSIONS
    # =====================================

    allowed_extensions = {".png", ".jpg", ".jpeg"}

    ext = os.path.splitext(file.filename)[1].lower()

    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400, detail="Only PNG, JPG, JPEG files allowed."
        )

    # =====================================
    # MIME TYPE VALIDATION
    # =====================================

    allowed_content_types = {"image/png", "image/jpeg"}

    if file.content_type not in allowed_content_types:
        raise HTTPException(status_code=400, detail="Invalid image type.")

    # =====================================
    # FILE SIZE VALIDATION (5 MB)
    # =====================================

    MAX_FILE_SIZE = 5 * 1024 * 1024

    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Image size cannot exceed 5 MB.")

    await file.seek(0)

    # =====================================
    # UNIQUE SAFE FILE NAME
    # =====================================

    filename = f"{prefix}_{uuid.uuid4().hex}{ext}"

    file_path = f"{upload_dir}/{filename}"

    # =====================================
    # SAVE FILE
    # =====================================

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return file_path


def validate_start_end_dates(start_date, end_date):

    if start_date and end_date and end_date < start_date:
        raise ValueError("End date cannot be before start date")

    return end_date




ALLOWED_DRAWING_EXTENSIONS = {
    ".pdf",
    ".dwg",
    ".dxf",
    ".png",
    ".jpg",
    ".jpeg",
}


def validate_drawing_file(filename: str):

    ext = os.path.splitext(filename)[1].lower()

    if ext not in ALLOWED_DRAWING_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported drawing file type"
        )