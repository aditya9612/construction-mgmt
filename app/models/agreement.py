from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, func
from app.models.base import Base, TimestampMixin

class Agreement(Base, TimestampMixin):
    __tablename__ = "agreements"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(String(50), unique=True, index=True, nullable=False) # e.g., INF-XXXX
    
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    owner_id = Column(Integer, ForeignKey("owners.id", ondelete="CASCADE"), nullable=False, index=True)
    
    type = Column(String(100), nullable=False) # Land Lease, etc.
    status = Column(String(50), default="Active")
    file_url = Column(Text, nullable=False)
    
    uploaded_at = Column(DateTime, server_default=func.now())
