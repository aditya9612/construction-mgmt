from sqlalchemy import (
    Boolean,
    Column,
    DECIMAL,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from app.core.enums import SkillType
from app.models.base import Base
from sqlalchemy import Enum as SAEnum

# =====================================================
# UNIT MASTER
# =====================================================


class Unit(Base):

    __tablename__ = "units"

    id = Column(Integer, primary_key=True)

    name = Column(String(100), unique=True, nullable=False)

    unique_code = Column(String(50), unique=True, nullable=True)

    category = Column(String(100), nullable=True)

    is_active = Column(Boolean, default=True)


# =====================================================
# LABOUR TYPE MASTER
# =====================================================


class LabourType(Base):

    __tablename__ = "labour_types"

    id = Column(Integer, primary_key=True)

    name = Column(String(100), unique=True, nullable=False)

    unique_code = Column(String(50), unique=True, nullable=True)

    category = Column(String(100), nullable=True)

    skill_category = Column(SAEnum(SkillType),nullable=False
)

    default_daily_wage = Column(DECIMAL(18, 2), nullable=False)

    default_working_hours = Column(Integer, default=8)

    default_ot_rate_per_hour = Column(DECIMAL(18, 2), nullable=True)

    is_active = Column(Boolean, default=True)


# =====================================================
# ACTIVITY TYPE MASTER
# =====================================================


class ActivityType(Base):

    __tablename__ = "activity_types"

    id = Column(Integer, primary_key=True)

    name = Column(String(100), unique=True, nullable=False)

    unique_code = Column(String(50), unique=True, nullable=True)

    category = Column(String(100), nullable=True)

    default_unit_id = Column(
        Integer,
        ForeignKey("units.id"),
        nullable=True
    )

    is_active = Column(Boolean, default=True)

    default_unit = relationship("Unit")


# =====================================================
# MATERIAL MASTER
# =====================================================


class MaterialMaster(Base):

    __tablename__ = "material_master"

    id = Column(Integer, primary_key=True)

    name = Column(String(150), nullable=False)

    unit = Column(String(50), nullable=False)

    unique_code = Column(String(50), unique=True, nullable=True)

    category = Column(String(100), nullable=True)

    brand = Column(String(100), nullable=True)

    specification = Column(String(255), nullable=True)

    hsn_code = Column(String(50), nullable=True)

    default_rate = Column(DECIMAL(18, 2), nullable=True)

    minimum_stock_level = Column(Integer, default=0)

    is_active = Column(Boolean, default=True)
