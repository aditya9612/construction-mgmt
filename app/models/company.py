from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.orm import relationship
from app.models.base import Base, TimestampMixin

class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    subdomain = Column(String(100), unique=True, nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True)

    users = relationship("User", back_populates="company")
    projects = relationship("Project", back_populates="company")
    settings = relationship("CompanySettings", back_populates="company", uselist=False)
