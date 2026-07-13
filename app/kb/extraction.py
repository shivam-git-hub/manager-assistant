import json
import logging
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import UnifiedMessage, TeamMember
from app.kb.models import Entity, AttributedClaim, TimelineEntry
from app import timeservice
from app.agent.gemini_client import get_client, GeminiClient
from app.config import FLASH_MODEL

logger = logging.getLogger(__name__)

def extract_from_message(db: Session, message: UnifiedMessage, client: Optional[GeminiClient] = None) -> dict:
    real_client: GeminiClient = client if client is not None else get_client()
        
    try:
        # 1. Skip check
        if message.direction == "outbound" or not message.content or len(message.content.strip()) < 10:
            return {"claims_created": 0, "timeline_entries_created": 0, "skipped": True}
            
        # 2. Assemble context
        entities = list(db.scalars(select(Entity)).all())
        team_members = list(db.scalars(select(TeamMember)).all())
        
        entities_context = "\n".join([f"- Slug: {e.slug} | Name: {e.name} | Type: {e.type}" for e in entities])
        team_context = "\n".join([f"- ID: {m.id} | Name: {m.name} | Role: {m.role}" for m in team_members])
        
        msg_context = (
            f"Sender Raw ID: {message.sender_raw_id}\n"
            f"Sender Name: {message.sender_mapped_name or 'Unknown'}\n"
            f"Channel: {message.channel_raw_id}\n"
            f"Timestamp: {message.timestamp.isoformat()}\n"
            f"Subject: {message.subject or 'None'}\n"
            f"Content:\n{message.content}"
        )
        
        # 3. Prompt
        system_instruction = (
            "You are a highly precise information extraction system. Your task is to extract atomic, verifiable project management claims and timeline events from an inbound communication message.\n\n"
            "Return a strictly formatted JSON object with two fields:\n"
            "1. 'claims': a list of objects, each containing:\n"
            "   - 'entity_slug': the exact string from the Candidate Entities slug list (or null if none matches).\n"
            "   - 'claim': a brief, atomic, verifiable statement (unbiased factual statement, commitment, blocker, etc.).\n"
            "   - 'kind': must be exactly one of: 'fact', 'status', 'commitment', 'blocker', 'opinion'.\n"
            "   - 'holder': the team_members.id of who asserts this claim (defaults to the sender's ID, unless they are quoting someone else).\n"
            "   - 'weight': confidence score between 0.0 and 1.0 (1.0 for first-person direct statements, lower for reports or hearsay).\n"
            "2. 'timeline_summary': a one-line factual summary of the event (or null if no relevant project/work event occurred).\n\n"
            "Rules:\n"
            "- Only extract claims that have business, task, status, or project relevance.\n"
            "- The entity_slug must match the Candidate Entities list exactly or be null. Do not invent new slugs.\n"
            "- If the message has no project or work relevance, return {\"claims\": [], \"timeline_summary\": null}.\n"
            "- Ensure kind is one of: 'fact', 'status', 'commitment', 'blocker', 'opinion'."
        )
        
        prompt = (
            f"### Message to Process:\n{msg_context}\n\n"
            f"### Candidate Entities:\n{entities_context}\n\n"
            f"### Team Roster:\n{team_context}\n\n"
            "Extract the claims and timeline summary in strict JSON format."
        )
        
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ]
        
        # Call client
        res = real_client.chat(model=FLASH_MODEL, messages=messages, json_mode=True)
        content_str = res.get("content") or "{}"
        
        # Parse JSON
        try:
            extracted = json.loads(content_str)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse LLM response as JSON. Content: {content_str}")
            extracted = {"claims": [], "timeline_summary": None}
            
        claims_list = extracted.get("claims", [])
        timeline_summary = extracted.get("timeline_summary")
        
        # 4. Validate in code
        valid_claims = []
        allowed_kinds = {"fact", "status", "commitment", "blocker", "opinion"}
        known_slugs = {e.slug for e in entities}
        known_holders = {m.id for m in team_members}
        
        for c in claims_list:
            slug = c.get("entity_slug")
            kind = c.get("kind")
            claim_text = c.get("claim")
            weight = c.get("weight")
            holder = c.get("holder")
            
            # Entity validation
            if not slug or slug not in known_slugs:
                continue
            # Kind validation
            if not kind or kind not in allowed_kinds:
                continue
            # Text validation
            if not claim_text or not claim_text.strip():
                continue
                
            # Weight validation
            try:
                weight_val = float(weight) if weight is not None else 1.0
                weight_val = max(0.0, min(1.0, weight_val))
            except ValueError:
                weight_val = 1.0
                
            # Holder validation
            if not holder or holder not in known_holders:
                # Resolve sender ID fallback
                holder_val = message.sender_raw_id
                matched_member = db.scalars(select(TeamMember).where(
                    (TeamMember.id == message.sender_raw_id) |
                    (TeamMember.slack_handle == message.sender_raw_id) |
                    (TeamMember.outlook_email == message.sender_raw_id)
                )).first()
                if matched_member:
                    holder_val = matched_member.id
                else:
                    if holder_val not in known_holders:
                        holder_val = "U_HARRY"
            else:
                holder_val = holder
                
            valid_claims.append({
                "entity_slug": slug,
                "claim": claim_text.strip(),
                "kind": kind,
                "holder": holder_val,
                "weight": weight_val
            })
            
        # 5. Persist Claims
        claims_created = []
        for c in valid_claims:
            ent = next(e for e in entities if e.slug == c["entity_slug"])
            db_claim = AttributedClaim(
                entity_id=ent.id,
                claim=c["claim"],
                kind=c["kind"],
                holder=c["holder"],
                weight=c["weight"],
                source_message_id=message.id,
                claimed_at=message.timestamp,
                active=True
            )
            db.add(db_claim)
            claims_created.append(db_claim)
            
        db.commit()
        
        # 6. Persist Timeline Entries
        timeline_created_count = 0
        if timeline_summary and len(claims_created) > 0:
            involved_entity_ids = {cl.entity_id for cl in claims_created}
            for ent_id in involved_entity_ids:
                timeline_entry = TimelineEntry(
                    entity_id=ent_id,
                    happened_at=message.timestamp,
                    summary=timeline_summary.strip(),
                    detail=message.content,
                    source_message_id=message.id
                )
                db.add(timeline_entry)
                timeline_created_count += 1
            db.commit()
            
        return {
            "claims_created": len(claims_created),
            "timeline_entries_created": timeline_created_count,
            "skipped": False
        }
    except Exception as e:
        logger.exception(f"Error during claim extraction of message {message.id}: {e}")
        db.rollback()
        return {"claims_created": 0, "timeline_entries_created": 0, "skipped": False}
    finally:
        message.is_processed = True
        message.processed_at = timeservice.now_ist()
        db.commit()
