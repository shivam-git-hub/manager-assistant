from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.projectkb import tracked
from app.controlplane.models import Manager
from app.controlplane.auth import get_current_manager

router = APIRouter(prefix="/api/projectkb/tracked-contacts", tags=["ProjectKB: Tracked Contacts"])


class TrackedContactCreate(BaseModel):
    label: str
    email_pattern: Optional[str] = None
    slack_pattern: Optional[str] = None


class TrackedContactUpdate(BaseModel):
    label: Optional[str] = None
    email_pattern: Optional[str] = None
    slack_pattern: Optional[str] = None
    clear_email_pattern: bool = False
    clear_slack_pattern: bool = False


@router.get("")
def list_tracked_contacts(manager: Manager = Depends(get_current_manager)):
    return tracked.load_tracked_contacts(manager.id)


@router.post("", status_code=201)
def create_tracked_contact(payload: TrackedContactCreate, manager: Manager = Depends(get_current_manager)):
    if not payload.email_pattern and not payload.slack_pattern:
        raise HTTPException(400, "At least one of email_pattern/slack_pattern is required.")
    return tracked.add_tracked_contact(manager.id, payload.label, payload.email_pattern, payload.slack_pattern)


@router.put("/{contact_id}")
def update_tracked_contact(contact_id: str, payload: TrackedContactUpdate, manager: Manager = Depends(get_current_manager)):
    email_pattern = None if payload.clear_email_pattern else (payload.email_pattern if payload.email_pattern is not None else ...)
    slack_pattern = None if payload.clear_slack_pattern else (payload.slack_pattern if payload.slack_pattern is not None else ...)
    updated = tracked.update_tracked_contact(
        manager.id,
        contact_id,
        label=payload.label,
        email_pattern=email_pattern,
        slack_pattern=slack_pattern,
    )
    if not updated:
        raise HTTPException(404, f"No tracked contact with id '{contact_id}'.")
    return updated


@router.delete("/{contact_id}", status_code=204)
def delete_tracked_contact(contact_id: str, manager: Manager = Depends(get_current_manager)):
    if not tracked.delete_tracked_contact(manager.id, contact_id):
        raise HTTPException(404, f"No tracked contact with id '{contact_id}'.")
