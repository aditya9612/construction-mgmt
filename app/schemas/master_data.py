from pydantic import BaseModel, field_validator


# ---------- COMMON ----------
class NameBase(BaseModel):
    name: str

    @field_validator("name")
    def validate_name(cls, v):
        if not v.strip():
            raise ValueError("Name cannot be empty")
        return v


# ---------- UNIT ----------
class UnitCreate(NameBase):
    pass


class UnitOut(NameBase):
    id: int

    class Config:
        from_attributes = True


# ---------- LABOUR ----------
class LabourTypeCreate(NameBase):
    pass


class LabourTypeOut(NameBase):
    id: int

    class Config:
        from_attributes = True


# ---------- ACTIVITY ----------
class ActivityTypeCreate(NameBase):
    pass


class ActivityTypeOut(NameBase):
    id: int

    class Config:
        from_attributes = True


# ---------- MATERIAL ----------
class MaterialMasterCreate(BaseModel):
    name: str
    unit: str


class MaterialMasterOut(BaseModel):
    id: int
    name: str
    unit: str

    class Config:
        from_attributes = True