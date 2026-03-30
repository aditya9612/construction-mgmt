from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base


class Owner(Base):
    __tablename__ = "owners"

    id = Column(Integer, primary_key=True, index=True)

    owner_name = Column(String(100), nullable=False)
    mobile = Column(String(20), nullable=False, unique=True, index=True)
    email = Column(String(100), nullable=True)
    address = Column(String(255), nullable=True)
    pan = Column(String(20), nullable=True)

    # timestamps (important)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

    # relationship
    projects = relationship("Project", back_populates="owner", cascade="all, delete")