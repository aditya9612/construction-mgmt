from sqlalchemy import Column, Integer, String, Float
from app.models.base import Base, TimestampMixin


class CADConversion(Base, TimestampMixin):
    __tablename__ = "cad_conversions"

    id = Column(Integer, primary_key=True, index=True)
    project_name = Column(String(255))
    file_path = Column(String(500))
    area = Column(Float)