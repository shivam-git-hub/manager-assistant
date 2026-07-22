from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, and_
from sqlalchemy.exc import IntegrityError

from app.database import UnifiedMessage
from app.tenancy.db import get_manager_db
from app import timeservice
from app.kb.models import Entity, TimelineEntry, AttributedClaim, Conflict
from app.kb.schemas import (
    EntityCreate, EntityResponse, EntityPageResponse,
    TimelineEntryCreate, TimelineEntryResponse,
    AttributedClaimCreate, AttributedClaimResponse,
    ConflictCreate, ConflictResponse, ConflictUpdate,
    SearchGroupedResponse, SearchEntityHit, SearchClaimHit, SearchTimelineHit
)

router = APIRouter(prefix="/api/kb", tags=["Knowledge Base"])

# 1. GET /api/kb/entities?type=project
@router.get("/entities", response_model=List[EntityResponse])
def list_entities(type: Optional[str] = Query(None), db: Session = Depends(get_manager_db)):
    stmt = select(Entity)
    if type:
        stmt = stmt.where(Entity.type == type)
    return list(db.scalars(stmt).all())

# 2. POST /api/kb/entities
@router.post("/entities", response_model=EntityResponse, status_code=status.HTTP_201_CREATED)
def create_entity(payload: EntityCreate, db: Session = Depends(get_manager_db)):
    # Validate type
    allowed_types = {"project", "person", "meeting"}
    if payload.type not in allowed_types:
        raise HTTPException(
            status_code=422,
            detail=f"Type must be one of {allowed_types}"
        )
        
    # Check duplicate slug
    existing = db.scalars(select(Entity).where(Entity.slug == payload.slug)).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Entity with slug '{payload.slug}' already exists."
        )
        
    new_entity = Entity(
        slug=payload.slug,
        type=payload.type,
        name=payload.name,
        ref_id=payload.ref_id
    )
    db.add(new_entity)
    try:
        db.commit()
        db.refresh(new_entity)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Integrity error creating entity")
        
    return new_entity

# 3. GET /api/kb/entities/{slug}
@router.get("/entities/{slug}", response_model=EntityPageResponse)
def get_entity_page(slug: str, db: Session = Depends(get_manager_db)):
    entity = db.scalars(select(Entity).where(Entity.slug == slug)).first()
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{slug}' not found")
        
    timeline = db.scalars(
        select(TimelineEntry)
        .where(TimelineEntry.entity_id == entity.id)
        .order_by(TimelineEntry.happened_at.desc(), TimelineEntry.id.desc())
    ).all()

    claims = db.scalars(
        select(AttributedClaim)
        .where((AttributedClaim.entity_id == entity.id) & (AttributedClaim.active == True))
    ).all()
    
    conflicts = db.scalars(
        select(Conflict)
        .where((Conflict.entity_id == entity.id) & (Conflict.status == "open"))
    ).all()
    
    return {
        "entity": entity,
        "compiled_truth": entity.compiled_truth,
        "truth_updated_at": entity.truth_updated_at,
        "timeline": list(timeline),
        "claims": list(claims),
        "conflicts": list(conflicts)
    }

# 4. POST /api/kb/entities/{slug}/timeline
@router.post("/entities/{slug}/timeline", response_model=TimelineEntryResponse, status_code=status.HTTP_201_CREATED)
def append_timeline_entry(slug: str, payload: TimelineEntryCreate, db: Session = Depends(get_manager_db)):
    entity = db.scalars(select(Entity).where(Entity.slug == slug)).first()
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{slug}' not found")
        
    if payload.source_message_id is not None:
        msg = db.get(UnifiedMessage, payload.source_message_id)
        if not msg:
            raise HTTPException(
                status_code=404,
                detail=f"Source message ID {payload.source_message_id} not found"
            )
            
    happened_at = payload.happened_at or timeservice.now_ist()
    
    entry = TimelineEntry(
        entity_id=entity.id,
        happened_at=happened_at,
        summary=payload.summary,
        detail=payload.detail,
        source_message_id=payload.source_message_id
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry

# 5. GET /api/kb/entities/{slug}/timeline
@router.get("/entities/{slug}/timeline", response_model=List[TimelineEntryResponse])
def get_timeline(slug: str, limit: int = Query(50), db: Session = Depends(get_manager_db)):
    entity = db.scalars(select(Entity).where(Entity.slug == slug)).first()
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{slug}' not found")
        
    timeline = db.scalars(
        select(TimelineEntry)
        .where(TimelineEntry.entity_id == entity.id)
        .order_by(TimelineEntry.happened_at.desc(), TimelineEntry.id.desc())
        .limit(limit)
    ).all()
    return list(timeline)

# 6. POST /api/kb/entities/{slug}/claims
@router.post("/entities/{slug}/claims", response_model=AttributedClaimResponse, status_code=status.HTTP_201_CREATED)
def create_claim(slug: str, payload: AttributedClaimCreate, db: Session = Depends(get_manager_db)):
    entity = db.scalars(select(Entity).where(Entity.slug == slug)).first()
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{slug}' not found")
        
    # Validate kind
    allowed_kinds = {"fact", "status", "commitment", "blocker", "opinion"}
    if payload.kind not in allowed_kinds:
        raise HTTPException(status_code=422, detail=f"Kind must be one of {allowed_kinds}")
        
    # Validate weight
    if not (0.0 <= payload.weight <= 1.0):
        raise HTTPException(status_code=422, detail="Weight must be between 0.0 and 1.0")
        
    if payload.source_message_id is not None:
        msg = db.get(UnifiedMessage, payload.source_message_id)
        if not msg:
            raise HTTPException(status_code=404, detail="Source message not found")
            
    claimed_at = payload.claimed_at or timeservice.now_ist()
    
    new_claim = AttributedClaim(
        entity_id=entity.id,
        claim=payload.claim,
        kind=payload.kind,
        holder=payload.holder,
        weight=payload.weight,
        source_message_id=payload.source_message_id,
        claimed_at=claimed_at,
        active=True
    )
    db.add(new_claim)
    db.commit()
    db.refresh(new_claim)
    return new_claim

# 7. GET /api/kb/entities/{slug}/claims
@router.get("/entities/{slug}/claims", response_model=List[AttributedClaimResponse])
def get_claims(
    slug: str,
    active: Optional[bool] = Query(None),
    holder: Optional[str] = Query(None),
    db: Session = Depends(get_manager_db)
):
    entity = db.scalars(select(Entity).where(Entity.slug == slug)).first()
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{slug}' not found")
        
    stmt = select(AttributedClaim).where(AttributedClaim.entity_id == entity.id)
    if active is not None:
        stmt = stmt.where(AttributedClaim.active == active)
    if holder is not None:
        stmt = stmt.where(AttributedClaim.holder == holder)
        
    return list(db.scalars(stmt).all())

# 8. POST /api/kb/claims/{id}/supersede
@router.post("/claims/{id}/supersede", response_model=List[AttributedClaimResponse])
def supersede_claim(id: int, payload: AttributedClaimCreate, db: Session = Depends(get_manager_db)):
    old_claim = db.get(AttributedClaim, id)
    if not old_claim:
        raise HTTPException(status_code=404, detail="Claim to supersede not found")
        
    if not old_claim.active or old_claim.superseded_by is not None:
        raise HTTPException(status_code=409, detail="Claim is already inactive or superseded")
        
    # Validate kind
    allowed_kinds = {"fact", "status", "commitment", "blocker", "opinion"}
    if payload.kind not in allowed_kinds:
        raise HTTPException(status_code=422, detail=f"Kind must be one of {allowed_kinds}")
        
    # Validate weight
    if not (0.0 <= payload.weight <= 1.0):
        raise HTTPException(status_code=422, detail="Weight must be between 0.0 and 1.0")
        
    claimed_at = payload.claimed_at or timeservice.now_ist()
    
    # Create B
    new_claim = AttributedClaim(
        entity_id=old_claim.entity_id,
        claim=payload.claim,
        kind=payload.kind,
        holder=payload.holder,
        weight=payload.weight,
        source_message_id=payload.source_message_id,
        claimed_at=claimed_at,
        active=True
    )
    db.add(new_claim)
    db.commit() # commit so we get B's id
    
    # Update A
    old_claim.active = False
    old_claim.superseded_by = new_claim.id
    db.commit()
    
    db.refresh(old_claim)
    db.refresh(new_claim)
    return [old_claim, new_claim]

# 9. POST /api/kb/conflicts
@router.post("/conflicts", response_model=ConflictResponse, status_code=status.HTTP_201_CREATED)
def create_conflict(payload: ConflictCreate, db: Session = Depends(get_manager_db)):
    entity = db.scalars(select(Entity).where(Entity.slug == payload.entity_slug)).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity slug not found")
        
    claim_a = db.get(AttributedClaim, payload.claim_a_id)
    claim_b = db.get(AttributedClaim, payload.claim_b_id)
    
    if not claim_a or not claim_b:
        raise HTTPException(status_code=422, detail="Both claims must exist")
        
    if claim_a.entity_id != entity.id or claim_b.entity_id != entity.id:
        raise HTTPException(status_code=422, detail="Both claims must belong to the specified entity")
        
    new_conflict = Conflict(
        entity_id=entity.id,
        claim_a_id=claim_a.id,
        claim_b_id=claim_b.id,
        severity=payload.severity,
        description=payload.description,
        status="open",
        detected_at=timeservice.now_ist()
    )
    db.add(new_conflict)
    db.commit()
    db.refresh(new_conflict)
    return new_conflict

# 10. GET /api/kb/conflicts
@router.get("/conflicts", response_model=List[ConflictResponse])
def list_conflicts(
    status: Optional[str] = Query(None),
    entity: Optional[str] = Query(None),
    db: Session = Depends(get_manager_db)
):
    stmt = select(Conflict)
    if status:
        stmt = stmt.where(Conflict.status == status)
    if entity:
        ent = db.scalars(select(Entity).where(Entity.slug == entity)).first()
        if not ent:
            return []
        stmt = stmt.where(Conflict.entity_id == ent.id)
    return list(db.scalars(stmt).all())

# 11. PATCH /api/kb/conflicts/{id}
@router.patch("/conflicts/{id}", response_model=ConflictResponse)
def patch_conflict(id: int, payload: ConflictUpdate, db: Session = Depends(get_manager_db)):
    conflict = db.get(Conflict, id)
    if not conflict:
        raise HTTPException(status_code=404, detail="Conflict not found")
        
    allowed_statuses = {"resolved", "dismissed"}
    if payload.status not in allowed_statuses:
        raise HTTPException(status_code=422, detail=f"Status must be one of {allowed_statuses}")
        
    conflict.status = payload.status
    conflict.resolution_note = payload.resolution_note
    conflict.resolved_at = timeservice.now_ist()
    db.commit()
    db.refresh(conflict)
    return conflict

# 12. GET /api/kb/search?q=schema
@router.get("/search", response_model=SearchGroupedResponse)
def search_kb(q: str = Query(...), db: Session = Depends(get_manager_db)):
    term = f"%{q}%"
    
    # 1. Search entities: name or compiled_truth
    stmt_ent = (
        select(Entity)
        .where(or_(Entity.name.ilike(term), Entity.compiled_truth.ilike(term)))
        .limit(20)
    )
    entities = db.scalars(stmt_ent).all()
    
    # 2. Search claims: claim text
    stmt_claims = (
        select(AttributedClaim, Entity.slug)
        .join(Entity, Entity.id == AttributedClaim.entity_id)
        .where(AttributedClaim.claim.ilike(term))
        .limit(20)
    )
    claims_res = db.execute(stmt_claims).all()
    
    # 3. Search timeline: summary or detail
    stmt_time = (
        select(TimelineEntry, Entity.slug)
        .join(Entity, Entity.id == TimelineEntry.entity_id)
        .where(or_(TimelineEntry.summary.ilike(term), TimelineEntry.detail.ilike(term)))
        .limit(20)
    )
    timeline_res = db.execute(stmt_time).all()
    
    # Format hits
    entity_hits = [
        SearchEntityHit(slug=e.slug, type=e.type, name=e.name, compiled_truth=e.compiled_truth)
        for e in entities
    ]
    
    claim_hits = [
        SearchClaimHit(id=c.AttributedClaim.id, entity_slug=c.slug, claim=c.AttributedClaim.claim, holder=c.AttributedClaim.holder)
        for c in claims_res
    ]
    
    timeline_hits = [
        SearchTimelineHit(id=t.TimelineEntry.id, entity_slug=t.slug, summary=t.TimelineEntry.summary, detail=t.TimelineEntry.detail)
        for t in timeline_res
    ]
    
    return SearchGroupedResponse(
        entities=entity_hits,
        claims=claim_hits,
        timeline=timeline_hits
    )


@router.post("/process")
def process_unprocessed_messages(limit: int = Query(20), db: Session = Depends(get_manager_db)):
    from app.kb.extraction import extract_from_message
    stmt = select(UnifiedMessage).where(UnifiedMessage.is_processed == False).order_by(UnifiedMessage.timestamp.asc()).limit(limit)
    msgs = list(db.scalars(stmt).all())
    
    processed = 0
    claims_created = 0
    timeline_created = 0
    skipped = 0
    
    for msg in msgs:
        res = extract_from_message(db, msg)
        processed += 1
        if res.get("skipped"):
            skipped += 1
        else:
            claims_created += res.get("claims_created", 0)
            timeline_created += res.get("timeline_entries_created", 0)
            
    return {
        "processed": processed,
        "claims_created": claims_created,
        "timeline_entries_created": timeline_created,
        "skipped": skipped
    }


@router.post("/dream")
def execute_dream_cycle(db: Session = Depends(get_manager_db)):
    from app.kb.synthesis import run_dream_cycle
    stats = run_dream_cycle(db)
    return stats
