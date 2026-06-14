from pydantic import BaseModel, field_validator
from typing import Optional
from decimal import Decimal
from app.core import enums as e
from app.core.validators import validate_positive_decimal

# =====================================================

# COMMON

# =====================================================


class MasterDataBase(BaseModel):

    name: str

    # unique_code: Optional[str] = None

    category: Optional[str] = None

    @field_validator("name")
    def validate_name(cls, v):

        if not v.strip():
            raise ValueError("Name cannot be empty")

        return v.strip()


# =====================================================

# UNIT

# =====================================================


class UnitCreate(MasterDataBase):
    pass


class UnitUpdate(BaseModel):

    name: Optional[str] = None

    # unique_code: Optional[str] = None

    category: Optional[str] = None

    is_active: Optional[bool] = None


class UnitOut(BaseModel):

    id: int

    name: str

    unique_code: Optional[str]

    category: Optional[str]

    is_active: bool

    class Config:
        from_attributes = True


# ---------- LABOUR ----------
class LabourTypeCreate(MasterDataBase):
    pass


class LabourTypeOut(MasterDataBase):

    id: int

    unique_code: Optional[str]

    default_daily_wage: Decimal

    default_working_hours: int

    skill_category: e.SkillType

    default_ot_rate_per_hour: Optional[Decimal]

    is_active: bool

    class Config:
        from_attributes = True


# =====================================================

# ACTIVITY TYPE

# =====================================================


class ActivityTypeCreate(MasterDataBase):

    default_unit_id: Optional[int] = None


class ActivityTypeUpdate(BaseModel):

    name: Optional[str] = None

    category: Optional[str] = None

    default_unit_id: Optional[int] = None

    is_active: Optional[bool] = None


class ActivityTypeUpdate(BaseModel):

    name: Optional[str] = None

    # unique_code: Optional[str] = None

    category: Optional[str] = None

    is_active: Optional[bool] = None


class ActivityTypeOut(MasterDataBase):

    id: int

    class Config:
        from_attributes = True


# =====================================================

# MATERIAL MASTER

# =====================================================


class MaterialMasterCreate(MasterDataBase):

    unit: str

    brand: Optional[str] = None

    specification: Optional[str] = None

    hsn_code: Optional[str] = None

    default_rate: Optional[Decimal] = None

    minimum_stock_level: Optional[int] = 0

    is_active: bool = True


class MaterialMasterUpdate(BaseModel):

    name: Optional[str] = None

    unit: Optional[str] = None

    # unique_code: Optional[str] = None

    category: Optional[str] = None

    brand: Optional[str] = None

    specification: Optional[str] = None

    hsn_code: Optional[str] = None

    default_rate: Optional[Decimal] = None

    minimum_stock_level: Optional[int] = None

    is_active: Optional[bool] = None


class MaterialMasterOut(MasterDataBase):

    id: int

    unit: str

    brand: Optional[str]

    unique_code: Optional[str]

    specification: Optional[str]

    hsn_code: Optional[str]

    default_rate: Optional[Decimal]

    minimum_stock_level: Optional[int]

    is_active: bool

    class Config:
        from_attributes = True


# =====================================================

# UNIFIED

# =====================================================


class MasterDataUnified(BaseModel):

    id: int

    name: str

    unique_code: Optional[str]

    category: Optional[str]

    system_tag: str

    unit: Optional[str] = None

    skill_category: Optional[e.SkillType] = None


# =====================================================

# STATS

# =====================================================


class MasterDataStats(BaseModel):

    total_materials: int

    total_labour_types: int

    total_activity_types: int

    total_units: int
