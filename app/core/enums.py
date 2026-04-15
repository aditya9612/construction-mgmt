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