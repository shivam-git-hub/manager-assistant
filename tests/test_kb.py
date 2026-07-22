import os
import pytest
from datetime import datetime
from fastapi.testclient import TestClient

from app import timeservice
from app.database import Project, TeamMember, UnifiedMessage
from app.kb.models import Entity, TimelineEntry, AttributedClaim, Conflict

@pytest.fixture(autouse=True)
def setup_tmp_clock(monkeypatch, tmp_path):
    """
    Automatically redirect SIM_CLOCK_PATH to a temporary file for every test
    to guarantee perfect isolation.
    """
    tmp_file = tmp_path / "sim_clock.json"
    monkeypatch.setenv("SIM_CLOCK_PATH", str(tmp_file))
    if os.path.exists(tmp_file):
        os.remove(tmp_file)
    
    if hasattr(timeservice, "_reset_state_for_tests"):
        timeservice._reset_state_for_tests()
        
    yield tmp_file
    
    if os.path.exists(tmp_file):
        try:
            os.remove(tmp_file)
        except OSError:
            pass

def test_01_entity_validations(client, db_session):
    """
    1. Entity create → 200/201; duplicate slug → 409; bad type → 422.
    """
    # Happy path
    payload = {
        "slug": "project:phoenix",
        "type": "project",
        "name": "Project Phoenix"
    }
    response = client.post("/api/kb/entities", json=payload)
    assert response.status_code == 201
    assert response.json()["slug"] == "project:phoenix"
    assert response.json()["type"] == "project"
    
    # Duplicate slug -> 409
    response_dup = client.post("/api/kb/entities", json=payload)
    assert response_dup.status_code == 409
    
    # Bad type -> 422
    payload_bad_type = {
        "slug": "project:vibe",
        "type": "vibe",
        "name": "Bad Project Type"
    }
    response_bad = client.post("/api/kb/entities", json=payload_bad_type)
    assert response_bad.status_code == 422

def test_02_auto_creation_entities(client, db_session):
    """
    2. POST /api/team auto-creates person:<id> entity (idempotent on repeat).

    Used to also cover POST /api/projects auto-creating a project:<slug>
    entity, but that v1 per-manager /api/projects handler was removed in
    step 18 (prompts/step_18_registry_and_scaffold.md §3) -- /api/projects
    is now the global control-plane registry (app.api.projects_registry),
    which has no KB-entity auto-creation side effect.
    """
    # Post TeamMember
    member_payload = {
        "id": "U_CHARLIE",
        "name": "Charlie Dev",
        "role": "Frontend",
        "slack_handle": "U_CHARLIE",
        "outlook_email": "charlie@company.com"
    }
    resp_member = client.post("/api/team", json=member_payload)
    assert resp_member.status_code == 201
    
    # Verify entity was automatically created
    ent_m = db_session.query(Entity).filter(Entity.slug == "person:U_CHARLIE").first()
    assert ent_m is not None
    assert ent_m.type == "person"
    assert ent_m.name == "Charlie Dev"

def test_03_timeline_append(client, db_session):
    """
    3. Timeline append with explicit happened_at + one defaulting to sim now
       (set sim time first, assert stamped value); newest-first ordering;
       bad source_message_id → 404; no DELETE/PUT route exists (405/404).
    """
    # Create Entity first
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    db_session.add(entity)
    db_session.commit()
    
    # Anchor sim time to Wed Jul 15 12:00:00 2026
    anchor_time = datetime(2026, 7, 15, 12, 0, 0)
    timeservice.set_time(anchor_time)
    
    # Timeline entry 1: defaulting to sim now
    payload_default = {
        "summary": "First kickoff meeting"
    }
    res1 = client.post("/api/kb/entities/project:phoenix/timeline", json=payload_default)
    assert res1.status_code == 201
    diff_1 = abs((datetime.fromisoformat(res1.json()["happened_at"]) - anchor_time).total_seconds())
    assert diff_1 < 2.0
    
    # Timeline entry 2: explicit happened_at
    explicit_time = datetime(2026, 7, 14, 10, 0, 0)
    payload_explicit = {
        "happened_at": explicit_time.isoformat(),
        "summary": "Prior prep sync"
    }
    res2 = client.post("/api/kb/entities/project:phoenix/timeline", json=payload_explicit)
    assert res2.status_code == 201
    assert datetime.fromisoformat(res2.json()["happened_at"]) == explicit_time
    
    # Check newest-first ordering on list
    res_list = client.get("/api/kb/entities/project:phoenix/timeline")
    assert res_list.status_code == 200
    timeline = res_list.json()
    assert len(timeline) == 2
    # Kickoff (Jul 15) must be first, sync (Jul 14) must be second
    assert timeline[0]["summary"] == "First kickoff meeting"
    assert timeline[1]["summary"] == "Prior prep sync"
    
    # Bad source_message_id -> 404
    payload_bad_msg = {
        "summary": "Some status",
        "source_message_id": 99999
    }
    res_bad = client.post("/api/kb/entities/project:phoenix/timeline", json=payload_bad_msg)
    assert res_bad.status_code == 404
    
    # No PUT or DELETE routes exist for timeline
    res_delete = client.delete("/api/kb/entities/project:phoenix/timeline")
    assert res_delete.status_code in {404, 405}
    res_put = client.put("/api/kb/entities/project:phoenix/timeline", json={"summary": "wrong"})
    assert res_put.status_code in {404, 405}

def test_04_claim_create_validations(client, db_session):
    """
    4. Claim create with weight 1.2 → 422; kind "vibe" → 422; happy path stores
       defaults (active=True, claimed_at = sim now).
    """
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    db_session.add(entity)
    db_session.commit()
    
    # Weight 1.2 -> 422
    payload_bad_weight = {
        "claim": "Alice finished backend",
        "kind": "fact",
        "holder": "U_ALICE",
        "weight": 1.2
    }
    res1 = client.post("/api/kb/entities/project:phoenix/claims", json=payload_bad_weight)
    assert res1.status_code == 422
    
    # Kind "vibe" -> 422
    payload_bad_kind = {
        "claim": "Everyone is happy",
        "kind": "vibe",
        "holder": "U_ALICE",
        "weight": 0.9
    }
    res2 = client.post("/api/kb/entities/project:phoenix/claims", json=payload_bad_kind)
    assert res2.status_code == 422
    
    # Happy path
    anchor_time = datetime(2026, 7, 15, 12, 0, 0)
    timeservice.set_time(anchor_time)
    
    payload_good = {
        "claim": "Finished API contract",
        "kind": "fact",
        "holder": "U_ALICE",
        "weight": 1.0
    }
    res3 = client.post("/api/kb/entities/project:phoenix/claims", json=payload_good)
    assert res3.status_code == 201
    data = res3.json()
    assert data["active"] is True
    diff_g = abs((datetime.fromisoformat(data["claimed_at"]) - anchor_time).total_seconds())
    assert diff_g < 2.0

def test_05_claim_supersede(client, db_session):
    """
    5. Supersede: create claim A, supersede with B → A inactive +
       superseded_by == B.id, B active; superseding A again → 409;
       ?active=true returns only B; no filter returns both.
    """
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    db_session.add(entity)
    db_session.commit()
    
    # Create Claim A
    claim_a = AttributedClaim(
        entity_id=entity.id,
        claim="Bob is working on CSS",
        kind="status",
        holder="U_BOB",
        weight=1.0,
        claimed_at=timeservice.now_ist(),
        active=True
    )
    db_session.add(claim_a)
    db_session.commit()
    
    # Supersede claim A with B
    payload_b = {
        "claim": "Bob finished CSS",
        "kind": "status",
        "holder": "U_BOB",
        "weight": 1.0
    }
    res = client.post(f"/api/kb/claims/{claim_a.id}/supersede", json=payload_b)
    assert res.status_code == 200
    claims_both = res.json()
    assert len(claims_both) == 2
    
    # Old claim A inactive & points to B
    assert claims_both[0]["id"] == claim_a.id
    assert claims_both[0]["active"] is False
    assert claims_both[0]["superseded_by"] == claims_both[1]["id"]
    
    # New claim B active
    assert claims_both[1]["active"] is True
    assert claims_both[1]["claim"] == "Bob finished CSS"
    
    # Try superseding A again -> 409
    res_again = client.post(f"/api/kb/claims/{claim_a.id}/supersede", json=payload_b)
    assert res_again.status_code == 409
    
    # GET with ?active=true returns only B
    res_active = client.get("/api/kb/entities/project:phoenix/claims?active=true")
    assert res_active.status_code == 200
    assert len(res_active.json()) == 1
    assert res_active.json()[0]["id"] == claims_both[1]["id"]
    
    # GET with no active filter returns both
    res_all = client.get("/api/kb/entities/project:phoenix/claims")
    assert res_all.status_code == 200
    assert len(res_all.json()) == 2

def test_06_conflict_creation(client, db_session):
    """
    6. Conflict create with claims from two different entities → 422; happy path
       → open conflict listed in GET /api/kb/conflicts?status=open.
    """
    ent1 = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    ent2 = Entity(slug="project:other", type="project", name="Other Project")
    db_session.add_all([ent1, ent2])
    db_session.commit()
    
    claim1 = AttributedClaim(
        entity_id=ent1.id,
        claim="Schema done",
        kind="status",
        holder="U_ALICE",
        weight=1.0,
        claimed_at=timeservice.now_ist(),
        active=True
    )
    claim2 = AttributedClaim(
        entity_id=ent2.id,
        claim="Schema NOT done",
        kind="status",
        holder="U_BOB",
        weight=1.0,
        claimed_at=timeservice.now_ist(),
        active=True
    )
    db_session.add_all([claim1, claim2])
    db_session.commit()
    
    # Conflict between claims on two different entities -> 422
    payload_bad = {
        "entity_slug": "project:phoenix",
        "claim_a_id": claim1.id,
        "claim_b_id": claim2.id,
        "severity": "high",
        "description": "Contradiction across projects!"
    }
    res_bad = client.post("/api/kb/conflicts", json=payload_bad)
    assert res_bad.status_code == 422
    
    # Fix claim2 to belong to ent1
    claim2.entity_id = ent1.id
    db_session.commit()
    
    # Happy path
    payload_good = {
        "entity_slug": "project:phoenix",
        "claim_a_id": claim1.id,
        "claim_b_id": claim2.id,
        "severity": "high",
        "description": "Schema is both done and not done!"
    }
    res_good = client.post("/api/kb/conflicts", json=payload_good)
    assert res_good.status_code == 201
    
    # Get conflicts?status=open
    res_list = client.get("/api/kb/conflicts?status=open")
    assert res_list.status_code == 200
    conflicts = res_list.json()
    assert len(conflicts) == 1
    assert conflicts[0]["entity_id"] == ent1.id
    assert conflicts[0]["claim_a_id"] == claim1.id

def test_07_conflict_resolution(client, db_session):
    """
    7. Conflict PATCH resolve → status, resolved_at (sim time), note set;
       entity page payload no longer includes it (open only).
    """
    ent = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    db_session.add(ent)
    db_session.commit()
    
    claim1 = AttributedClaim(entity_id=ent.id, claim="X", kind="status", holder="U_A", weight=1.0, claimed_at=timeservice.now_ist(), active=True)
    claim2 = AttributedClaim(entity_id=ent.id, claim="Y", kind="status", holder="U_B", weight=1.0, claimed_at=timeservice.now_ist(), active=True)
    db_session.add_all([claim1, claim2])
    db_session.commit()
    
    conflict = Conflict(
        entity_id=ent.id,
        claim_a_id=claim1.id,
        claim_b_id=claim2.id,
        severity="medium",
        description="Contradiction",
        status="open",
        detected_at=timeservice.now_ist()
    )
    db_session.add(conflict)
    db_session.commit()
    
    # Anchor sim time
    anchor_time = datetime(2026, 7, 15, 12, 0, 0)
    timeservice.set_time(anchor_time)
    
    # Resolve conflict
    payload_patch = {
        "status": "resolved",
        "resolution_note": "Bob verified everything."
    }
    res_patch = client.patch(f"/api/kb/conflicts/{conflict.id}", json=payload_patch)
    assert res_patch.status_code == 200
    data = res_patch.json()
    assert data["status"] == "resolved"
    assert data["resolution_note"] == "Bob verified everything."
    diff_patch = abs((datetime.fromisoformat(data["resolved_at"]) - anchor_time).total_seconds())
    assert diff_patch < 2.0
    
    # Verify entity page doesn't return resolved conflict
    res_page = client.get("/api/kb/entities/project:phoenix")
    assert res_page.status_code == 200
    assert len(res_page.json()["conflicts"]) == 0

def test_08_entity_page_payload(client, db_session):
    """
    8. Entity page payload (GET /api/kb/entities/{slug}) contains truth field,
       timeline newest-first, only active claims, only open conflicts.
    """
    ent = Entity(slug="project:phoenix", type="project", name="Project Phoenix", compiled_truth="Highly active project.", truth_updated_at=timeservice.now_ist())
    db_session.add(ent)
    db_session.commit()
    
    # Timeline
    t1 = TimelineEntry(entity_id=ent.id, happened_at=datetime(2026, 7, 14), summary="Older event")
    t2 = TimelineEntry(entity_id=ent.id, happened_at=datetime(2026, 7, 15), summary="Newer event")
    db_session.add_all([t1, t2])
    
    # Claims
    claim_active = AttributedClaim(entity_id=ent.id, claim="Active Claim", kind="fact", holder="U_A", weight=1.0, claimed_at=timeservice.now_ist(), active=True)
    claim_inactive = AttributedClaim(entity_id=ent.id, claim="Old Inactive Claim", kind="fact", holder="U_A", weight=1.0, claimed_at=timeservice.now_ist(), active=False)
    db_session.add_all([claim_active, claim_inactive])
    
    # Conflicts
    conflict_open = Conflict(entity_id=ent.id, claim_a_id=1, claim_b_id=2, severity="low", description="Open conflict", status="open", detected_at=timeservice.now_ist())
    conflict_resolved = Conflict(entity_id=ent.id, claim_a_id=1, claim_b_id=2, severity="low", description="Resolved conflict", status="resolved", detected_at=timeservice.now_ist())
    db_session.add_all([conflict_open, conflict_resolved])
    db_session.commit()
    
    # Get Entity Page
    res = client.get("/api/kb/entities/project:phoenix")
    assert res.status_code == 200
    data = res.json()
    
    # Truth
    assert data["compiled_truth"] == "Highly active project."
    assert "truth_updated_at" in data
    
    # Timeline sorted newest-first
    assert len(data["timeline"]) == 2
    assert data["timeline"][0]["summary"] == "Newer event"
    assert data["timeline"][1]["summary"] == "Older event"
    
    # Active claims only
    assert len(data["claims"]) == 1
    assert data["claims"][0]["claim"] == "Active Claim"
    
    # Open conflicts only
    assert len(data["conflicts"]) == 1
    assert data["conflicts"][0]["description"] == "Open conflict"

def test_09_kb_search(client, db_session):
    """
    9. Search: seed an entity + claim + timeline entry all mentioning "phoenix";
       ?q=phoenix -> hits in all three groups; ?q=zzz -> empty.
    """
    # Create entity mentioning phoenix in name
    ent_phoenix = Entity(slug="project:phoenix", type="project", name="Project Phoenix", compiled_truth="None")
    # Create entity mentioning phoenix in compiled truth
    ent_other = Entity(slug="project:other", type="project", name="Other", compiled_truth="Mentions phoenix here!")
    db_session.add_all([ent_phoenix, ent_other])
    db_session.commit()
    
    # Claim with phoenix
    claim = AttributedClaim(entity_id=ent_phoenix.id, claim="phoenix is awesome", kind="fact", holder="U_A", weight=1.0, claimed_at=timeservice.now_ist(), active=True)
    db_session.add(claim)
    
    # Timeline with phoenix
    timeline = TimelineEntry(entity_id=ent_phoenix.id, happened_at=timeservice.now_ist(), summary="Finished phoenix setup")
    db_session.add(timeline)
    db_session.commit()
    
    # Search for "phoenix"
    res = client.get("/api/kb/search?q=phoenix")
    assert res.status_code == 200
    data = res.json()
    
    # Matches
    assert len(data["entities"]) == 2
    assert len(data["claims"]) == 1
    assert len(data["timeline"]) == 1
    
    # Search for "zzz"
    res_empty = client.get("/api/kb/search?q=zzz")
    assert res_empty.status_code == 200
    data_empty = res_empty.json()
    assert len(data_empty["entities"]) == 0
    assert len(data_empty["claims"]) == 0
    assert len(data_empty["timeline"]) == 0
