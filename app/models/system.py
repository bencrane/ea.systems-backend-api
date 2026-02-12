from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
import uuid
from pydantic import BaseModel, Field
from app.database import Base


# SQLAlchemy Model (Database)
class SystemDB(Base):
    __tablename__ = "systems"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    category = Column(String(50), nullable=False)
    description = Column(Text, nullable=False)
    modal_url = Column(Text, nullable=True)
    api_key = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="scaffold")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# Pydantic Models (API)
class SystemCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255, pattern="^[a-z0-9-]+$")
    category: str = Field(..., pattern="^(signals|pipeline|content|operations)$")
    description: str = Field(..., min_length=1)


class SystemUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    category: Optional[str] = Field(None, pattern="^(signals|pipeline|content|operations)$")
    description: Optional[str] = Field(None, min_length=1)
    status: Optional[str] = Field(None, pattern="^(scaffold|deployed|active|inactive)$")


class SystemResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    category: str
    description: str
    modal_url: Optional[str]
    api_key: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
