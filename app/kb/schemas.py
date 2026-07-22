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
    receiver_raw_id: Optional[str] = None
    receiver_mapped_name: Optional[str] = None
    channel_raw_id: str
    thread_id: Optional[str] = None
    subject: Optional[str] = None
    content: str
    timestamp: datetime
    created_at: Optional[datetime] = None
    is_processed: bool = False
    processed_at: Optional[datetime] = None
    raw_metadata: Optional[str] = None

class UnifiedMessageResponse(UnifiedMessageBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class EntityCreate(BaseModel):
    slug: str
    type: str
    name: str
    ref_id: Optional[str] = None


class EntityResponse(BaseModel):
    id: int
    slug: str
    type: str
    name: str
    ref_id: Optional[str] = None
    truth_updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TimelineEntryCreate(BaseModel):
    happened_at: Optional[datetime] = None
    summary: str
    detail: Optional[str] = None
    source_message_id: Optional[int] = None


class TimelineEntryResponse(BaseModel):
    id: int
    entity_id: int
    happened_at: datetime
    summary: str
    detail: Optional[str] = None
    source_message_id: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AttributedClaimCreate(BaseModel):
    claim: str
    kind: str
    holder: str
    weight: float
    source_message_id: Optional[int] = None
    claimed_at: Optional[datetime] = None


class AttributedClaimResponse(BaseModel):
    id: int
    entity_id: int
    claim: str
    kind: str
    holder: str
    weight: float
    source_message_id: Optional[int] = None
    claimed_at: datetime
    superseded_by: Optional[int] = None
    active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConflictCreate(BaseModel):
    entity_slug: str
    claim_a_id: int
    claim_b_id: int
    severity: str
    description: str


class ConflictResponse(BaseModel):
    id: int
    entity_id: int
    claim_a_id: int
    claim_b_id: int
    severity: str
    description: str
    status: str
    detected_at: datetime
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ConflictUpdate(BaseModel):
    status: str
    resolution_note: Optional[str] = None


class EntityPageEntityResponse(BaseModel):
    id: int
    slug: str
    type: str
    name: str
    ref_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EntityPageResponse(BaseModel):
    entity: EntityPageEntityResponse
    compiled_truth: Optional[str] = None
    truth_updated_at: Optional[datetime] = None
    timeline: List[TimelineEntryResponse]
    claims: List[AttributedClaimResponse]
    conflicts: List[ConflictResponse]


class SearchEntityHit(BaseModel):
    slug: str
    type: str
    name: str
    compiled_truth: Optional[str] = None


class SearchClaimHit(BaseModel):
    id: int
    entity_slug: str
    claim: str
    holder: str


class SearchTimelineHit(BaseModel):
    id: int
    entity_slug: str
    summary: str
    detail: Optional[str] = None


class SearchGroupedResponse(BaseModel):
    entities: List[SearchEntityHit]
    claims: List[SearchClaimHit]
    timeline: List[SearchTimelineHit]
