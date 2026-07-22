"""Blocklist management API (step 20 -- prompts/step_20_poll_completion.md).
Replaces the old /api/projectkb/tracked-contacts allowlist router: v2
tracks everything and the user lists what NOT to process (spec
architecture_v2_kb.md §4.2). The matching itself lives in
app/projectkb/blocklist.py; the ingest job is the consumer."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.projectkb import blocklist
from app.controlplane.models import Manager
from app.controlplane.auth import get_current_manager

router = APIRouter(prefix="/api/blocklist", tags=["Blocklist"])


class BlockedContactCreate(BaseModel):
    label: str
    email_pattern: Optional[str] = None
    slack_pattern: Optional[str] = None


class BlockedContactUpdate(BaseModel):
    label: Optional[str] = None
    email_pattern: Optional[str] = None
    slack_pattern: Optional[str] = None
    clear_email_pattern: bool = False
    clear_slack_pattern: bool = False


class BlockedChannelCreate(BaseModel):
    label: str
    source: str  # "slack" | "outlook"
    pattern: str


@router.get("")
def get_blocklist(manager: Manager = Depends(get_current_manager)):
    return blocklist.load_blocklist(manager.id)


@router.post("/contacts", status_code=201)
def create_blocked_contact(payload: BlockedContactCreate, manager: Manager = Depends(get_current_manager)):
    if not payload.email_pattern and not payload.slack_pattern:
        raise HTTPException(400, "At least one of email_pattern/slack_pattern is required.")
    return blocklist.add_blocked_contact(manager.id, payload.label, payload.email_pattern, payload.slack_pattern)


@router.put("/contacts/{contact_id}")
def update_blocked_contact(contact_id: str, payload: BlockedContactUpdate, manager: Manager = Depends(get_current_manager)):
    email_pattern = None if payload.clear_email_pattern else (payload.email_pattern if payload.email_pattern is not None else ...)
    slack_pattern = None if payload.clear_slack_pattern else (payload.slack_pattern if payload.slack_pattern is not None else ...)
    updated = blocklist.update_blocked_contact(
        manager.id, contact_id, label=payload.label,
        email_pattern=email_pattern, slack_pattern=slack_pattern,
    )
    if not updated:
        raise HTTPException(404, f"No blocked contact with id '{contact_id}'.")
    return updated


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_blocked_contact(contact_id: str, manager: Manager = Depends(get_current_manager)):
    if not blocklist.delete_blocked_contact(manager.id, contact_id):
        raise HTTPException(404, f"No blocked contact with id '{contact_id}'.")


@router.post("/channels", status_code=201)
def create_blocked_channel(payload: BlockedChannelCreate, manager: Manager = Depends(get_current_manager)):
    if payload.source not in ("slack", "outlook"):
        raise HTTPException(400, "source must be 'slack' or 'outlook'.")
    return blocklist.add_blocked_channel(manager.id, payload.label, payload.source, payload.pattern)


@router.delete("/channels/{channel_id}", status_code=204)
def delete_blocked_channel(channel_id: str, manager: Manager = Depends(get_current_manager)):
    if not blocklist.delete_blocked_channel(manager.id, channel_id):
        raise HTTPException(404, f"No blocked channel with id '{channel_id}'.")
