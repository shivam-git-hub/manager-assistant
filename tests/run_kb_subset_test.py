"""
Knowledge Base Processing Pipeline Test Runner for Ingestion and Heartbeat.
This script tests the 1st and 2nd jobs (Ingestion & Heartbeat) using a subset
of 10 representative threads evaluated by an LLM-as-a-Judge.
"""

import os
import sys
import json
import uuid
import shutil
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from sqlalchemy import select, text
from sqlalchemy.orm import Session

# Ensure correct python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import timeservice
from app.config import FLASH_MODEL, SMART_MODEL, GEMINI_API_KEY
from app.database import Base, UnifiedMessage, Claim, ClaimSource, Event, TeamMember
from app.agent.gemini_client import GeminiClient, get_client
from app.projectkb.jobs import ingestion, heartbeat
from app.projectkb import blocklist
from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    Manager,
    Project as RegistryProject,
    ProjectMember,
    Employee,
    init_controlplane_db,
)
from app.projects.db import get_project_session
from app.projects.paths import ensure_project_scaffold, project_dir

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("kb_pipeline_subset_judge")

# Path to the 30-threads dataset
DATASET_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "kb_test_dataset.json"))

# 10 Representative threads selected for this test
SUBSET_THREAD_IDS = [
    "thread_01_scope_block",          # Blocker, Slack, Process
    "thread_02_dashboard_vercel",     # Status Update, Outlook, Process
    "thread_03_qa_tests_commit",      # Commitment, Slack, Process
    "thread_05_blocked_spam",         # Blocked Contact, Outlook, Skip
    "thread_06_slack_join_noise",      # Noise Join, Slack, Skip
    "thread_08_schema_conflict",      # Conflict, Outlook, Process
    "thread_09_designer_theme",       # Clarification, Slack, Process
    "thread_10_auth_middleware_pr",   # Request, Slack, Process
    "thread_12_linear_inspiration",   # FYI, Slack, Process
    "thread_14_blocked_memes_channel" # Blocked Channel, Slack, Skip
]

class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"

def print_section(title: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}══════════════════════════════════════════════════════════════════════")
    print(f"  {title.upper()}")
    print(f"══════════════════════════════════════════════════════════════════════{Colors.ENDC}")

def print_success(msg: str):
    print(f"{Colors.OKGREEN}✓ SUCCESS: {msg}{Colors.ENDC}")

def print_fail(msg: str):
    print(f"{Colors.FAIL}✗ FAILED: {msg}{Colors.ENDC}")

def print_info(msg: str):
    print(f"{Colors.OKBLUE}ℹ INFO: {msg}{Colors.ENDC}")


# ─── LLM JUDGE PROMPTS ─────────────────────────────────────────────────────────
CLAIMS_JUDGE_SYSTEM = (
    "You are an expert AI quality assurance auditor. Your task is to evaluate the accuracy "
    "of claims extracted from email and chat threads against a gold-standard reference list.\n\n"
    "You must output strict JSON in the following format:\n"
    "{\n"
    '  "score": 0.0 to 1.0,\n'
    '  "decision": "PASS" or "FAIL",\n'
    '  "recall_critique": "Analysis of what key details were missed (if any).",\n'
    '  "precision_critique": "Analysis of any false or hallucinated details (if any).",\n'
    '  "reason": "Detailed summary explanation of the final decision and score."\n'
    "}\n\n"
    "Rule for PASS: A score >= 0.8 is a PASS."
)

EVENTS_JUDGE_SYSTEM = (
    "You are an expert AI product quality auditor. Your task is to evaluate whether a "
    "list of processed claims was correctly judged and grouped into timeline events for a manager's dashboard.\n\n"
    "You must output strict JSON in the following format:\n"
    "{\n"
    '  "score": 0.0 to 1.0,\n'
    '  "decision": "PASS" or "FAIL",\n'
    '  "type_correct": true/false,\n'
    '  "severity_correct": true/false,\n'
    '  "reason": "Detailed summary explanation of your judgment."\n'
    "}\n\n"
    "Rule for PASS: A score >= 0.8 is a PASS."
)

class KBSubsetHarness:
    manager_email: str
    manager_id: str
    db_session: Session
    client: GeminiClient
    project_mappings: Dict[str, str]
    dataset: List[Dict[str, Any]]
    team_employees: Dict[str, Dict[str, Any]]

    def __init__(self, manager_email: str = "test-judge-manager@test.local"):
        self.manager_email = manager_email
        self.manager_id = ""
        self.db_session = None # type: ignore
        self.client = None # type: ignore
        self.project_mappings = {}

    def setup(self):
        os.environ["CONTROLPLANE_DB_FILENAME"] = "test_controlplane_subset_judge.sqlite"
        init_controlplane_db()
        
        cdb = ControlPlaneSessionLocal()
        
        self.manager_id = uuid.uuid4().hex
        mgr = Manager(
            id=self.manager_id,
            email=self.manager_email,
            name="Judge Manager",
            created_at=timeservice.now_ist(),
        )
        cdb.add(mgr)
        
        emp_mgr = Employee(
            id=uuid.uuid4().hex,
            email=self.manager_email,
            name="Judge Manager",
            role="Manager"
        )
        cdb.add(emp_mgr)
        
        self.team_employees = {
            "alice@example.com": {"name": "Alice Chen", "slack_id": "U0000001", "role": "Backend Engineer"},
            "bob@example.com": {"name": "Bob Iyer", "slack_id": "U0000002", "role": "Frontend Engineer"},
            "carol@example.com": {"name": "Carol Fernandes", "slack_id": "U0000003", "role": "Product Designer"},
            "dave@example.com": {"name": "Dave Menon", "slack_id": "U0000004", "role": "QA Engineer"},
        }
        for email, info in self.team_employees.items():
            emp = Employee(
                id=uuid.uuid4().hex,
                email=email,
                name=info["name"],
                slack_id=info["slack_id"],
                role=info["role"],
            )
            cdb.add(emp)
            
        projects_to_register = [
            ("AI chief of Staff", "Greenfield AI manager assistant"),
            ("Pulse.ai Frontend Rebuild", "React-Vite-Tailwind dashboard")
        ]
        for name, desc in projects_to_register:
            proj = RegistryProject(
                id=uuid.uuid4().hex,
                name=name,
                description=desc,
                kind="team",
                manager_user_id=self.manager_id
            )
            cdb.add(proj)
            cdb.flush()
            self.project_mappings[name] = proj.id
            ensure_project_scaffold(proj.id, proj.name, proj.description)
            
            for email in self.team_employees:
                emp_row = cdb.query(Employee).filter(Employee.email == email).first()
                assert emp_row is not None
                cdb.add(ProjectMember(
                    id=uuid.uuid4().hex,
                    project_id=proj.id,
                    employee_id=emp_row.id,
                ))
                
        cdb.commit()
        cdb.close()
        
        from app.tenancy.db import init_manager_db, get_manager_session
        init_manager_db(self.manager_id)
        self.db_session = get_manager_session(self.manager_id)
        
        for email, info in self.team_employees.items():
            self.db_session.add(TeamMember(
                id=info["slack_id"] or email,
                name=info["name"],
                role=info["role"],
                slack_handle=info["slack_id"],
                outlook_email=email,
            ))
        self.db_session.commit()
        
        self.client = get_client()
        
        # Add blocklists
        blocklist.add_blocked_contact(self.manager_id, label="spammy", email_pattern="*@spammytracker.com")
        blocklist.add_blocked_contact(self.manager_id, label="evil_recruiter", email_pattern="recruiter@evilheadhunter.com")
        blocklist.add_blocked_channel(self.manager_id, label="memes_block", source="slack", pattern="C_RANDOM_MEMES")
        
        # Load dataset and filter to subset
        with open(DATASET_PATH, "r", encoding="utf-8") as f:
            all_threads = json.load(f)
            self.dataset = [t for t in all_threads if t["thread_id"] in SUBSET_THREAD_IDS]
            
        print_info(f"Loaded {len(self.dataset)} subset threads for testing.")

    def teardown(self):
        if self.db_session:
            self.db_session.close()
        from app.controlplane.models import CONTROLPLANE_DB_PATH, engine
        engine.dispose()
        if CONTROLPLANE_DB_PATH.exists():
            try:
                CONTROLPLANE_DB_PATH.unlink()
            except OSError:
                pass
        from app.tenancy.paths import manager_dir
        if self.manager_id:
            shutil.rmtree(manager_dir(self.manager_id), ignore_errors=True)
        for pid in self.project_mappings.values():
            shutil.rmtree(project_dir(pid), ignore_errors=True)


def run_subset_test():
    if not GEMINI_API_KEY:
        print_fail("GEMINI_API_KEY is not set. Cannot run evaluation.")
        return
        
    print_section("Starting Subset Evaluation: Ingestion & Heartbeat (10 Examples)")
    
    harness = KBSubsetHarness()
    try:
        harness.setup()
        
        # ─── SEED MESSAGES ───
        print_section("Phase 1: Seeding & Classification Check")
        class_passed = 0
        for thread in harness.dataset:
            thread_id = thread["thread_id"]
            source = thread["source"]
            subject = thread["subject"]
            channel_raw_id = thread["channel_raw_id"]
            
            for msg_data in thread["messages"]:
                um = UnifiedMessage(
                    platform_msg_id=msg_data["platform_msg_id"],
                    source=source,
                    sender_raw_id=msg_data["sender_raw_id"],
                    sender_mapped_name=msg_data["sender_mapped_name"],
                    receiver_raw_id=msg_data["receiver_raw_id"],
                    receiver_mapped_name=msg_data["receiver_mapped_name"],
                    channel_raw_id=channel_raw_id,
                    thread_id=thread_id,
                    subject=subject,
                    content=msg_data["content"],
                    timestamp=datetime.strptime(msg_data["timestamp"], "%Y-%m-%d %H:%M:%S"),
                    created_at=timeservice.now_ist(),
                    is_processed=False,
                    raw_metadata=msg_data["raw_metadata"]
                )
                harness.db_session.add(um)
                harness.db_session.commit()
                harness.db_session.refresh(um)
                
                # Check classification
                classified = blocklist.classify_message(harness.manager_id, um)
                expected = msg_data["expected_classification"]
                passed = (classified == expected) or (expected == "process" and classified is None)
                if passed:
                    class_passed += 1
                    print_success(f"Message {um.platform_msg_id} ({thread_id}) classified correctly as '{classified or 'process'}'")
                else:
                    print_fail(f"Message {um.platform_msg_id} ({thread_id}) misclassified! Expected: {expected}, Got: {classified or 'process'}")
                    
        print_info(f"Classification result: {class_passed}/{len(harness.dataset)} correct.")

        # ─── INGESTION JOB ───
        print_section("Phase 2: Claims Ingestion Job (LLM-as-a-Judge)")
        stats_ingest = ingestion.run(harness.db_session, harness.manager_id, client=harness.client)
        print_info(f"Ingestion Completed. Stats: {stats_ingest}")
        
        ingest_passes = 0
        for thread in harness.dataset:
            thread_id = thread["thread_id"]
            expected_class = thread["messages"][0]["expected_classification"]
            
            if expected_class in ("blocked", "noise"):
                # verify skipped
                db_claims = harness.db_session.scalars(
                    select(Claim).where(Claim.thread_key == thread_id)
                ).all()
                if len(db_claims) == 0:
                    ingest_passes += 1
                    print_success(f"Thread {thread_id} skipped correctly as noise/blocked (Claims count: 0)")
                else:
                    print_fail(f"Thread {thread_id} should have been skipped, but found claims!")
                continue
                
            # Extract actual claims
            db_claims = harness.db_session.scalars(
                select(Claim).where(Claim.thread_key == thread_id)
            ).all()
            actual_claims = [c.text for c in db_claims]
            
            # Judge
            prompt = (
                f"### Original Thread:\n" + 
                "\n".join(f"{m['sender_mapped_name']}: {m['content']}" for m in thread["messages"]) +
                f"\n\n### Expected Reference Claims:\n" +
                "\n".join(f"- {c}" for c in thread["expected_claims"]) +
                f"\n\n### System Extracted Claims:\n" +
                ("\n".join(f"- {c}" for c in actual_claims) if actual_claims else "(No claims extracted)")
            )
            
            judge_res = harness.client.chat(
                model=SMART_MODEL,
                messages=[
                    {"role": "system", "content": CLAIMS_JUDGE_SYSTEM},
                    {"role": "user", "content": prompt}
                ],
                json_mode=True
            )
            
            try:
                eval_data = json.loads(judge_res["content"])
                score = eval_data.get("score", 0.0)
                decision = eval_data.get("decision", "FAIL")
                reason = eval_data.get("reason", "N/A")
            except Exception:
                score, decision, reason = 0.5, "FAIL", "Unparseable judge output."
                
            if decision == "PASS":
                ingest_passes += 1
                print_success(f"Claims Extraction PASS: Thread {thread_id} | Score: {score:.2f} | Reason: {reason}")
            else:
                print_fail(f"Claims Extraction FAIL: Thread {thread_id} | Score: {score:.2f} | Reason: {reason}")
                
        # ─── HEARTBEAT JOB ───
        print_section("Phase 3: Heartbeat Dashboard Events (LLM-as-a-Judge)")
        stats_hb = heartbeat.run(harness.db_session, harness.manager_id, client=harness.client)
        print_info(f"Heartbeat Completed. Stats: {stats_hb}")
        
        hb_passes = 0
        total_eval_threads = 0
        for thread in harness.dataset:
            thread_id = thread["thread_id"]
            expected_class = thread["messages"][0]["expected_classification"]
            
            if expected_class in ("blocked", "noise"):
                continue # No events generated for skipped threads
                
            total_eval_threads += 1
            claims = harness.db_session.scalars(
                select(Claim).where(Claim.thread_key == thread_id)
            ).all()
            claim_ids = [c.id for c in claims]
            
            # Find matching events
            db_events = harness.db_session.scalars(select(Event)).all()
            matching_events = []
            for e in db_events:
                try:
                    citation_ids = json.loads(e.claim_ids or "[]")
                    if any(cid in citation_ids for cid in claim_ids):
                        matching_events.append(e)
                except Exception:
                    pass
            
            claims_text_block = "\n".join(f"- [id={c.id}] {c.text}" for c in claims)
            actual_events_block = ""
            for e in matching_events:
                actual_events_block += (
                    f"- EVENT_ID: {e.id}\n"
                    f"  Title: {e.title}\n"
                    f"  Type: {e.type}\n"
                    f"  Severity: {e.severity}\n"
                    f"  Body: {e.body or 'None'}\n\n"
                )
            if not actual_events_block:
                actual_events_block = "(No events generated)"
                
            prompt = (
                f"### Input Claims:\n{claims_text_block}\n\n"
                f"### Target Expectation:\n"
                f"- Event Category: {thread['expected_event_type']}\n"
                f"- Targeted Severity: {thread['expected_event_severity']}\n\n"
                f"### Generated Events:\n{actual_events_block}"
            )
            
            judge_res = harness.client.chat(
                model=SMART_MODEL,
                messages=[
                    {"role": "system", "content": EVENTS_JUDGE_SYSTEM},
                    {"role": "user", "content": prompt}
                ],
                json_mode=True
            )
            
            try:
                eval_data = json.loads(judge_res["content"])
                score = eval_data.get("score", 0.0)
                decision = eval_data.get("decision", "FAIL")
                reason = eval_data.get("reason", "N/A")
            except Exception:
                score, decision, reason = 0.5, "FAIL", "Unparseable judge output."
                
            if decision == "PASS":
                hb_passes += 1
                print_success(f"Event Classification PASS: Thread {thread_id} | Score: {score:.2f} | Reason: {reason}")
            else:
                print_fail(f"Event Classification FAIL: Thread {thread_id} | Score: {score:.2f} | Reason: {reason}")
                
        print_section("Summary of Subset Test Result")
        print_info(f"Ingestion/Claims validation: {ingest_passes}/{len(harness.dataset)} passed.")
        print_info(f"Heartbeat/Events validation: {hb_passes}/{total_eval_threads} passed.")
        
    finally:
        print_info("Tearing down temporary databases...")
        harness.teardown()

if __name__ == "__main__":
    run_subset_test()
