from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, func
from app.models.base import Base, TimestampMixin

class ProjectVisualization(Base, TimestampMixin):
    __tablename__ = "project_visualizations"

    id = Column(Integer, primary_key=True, index=True)
    visualization_id = Column(String(50), unique=True, index=True, nullable=False) # e.g., VIZ-XXXX
    
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    
    title = Column(String(255), nullable=False)
    points = Column(Integer, default=0)
    image_url = Column(Text, nullable=False)
    
    created_at = Column(DateTime, server_default=func.now())
