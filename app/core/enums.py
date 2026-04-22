from enum import Enum


class AttendanceStatus(str, Enum):
    PRESENT = "Present"
    ABSENT = "Absent"
    HALF = "Half"


class PayrollStatus(str, Enum):
    PENDING = "Pending"
    PAID = "Paid"
    PARTIAL = "Partial"


class LabourStatus(str, Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"


class SkillType(str, Enum):
    SKILLED = "Skilled"
    UNSKILLED = "Unskilled"


class TransactionType(str, Enum):
    PURCHASE = "PURCHASE"
    USAGE = "USAGE"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    ADJUSTMENT = "ADJUSTMENT"


class IssueType(str, Enum):
    SYSTEM = "SYSTEM"
    SITE = "SITE"
    DAMAGE = "DAMAGE"
    LOSS = "LOSS"
    VENDOR = "VENDOR"
    TRANSFER = "TRANSFER"
    ADJUSTMENT = "ADJUSTMENT"
    PURCHASE = "PURCHASE"


class RateType(str, Enum):
    FIXED = "FIXED"
    PER_UNIT = "PER_UNIT"
    PER_KG = "PER_KG"
    PER_TON = "PER_TON"
    PER_BAG = "PER_BAG"


class TransferStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PurchaseStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class AttendanceStatus(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    HALF_DAY = "half_day"
