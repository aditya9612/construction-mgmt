# Import all models so SQLAlchemy metadata is populated.
from app.models.ai_prediction import AIPrediction  # noqa: F401
from app.models.boq import BOQ  # noqa: F401
from app.models.document import Document  # noqa: F401
from app.models.equipment import Equipment  # noqa: F401
from app.models.labour import Labour  # noqa: F401
from app.models.material import Material  # noqa: F401
from app.models.project import Project  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.owner import Owner
from app.models.contractor import Contractor
from app.models.expense import Expense
from app.models.invoice import Invoice
from app.models.final_measurement import FinalMeasurement
from app.models.ra_bill import RABill