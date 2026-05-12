from pydantic import BaseModel, field_validator
from typing import Optional, List


# ---------- COMMON ----------
class MasterDataBase(BaseModel):
    name: str
    unique_code: Optional[str] = None
    category: Optional[str] = None

    @field_validator("name")
    def validate_name(cls, v):
        if not v.strip():
            raise ValueError("Name cannot be empty")
        return v


# ---------- UNIT ----------
class UnitCreate(MasterDataBase):
    pass


class UnitOut(MasterDataBase):
    id: int

    class Config:
        from_attributes = True


# ---------- LABOUR ----------
class LabourTypeCreate(MasterDataBase):
    pass


class LabourTypeOut(MasterDataBase):
    id: int

    class Config:
        from_attributes = True


# ---------- ACTIVITY ----------
class ActivityTypeCreate(MasterDataBase):
    pass


class ActivityTypeOut(MasterDataBase):
    id: int

    class Config:
        from_attributes = True


# ---------- MATERIAL ----------
class MaterialMasterCreate(MasterDataBase):
    unit: str


class MaterialMasterOut(MasterDataBase):
    id: int
    unit: str

    class Config:
        from_attributes = True


# ---------- UNIFIED ----------
class MasterDataUnified(BaseModel):
    id: int
    name: str
    unique_code: Optional[str]
    category: Optional[str]
    system_tag: str # MATERIAL, LABOR, ACTIVITY, UNIT
    unit: Optional[str] = None


class MasterDataStats(BaseModel):
    total_materials: int
    total_labour_types: int
    total_activity_types: int
    total_units: int