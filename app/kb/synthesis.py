import json
import logging
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, and_

from app.database import UnifiedMessage, TeamMember
from app.kb.models import Entity, TimelineEntry, AttributedClaim, Conflict
from app import timeservice
from app.agent.gemini_client import get_client, GeminiClient
from app.config import SMART_MODEL, FLASH_MODEL
from app.kb.extraction import extract_from_message

logger = logging.getLogger(__name__)

def run_supersession_pass(db: Session, entity: Entity, client: GeminiClient) -> int:
    """
    Identifies active claims for the same holder and kind, and uses the Flash judge
    to determine if newer claims supersede older claims.
    """
    # Fetch all active claims for this entity sorted oldest first
    claims = list(db.scalars(
        select(AttributedClaim)
        .where((AttributedClaim.entity_id == entity.id) & (AttributedClaim.active == True))
        .order_by(AttributedClaim.claimed_at.asc())
    ).all())
    
    # Group claims by (holder, kind)
    groups = {}
    for c in claims:
        key = (c.holder, c.kind)
        if key not in groups:
            groups[key] = []
        groups[key].append(c)
        
    # Gather candidate pairs (older, newer)
    pairs = []
    for key, group_claims in groups.items():
        for i in range(len(group_claims)):
            for j in range(i + 1, len(group_claims)):
                pairs.append((group_claims[i], group_claims[j]))
                if len(pairs) >= 10:
                    break
            if len(pairs) >= 10:
                break
                
    if not pairs:
        return 0
        
    # Format payload for Flash judge
    judge_pairs = []
    for idx, (older, newer) in enumerate(pairs):
        judge_pairs.append({
            "pair_index": idx,
            "older_claim": older.claim,
            "newer_claim": newer.claim
        })
        
    system_instruction = (
        "You are a precise logical judge. Your task is to evaluate pairs of status/progress claims made by the same team member. "
        "For each pair, determine if the newer claim updates, replaces, or supersedes the older claim. "
        "This is true ONLY if both claims describe the same task, project aspect, or topic, and the newer statement renders the older one obsolete. "
        "For example, 'Working on schema' is superseded by 'Finished schema'. "
        "However, 'Working on CSS' does NOT supersede 'Working on database'. "
        "Return a JSON object in this format:\n"
        "{\n"
        "  \"results\": [\n"
        "    { \"pair_index\": 0, \"supersedes\": true }\n"
        "  ]\n"
        "}"
    )
    
    prompt = f"### Candidate Pairs:\n{json.dumps({'pairs': judge_pairs}, indent=2)}\n\nJudge each pair and return JSON results."
    
    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": prompt}
    ]
    
    try:
        res = client.chat(model=FLASH_MODEL, messages=messages, json_mode=True)
        content_str = res.get("content") or "{}"
        result_data = json.loads(content_str)
        results = result_data.get("results", [])
    except Exception as e:
        logger.error(f"Error executing supersession pass LLM call: {e}")
        return 0
        
    superseded_count = 0
    results_map = {item["pair_index"]: item.get("supersedes", False) for item in results if "pair_index" in item}
    
    for idx, (older, newer) in enumerate(pairs):
        if results_map.get(idx, False):
            # Verify older hasn't already been superseded by something else in this transaction
            if older.active:
                older.active = False
                older.superseded_by = newer.id
                superseded_count += 1
                
    if superseded_count > 0:
        db.commit()
        
    return superseded_count


def synthesize_entity(db: Session, entity: Entity, client: GeminiClient) -> Optional[str]:
    """
    Compiles active claims and timeline logs into a cited compiled truth prose.
    """
    # 1. Dirty check
    newest_timeline = db.scalars(
        select(TimelineEntry.happened_at)
        .where(TimelineEntry.entity_id == entity.id)
        .order_by(TimelineEntry.happened_at.desc())
    ).first()
    
    newest_claim = db.scalars(
        select(AttributedClaim.claimed_at)
        .where(AttributedClaim.entity_id == entity.id)
        .order_by(AttributedClaim.claimed_at.desc())
    ).first()
    
    newest_activity = None
    if newest_timeline and newest_claim:
        newest_activity = max(newest_timeline, newest_claim)
    elif newest_timeline:
        newest_activity = newest_timeline
    elif newest_claim:
        newest_activity = newest_claim
        
    if newest_activity and entity.compiled_truth and entity.truth_updated_at:
        if newest_activity <= entity.truth_updated_at:
            return entity.compiled_truth
            
    # Assemble input context
    timeline_entries = list(db.scalars(
        select(TimelineEntry)
        .where(TimelineEntry.entity_id == entity.id)
        .order_by(TimelineEntry.happened_at.desc())
        .limit(40)
    ).all())
    
    active_claims = list(db.scalars(
        select(AttributedClaim)
        .where((AttributedClaim.entity_id == entity.id) & (AttributedClaim.active == True))
    ).all())
    
    # Format lists for LLM
    timeline_context = "\n".join([
        f"- ID: [T{t.id}] | Time: {t.happened_at.isoformat()} | Summary: {t.summary}"
        for t in timeline_entries
    ])
    claims_context = "\n".join([
        f"- Holder: {c.holder} | Kind: {c.kind} | Claim: {c.claim} | Confidence: {c.weight}"
        for c in active_claims
    ])
    
    system_instruction = (
        "You are an expert project manager synthesis agent. "
        "Your task is to write a highly concise compiled truth summary (1-3 short paragraphs) in the present tense describing the current status of the project, person, or meeting. "
        "Rules:\n"
        "1. Write clear, business-grade prose.\n"
        "2. EVERY factual statement you make must end with one or more inline timeline citations, such as [T1] or [T4, T5], where the ID corresponds to a timeline entry ID in the input list.\n"
        "3. Explicitly state uncertainties or lower confidence items (e.g., if a claim has weight < 0.7, qualify it with 'reportedly' or 'allegedly').\n"
        "4. DO NOT invent any facts or details that are not directly supported by the timeline entries or active claims provided in the context.\n"
        "5. Respond with ONLY the summary text. No JSON wrapping, no introductions."
    )
    
    prompt = (
        f"### Entity Name: {entity.name} (Type: {entity.type})\n"
        f"### Current Time: {timeservice.now_ist().isoformat()} IST\n\n"
        f"### Recent Timeline Events (Cite these IDs):\n{timeline_context or 'None'}\n\n"
        f"### Active Supporting Claims:\n{claims_context or 'None'}\n\n"
        f"### Prior Summary (for continuity):\n{entity.compiled_truth or 'None'}\n\n"
        "Generate the factual present-tense synthesis summary incorporating inline [T<id>] citations."
    )
    
    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": prompt}
    ]
    
    try:
        res = client.chat(model=SMART_MODEL, messages=messages)
        synthesis_text = res.get("content")
    except Exception as e:
        logger.error(f"Error during smart model synthesis call: {e}")
        return entity.compiled_truth
        
    if not synthesis_text:
        return entity.compiled_truth
        
    # Validate citations in code
    known_t_ids = {f"T{t.id}" for t in timeline_entries}
    
    # Regex to find citations like [T12] or [T1, T2]
    # We can match all parts inside brackets and validate them individually
    def replace_citation(match):
        inner = match.group(1) # e.g. "T1, T2"
        parts = [p.strip() for p in inner.split(",")]
        valid_parts = [p for p in parts if p in known_t_ids]
        if valid_parts:
            return f"[{', '.join(valid_parts)}]"
        return "" # Strip if none of the IDs exist in this entity's timeline
        
    # Replace all [T...] patterns
    cleaned_text = re.sub(r'\[(T\d+(?:\s*,\s*T\d+)*)\]', replace_citation, synthesis_text)
    
    # Clean up empty spaces around stripped citations
    cleaned_text = re.sub(r'\s+([.,;:?])', r'\1', cleaned_text)
    cleaned_text = re.sub(r' +', ' ', cleaned_text)
    
    entity.compiled_truth = cleaned_text.strip()
    entity.truth_updated_at = timeservice.now_ist()
    db.commit()
    
    return entity.compiled_truth


def run_contradiction_probe(db: Session, entity: Entity, client: GeminiClient) -> List[Conflict]:
    """
    Examines pairs of active claims from different holders for logical contradictions,
    flagging them as Conflicts in the DB.
    """
    # Fetch all active claims for this entity of specific kinds
    claims = list(db.scalars(
        select(AttributedClaim)
        .where(
            (AttributedClaim.entity_id == entity.id) &
            (AttributedClaim.active == True) &
            (AttributedClaim.kind.in_({"fact", "status", "commitment", "blocker"}))
        )
    ).all())
    
    # Find candidate pairs across different holders with no existing conflict log
    pairs = []
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            c1 = claims[i]
            c2 = claims[j]
            if c1.holder != c2.holder:
                # Check existing conflict logs in any status (open, resolved, dismissed)
                stmt_conf = select(Conflict).where(
                    or_(
                        and_(Conflict.claim_a_id == c1.id, Conflict.claim_b_id == c2.id),
                        and_(Conflict.claim_a_id == c2.id, Conflict.claim_b_id == c1.id)
                    )
                )
                existing = db.scalars(stmt_conf).first()
                if not existing:
                    pairs.append((c1, c2))
                    if len(pairs) >= 15:
                        break
        if len(pairs) >= 15:
            break
            
    if not pairs:
        return []
        
    judge_pairs = []
    for idx, (c1, c2) in enumerate(pairs):
        judge_pairs.append({
            "pair_index": idx,
            "claim_a": f"[{c1.holder}]: {c1.claim}",
            "claim_b": f"[{c2.holder}]: {c2.claim}"
        })
        
    system_instruction = (
        "You are a precise contradiction detector. Your task is to evaluate pairs of claims made by different team members about a project aspect. "
        "Determine if there is a logical, factual, or status contradiction, conflict, or deadlock between the statements. "
        "A contradiction is true only if both cannot be simultaneously true. "
        "Classify conflict severity as low, medium, or high:\n"
        "- low: Minor discrepancy in wording, progress percentages, or details.\n"
        "- medium: Timeline discrepancies, or stale status versus newer status disagreements.\n"
        "- high: Direct logical deadlocks, or critical blockers (e.g. 'I delivered the schema' vs 'I did not receive the schema').\n\n"
        "Return a JSON object in this format:\n"
        "{\n"
        "  \"results\": [\n"
        "    {\n"
        "      \"pair_index\": 0,\n"
        "      \"contradicts\": true,\n"
        "      \"severity\": \"high\",\n"
        "      \"description\": \"...\"\n"
        "    }\n"
        "  ]\n"
        "}"
    )
    
    prompt = f"### Candidate Claims Pairs:\n{json.dumps({'pairs': judge_pairs}, indent=2)}\n\nEvaluate each pair for contradictions and return JSON."
    
    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": prompt}
    ]
    
    try:
        res = client.chat(model=FLASH_MODEL, messages=messages, json_mode=True)
        content_str = res.get("content") or "{}"
        result_data = json.loads(content_str)
        results = result_data.get("results", [])
    except Exception as e:
        logger.error(f"Error during contradiction probe LLM call: {e}")
        return []
        
    new_conflicts = []
    results_map = {item["pair_index"]: item for item in results if "pair_index" in item}
    
    for idx, (c1, c2) in enumerate(pairs):
        res_item = results_map.get(idx)
        if res_item and res_item.get("contradicts", False):
            sev = res_item.get("severity", "medium").lower()
            if sev not in ("low", "medium", "high"):
                sev = "medium"
                
            conflict = Conflict(
                entity_id=entity.id,
                claim_a_id=c1.id,
                claim_b_id=c2.id,
                severity=sev,
                description=res_item.get("description", "Logical contradiction detected between claims."),
                status="open",
                detected_at=timeservice.now_ist()
            )
            db.add(conflict)
            new_conflicts.append(conflict)
            
    if new_conflicts:
        db.commit()
        # Refresh to populate IDs
        for cf in new_conflicts:
            db.refresh(cf)
            
    return new_conflicts


def run_dream_cycle(db: Session, client: Optional[GeminiClient] = None) -> dict:
    """
    Orchestrates the full dream cycle process.
    1. Processes unprocessed messages (limit 50).
    2. Runs supersession passes, compiled truth synthesis, and contradiction probes on dirty entities.
    """
    if client is None:
        client = get_client()
        
    # 1. Process unprocessed messages
    stmt = select(UnifiedMessage).where(UnifiedMessage.is_processed == False).order_by(UnifiedMessage.timestamp.asc()).limit(50)
    unprocessed_msgs = list(db.scalars(stmt).all())
    
    msg_count = 0
    for msg in unprocessed_msgs:
        extract_from_message(db, msg, client)
        msg_count += 1
        
    # Get all entities
    entities = list(db.scalars(select(Entity)).all())
    
    synth_count = 0
    superseded_count = 0
    conflicts_found = 0
    
    for ent in entities:
        # Run supersession pass
        claims_super = run_supersession_pass(db, ent, client)
        superseded_count += claims_super
        
        # Run compiled truth synthesis
        # Record prior state to see if synthesis actually wrote new truth
        prior_truth = ent.compiled_truth
        synthesized_truth = synthesize_entity(db, ent, client)
        if synthesized_truth != prior_truth:
            synth_count += 1
            
        # Run contradiction probe
        new_conflicts = run_contradiction_probe(db, ent, client)
        conflicts_found += len(new_conflicts)
        
    return {
        "messages_processed": msg_count,
        "entities_synthesized": synth_count,
        "claims_superseded": superseded_count,
        "conflicts_found": conflicts_found
    }
