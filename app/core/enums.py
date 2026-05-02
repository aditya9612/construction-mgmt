from enum import Enum

class InvoiceStatus(str, Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    PAID = "paid"

class AccountType(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    INCOME = "income"
    EXPENSE = "expense"
    EQUITY = "equity"


class PaymentMode(str, Enum):
    CASH = "Cash"
    BANK_TRANSFER = "BankTransfer"
    CHEQUE = "Cheque"
    UPI = "UPI"


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

class ProjectStatus(str, Enum):
    PLANNED = "PLANNED"
    ONGOING = "ONGOING"
    COMPLETED = "COMPLETED"
    ON_HOLD = "ON_HOLD"


class IssuePriority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class IssueStatus(str, Enum):
    OPEN = "Open"
    CLOSED = "Closed"


class TaskStatus(str, Enum):
    PLANNED = "Planned"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


# reuse existing enum
TaskPriority = IssuePriority


PRIORITY_MAP = {
    1: TaskPriority.LOW,
    2: TaskPriority.MEDIUM,
    3: TaskPriority.HIGH,
    4: TaskPriority.CRITICAL
}

REVERSE_PRIORITY_MAP = {
    TaskPriority.LOW: 1,
    TaskPriority.MEDIUM: 2,
    TaskPriority.HIGH: 3,
    TaskPriority.CRITICAL: 4
}


class MilestoneStatus(str, Enum):
    PLANNED = "Planned"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    DELAYED = "Delayed"

class WeatherType(str, Enum):
    SUNNY = "Sunny"
    RAINY = "Rainy"
    CLOUDY = "Cloudy"
    WINDY = "Windy"


class IssueCategory(str, Enum):
    MATERIAL = "Material"
    SAFETY = "Safety"
    DELAY = "Delay"


class SiteRequestStatus(str, Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


class SiteRequestType(str, Enum):
    MATERIAL = "Material"
    LABOUR = "Labour"
    EQUIPMENT = "Equipment"
    WORK = "Work"   # optional: keep only if you have generic work requests

class QCStatus(str, Enum):
    PASS = "Pass"
    FAIL = "Fail"


class EquipmentCondition(str, Enum):
    GOOD = "GOOD"
    REPAIR = "REPAIR"
    DAMAGED = "DAMAGED"
    MAINTENANCE = "MAINTENANCE"


class EquipmentStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    IN_PROJECT = "IN_PROJECT"
    IDLE = "IDLE"
    RENTED = "RENTED"
    MAINTENANCE = "MAINTENANCE"