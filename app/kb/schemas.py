from pydantic import BaseModel, ConfigDict, EmailStr, field_validator
from datetime import datetime, date
from typing import Optional, List

class TeamMemberBase(BaseModel):
    id: str  # Slack UserID or Email
    name: str
    role: str
    slack_handle: Optional[str] = None
    outlook_email: Optional[str] = None
    timezone: str = "Asia/Kolkata"

class TeamMemberCreate(TeamMemberBase):
    pass

class TeamMemberResponse(TeamMemberBase):
    model_config = ConfigDict(from_attributes=True)

class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = None
    manager_id: Optional[str] = None
    status: str = "active"

class ProjectCreate(ProjectBase):
    pass

class ProjectResponse(ProjectBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class TaskBase(BaseModel):
    project_id: int
    title: str
    description: Optional[str] = None
    assignee_id: Optional[str] = None
    status: str = "pending"
    blockage_reason: Optional[str] = None
    due_date: Optional[date] = None

class TaskCreate(TaskBase):
    pass

class TaskResponse(TaskBase):
    id: int
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assignee_id: Optional[str] = None
    status: Optional[str] = None
    blockage_reason: Optional[str] = None
    due_date: Optional[date] = None

    @field_validator('status')
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"pending", "in_progress", "completed", "blocked"}
        if v not in allowed:
            raise ValueError(f"Status must be one of {allowed}")
        return v

class UnifiedMessageBase(BaseModel):
    platform_msg_id: str
    source: str
    direction: str = "inbound"
    sender_raw_id: str
    sender_mapped_name: Optional[str] = None
    channel_raw_id: str
    thread_id: Optional[str] = None
    subject: Optional[str] = None
    content: str
    timestamp: datetime
    is_processed: bool = False
    processed_at: Optional[datetime] = None
    raw_metadata: Optional[str] = None

class UnifiedMessageResponse(UnifiedMessageBase):
    id: int

    model_config = ConfigDict(from_attributes=True)
