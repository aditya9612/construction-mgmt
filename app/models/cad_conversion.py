from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import relationship
from app.models.base import Base, TimestampMixin


class CADConversion(Base, TimestampMixin):
    __tablename__ = "cad_conversions"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True)
    project_name = Column(String(255))
    file_path = Column(String(500))
    area = Column(Float)

    company = relationship("Company")