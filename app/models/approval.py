from sqlalchemy import Column, Integer, String, ForeignKey
from app.models.base import Base, TimestampMixin


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True, index=True)

    entity_type = Column(String(50))   # "bill", "expense", "material"
    entity_id = Column(Integer)        # reference id

    status = Column(
        String(50),
        default="Pending"  # Pending → Approved → Rejected
    )

    requested_by = Column(Integer, nullable=False)
    approved_by = Column(Integer, nullable=True)

    remarks = Column(String(255), nullable=True)