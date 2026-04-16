from sqlalchemy import Column, Integer, String, Numeric, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.base import Base


class Contractor(Base):
    __tablename__ = "contractors"

    id = Column(Integer, primary_key=True, index=True)
    contractor_id = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    work_type = Column(String(50), nullable=False)
    contact_number = Column(String(15), nullable=False, unique=True, index=True)
    gst_number = Column(String(20), nullable=True)
    rate_type = Column(String(50), nullable=False)

    total_work_assigned = Column(Numeric(12, 2), default=0)
    payment_given = Column(Numeric(12, 2), default=0)

    bank_details = Column(String(255), nullable=True)

    projects = relationship(
        "ContractorProject", back_populates="contractor", cascade="all, delete-orphan"
    )


class ContractorProject(Base):
    __tablename__ = "contractor_projects"

    id = Column(Integer, primary_key=True, index=True)

    contractor_id = Column(
        Integer, ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False
    )
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    contractor = relationship("Contractor", back_populates="projects")

    __table_args__ = (
        UniqueConstraint("contractor_id", "project_id", name="uq_contractor_project"),
    )
