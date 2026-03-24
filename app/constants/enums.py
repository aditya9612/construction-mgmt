from enum import Enum


class RateType(str, Enum):
    PER_SQFT = "Per Sq Ft"
    PER_DAY = "Per Day"
    PER_ITEM = "Per Item"


class ExpenseType(str, Enum):
    MATERIAL = "Material"
    LABOR = "Labor"
    TRANSPORT = "Transport"
    MISC = "Misc"


class PaymentMode(str, Enum):
    CASH = "Cash"
    BANK = "Bank Transfer"
    UPI = "UPI"
    CHEQUE = "Cheque"


class ProjectType(str, Enum):
    RESIDENTIAL = "Residential"
    COMMERCIAL = "Commercial"


class ProjectStatus(str, Enum):
    ONGOING = "Ongoing"
    COMPLETED = "Completed"
    HOLD = "Hold"


class PaymentStatus(str, Enum):
    PAID = "Paid"
    UNPAID = "Unpaid"


class PaymentType(str, Enum):
    CREDIT = "Credit"
    DEBIT = "Debit"


class InvoiceType(str, Enum):
    OWNER = "Owner"
    CONTRACTOR = "Contractor"
    LABOR = "Labor"
    MATERIAL = "Material"