# Import all models so SQLAlchemy metadata is populated.
from app.models.ai_prediction import AIPrediction  # noqa: F401
from app.models.boq import BOQ  # noqa: F401
# from app.models.document import Document  # noqa: F401
# from app.models.equipment import Equipment  # noqa: F401
from app.models.labour import Labour  # noqa: F401

# ================= EQUIPMENT =================
# from app.models.equipment import Equipment  # noqa: F401
from app.models.equipment import EquipmentUsage  # noqa: F401
from app.models.equipment import EquipmentMaintenance  # noqa: F401
from app.models.equipment import EquipmentRental  # noqa: F401
from app.models.equipment import EquipmentAuditLog  # noqa: F401

# ================= MATERIAL =================
from app.models.material import Material  # noqa: F401
from app.models.material import MaterialUsage 
from app.models.material import MaterialTransaction  # noqa: F401
from app.models.material import MaterialLedger  # noqa: F401
from app.models.material import Supplier  # noqa: F401
from app.models.material import PurchaseOrder  # noqa: F401
from app.models.material import MaterialTransfer  # noqa: F401

from app.models.project import Project  # noqa: F401
from app.models.user import User # noqa: F401
from app.models.owner import Owner
from app.models.contractor import Contractor
from app.models.expense import Expense
from app.models.invoice import Invoice
from app.models.invoice import Transaction
from app.models.final_measurement import FinalMeasurement
from app.models.billing import RABill
from app.models.approval import Approval
from app.models.work_order import WorkOrder
from app.models.user import UserAuditLog # noqa: F401
from app.models.user import ActivityLog # noqa: F401
from app.models.accountant import Account
from app.models.accountant import JournalEntry
from app.models.accountant import JournalLine
from app.models.accountant import FixedAsset
from app.models.alert import Alert
from app.models.cad_conversion import CADConversion
from app.models.master_data import Unit
from app.models.master_data import LabourType
from app.models.master_data import ActivityType
from app.models.master_data import MaterialMaster
from app.models.messages import Message
from app.models.settings import UserSettings
from app.models.chat import ChatSession, ChatMember, ChatMessage, MessageRead, MessageReaction