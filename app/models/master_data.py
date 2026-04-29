from sqlalchemy import Column, Integer, String
from app.models.base import Base


class Unit(Base):
    __tablename__ = "units"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)


class LabourType(Base):
    __tablename__ = "labour_types"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)


class ActivityType(Base):
    __tablename__ = "activity_types"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)


class MaterialMaster(Base):
    __tablename__ = "material_master"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    unit = Column(String(50), nullable=False)