"""
Knowledge Base Pipeline Testing with LLM-as-a-Judge.
This script tests the four processing jobs (Ingestion, Heartbeat, Dream, Lint)
as well as the blocklist/noise classifier using a 30-thread evaluation dataset.

Can be run in two modes:
1. Standard Pytest test-suite (`pytest tests/test_kb_pipeline_judge.py`)
2. Standalone interactive script (`python3 tests/test_kb_pipeline_judge.py`)
   which generates a rich Markdown test report under `tests/test_kb_pipeline_report.md`.
"""

import os
import sys
import json
import uuid
import shutil
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

# Correct python path for local imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import timeservice
from app.config import FLASH_MODEL, SMART_MODEL, GEMINI_API_KEY
from app.database import Base, UnifiedMessage, Claim, ClaimSource, Event, TeamMember
from app.agent.gemini_client import GeminiClient, get_client
from app.projectkb.jobs import ingestion, heartbeat, dream, lint
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
from app.projects.models import Task, Suggestion, Concern, HealthLog
from app.projects.paths import ensure_project_scaffold, project_dir

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("kb_pipeline_judge")

# Path to the 30-threads dataset
DATASET_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "kb_test_dataset.json"))


# ─── COLORFUL PRINTING HELPERS ──────────────────────────────────────────────────
class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"

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
    "You will be given:\n"
    "1. The original conversation thread.\n"
    "2. The target expected claims (gold-standard reference).\n"
    "3. The actually extracted claims from the system.\n\n"
    "Your evaluation should focus on:\n"
    "- Semantic coverage: Do the extracted claims capture the same key business facts (blockers, commitments, progress) as the expected claims?\n"
    "- Precision: Are there any false, hallucinated, or highly exaggerated claims in the extracted set that are unsupported by the thread?\n"
    "- Factuality: Are the claims objective, short, and free of conversational fluff?\n\n"
    "You must output strict JSON in the following format:\n"
    "{\n"
    '  "score": 0.0 to 1.0,\n'
    '  "decision": "PASS" or "FAIL",\n'
    '  "recall_critique": "Analysis of what key details were missed (if any).",\n'
    '  "precision_critique": "Analysis of any false or hallucinated details (if any).",\n'
    '  "reason": "Detailed summary explanation of the final decision and score."\n'
    "}\n\n"
    "Rule for PASS: A score >= 0.8 is a PASS. High semantic similarity in business value passes even if phrasing differs. Completely missing a major blocker, commitment, or conflict should result in a FAIL."
)

EVENTS_JUDGE_SYSTEM = (
    "You are an expert AI product quality auditor. Your task is to evaluate whether a "
    "list of processed claims was correctly judged and grouped into timeline events for a manager's dashboard.\n\n"
    "You will be given:\n"
    "1. The input claims extracted from the message thread.\n"
    "2. The expected event category and severity target.\n"
    "3. The actual generated events (with title, body, type, severity, and project tag).\n\n"
    "Your evaluation should check:\n"
    "- Type Alignment: Is the generated event type (status_update, blocker, clarification, commitment, request, conflict, fyi) logical?\n"
    "- Conflict Rule: If claims represent people contradicting each other (one says they did X, one says they didn't receive X/it's broken), is it correctly marked as 'conflict' (and NOT downgraded to blocker)?\n"
    "- Severity Alignment: Is the severity score (0 to 3) reasonable for the business impact? (Conflict = 3; Blocker = at least 1 or 2; FYI/routine progress = 0 or 1).\n"
    "- Dashboard Utility: Are the title and body professional, clear, and actionable for a senior manager?\n\n"
    "You must output strict JSON in the following format:\n"
    "{\n"
    '  "score": 0.0 to 1.0,\n'
    '  "decision": "PASS" or "FAIL",\n'
    '  "type_correct": true/false,\n'
    '  "severity_correct": true/false,\n'
    '  "reason": "Detailed summary explanation of your judgment."\n'
    "}\n\n"
    "Rule for PASS: A score >= 0.8 is a PASS. Correct classification of blockers, commitments, and conflicts is highly valued."
)

DREAM_JUDGE_SYSTEM = (
    "You are an expert executive auditor. Your task is to evaluate the quality of a synthesized "
    "executive summary, memory log, and project suggestions generated by a manager's AI assistant.\n\n"
    "You will be given:\n"
    "1. The underlying dashboard events recorded for a project.\n"
    "2. The synthesized project.md and memory.md before/after files.\n"
    "3. The newly generated recommendations (Suggestions & Concerns) and calculated project health score.\n\n"
    "Your evaluation should verify:\n"
    "- Executive Value: Are the synthesized outputs clean, free of conversational filler, and highly professional?\n"
    "- Actionability: Do the suggestions provide real value to help a project manager unblock team members or resolve disputes?\n"
    "- Health Justification: Does the health rubric match the events (e.g. active blocker or conflict should downgrade health, and the reason must cite the concrete event)?\n\n"
    "You must output strict JSON in the following format:\n"
    "{\n"
    '  "score": 0.0 to 1.0,\n'
    '  "decision": "PASS" or "FAIL",\n'
    '  "suggestions_quality": "High/Medium/Low",\n'
    '  "health_rubric_logical": true/false,\n'
    '  "reason": "Detailed summary of your evaluation."\n'
    "}\n\n"
    "Rule for PASS: A score >= 0.8 is a PASS."
)


# ─── TEST FIXTURE AND HARNESS SETUP ─────────────────────────────────────────────
class KBTestHarness:
    manager_email: str
    manager_id: str
    db_session: Session
    client: GeminiClient
    project_mappings: Dict[str, str]
    report_data: List[Any]
    team_employees: Dict[str, Dict[str, Any]]
    dataset: List[Dict[str, Any]]

    def __init__(self, manager_email: str = "test-judge-manager@test.local"):
        self.manager_email = manager_email
        self.manager_id = ""
        # We initialize self.db_session and self.client with cast or dummy to keep Pyright happy
        self.db_session = None  # type: ignore
        self.client = None      # type: ignore
        self.project_mappings = {} # Maps name -> global project registry ID
        self.report_data = [] # To accumulate test results for standalone reporting

    def setup(self):
        """Sets up a completely fresh, isolated testing environment."""
        # 1. Initialize controlplane
        os.environ["CONTROLPLANE_DB_FILENAME"] = "test_controlplane_judge.sqlite"
        init_controlplane_db()
        
        # Create fresh control plane session
        cdb = ControlPlaneSessionLocal()
        
        # Delete existing manager directories if they exist
        from app.tenancy.paths import manager_dir
        
        # Create fresh Manager in Control Plane
        self.manager_id = uuid.uuid4().hex
        mgr = Manager(
            id=self.manager_id,
            email=self.manager_email,
            name="Judge Manager",
            created_at=timeservice.now_ist(),
        )
        cdb.add(mgr)
        
        # Create corresponding Employee record for manager self-lookup
        emp_mgr = Employee(
            id=uuid.uuid4().hex,
            email=self.manager_email,
            name="Judge Manager",
            role="Manager"
        )
        cdb.add(emp_mgr)
        
        # Add team member employee records from employees.json definitions
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
            
        # Register the two test projects
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
            
            # Map members
            for email in self.team_employees:
                # Find matching employee
                emp_row = cdb.query(Employee).filter(Employee.email == email).first()
                assert emp_row is not None, f"Employee {email} should exist"
                cdb.add(ProjectMember(
                    id=uuid.uuid4().hex,
                    project_id=proj.id,
                    employee_id=emp_row.id,
                ))
                
        cdb.commit()
        cdb.close()
        
        # 2. Get the manager's tenant database session
        from app.tenancy.db import init_manager_db, get_manager_session
        init_manager_db(self.manager_id)
        self.db_session = get_manager_session(self.manager_id)
        
        # Seed team members into the tenant database
        for email, info in self.team_employees.items():
            self.db_session.add(TeamMember(
                id=info["slack_id"] or email,
                name=info["name"],
                role=info["role"],
                slack_handle=info["slack_id"],
                outlook_email=email,
            ))
        self.db_session.commit()
        
        # Initialize Gemini Client for judging
        self.client = get_client()
        
        # Configure blocklist rules for the manager
        # Contact rules
        blocklist.add_blocked_contact(self.manager_id, label="spammy", email_pattern="*@spammytracker.com")
        blocklist.add_blocked_contact(self.manager_id, label="evil_recruiter", email_pattern="recruiter@evilheadhunter.com")
        # Channel rules
        blocklist.add_blocked_channel(self.manager_id, label="memes_block", source="slack", pattern="C_RANDOM_MEMES")
        
        # Read dataset
        with open(DATASET_PATH, "r", encoding="utf-8") as f:
            self.dataset = json.load(f)

    def teardown(self):
        """Cleans up all temporary directories and databases."""
        if self.db_session:
            self.db_session.close()
            
        # Clean up control plane db file
        from app.controlplane.models import CONTROLPLANE_DB_PATH, engine
        engine.dispose()
        if CONTROLPLANE_DB_PATH.exists():
            try:
                CONTROLPLANE_DB_PATH.unlink()
            except OSError:
                pass
                
        # Clean up manager directories
        from app.tenancy.paths import manager_dir
        if self.manager_id:
            shutil.rmtree(manager_dir(self.manager_id), ignore_errors=True)
            
        # Clean up registered project scaffolds
        for pid in self.project_mappings.values():
            shutil.rmtree(project_dir(pid), ignore_errors=True)


# ─── THE TESTING SUITE FUNCTIONS ───────────────────────────────────────────────

def run_classification_tests(harness: KBTestHarness) -> List[Dict]:
    """Phase 1: Validates that classify_message filters blocklist/noise with 100% accuracy."""
    results = []
    print_info("Running Phase 1: Deterministic Blocklist and Noise Classification...")
    
    # Pre-seed messages from dataset into DB
    for thread in harness.dataset:
        thread_id = thread["thread_id"]
        source = thread["source"]
        subject = thread["subject"]
        channel_raw_id = thread["channel_raw_id"]
        
        for msg_data in thread["messages"]:
            # Insert message
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
            
            # Run the classifier
            classified = blocklist.classify_message(harness.manager_id, um)
            expected = msg_data["expected_classification"]
            
            passed = (classified == expected) or (expected == "process" and classified is None)
            
            res_dict = {
                "thread_id": thread_id,
                "msg_id": msg_data["platform_msg_id"],
                "content_snippet": msg_data["content"][:60] + "...",
                "expected": expected,
                "actual": classified or "process",
                "passed": passed
            }
            results.append(res_dict)
            
            if passed:
                logger.debug(f"Classified {msg_data['platform_msg_id']}: Expected={expected}, Actual={classified or 'process'} -> PASS")
            else:
                print_fail(f"Misclassified {msg_data['platform_msg_id']} ({thread_id}): Expected={expected}, Actual={classified or 'process'}")
                
    success_rate = sum(1 for r in results if r["passed"]) / len(results)
    print_info(f"Phase 1 Classification Accuracy: {success_rate * 100:.1f}% ({sum(1 for r in results if r['passed'])}/{len(results)})")
    
    return results


def run_ingestion_tests(harness: KBTestHarness) -> List[Dict]:
    """Phase 2: Runs the Ingest job and evaluates extracted claims using LLM-as-a-Judge."""
    results = []
    print_info("Running Phase 2: Ingest Job & LLM-as-a-Judge Claims Evaluation...")
    
    # 1. Run Ingestion (this runs classify_message internally to skip noise/blocklist, then calls Gemini on the rest)
    stats = ingestion.run(harness.db_session, harness.manager_id, client=harness.client)
    print_info(f"Ingestion Job finished. Stats: {stats}")
    
    # 2. Evaluate extracted claims per thread using LLM
    for thread in harness.dataset:
        thread_id = thread["thread_id"]
        expected_class = thread["messages"][0]["expected_classification"]
        
        # If the thread was blocked or noise, no claims should be extracted
        if expected_class in ("blocked", "noise"):
            # Verify no claims exist
            db_claims = harness.db_session.scalars(
                select(Claim).where(Claim.thread_key == thread_id)
            ).all()
            passed = len(db_claims) == 0
            results.append({
                "thread_id": thread_id,
                "type": "skipped",
                "passed": passed,
                "score": 1.0,
                "critique": "Correctly skipped as blocked/noise.",
                "claims": []
            })
            continue
            
        # Get extracted claims for this thread
        db_claims = harness.db_session.scalars(
            select(Claim).where(Claim.thread_key == thread_id)
        ).all()
        actual_claims_text = [c.text for c in db_claims]
        
        # Call LLM as judge
        prompt = (
            f"### Original Thread:\n" + 
            "\n".join(f"{m['sender_mapped_name']}: {m['content']}" for m in thread["messages"]) +
            f"\n\n### Expected Reference Claims:\n" +
            "\n".join(f"- {c}" for c in thread["expected_claims"]) +
            f"\n\n### System Extracted Claims:\n" +
            ("\n".join(f"- {c}" for c in actual_claims_text) if actual_claims_text else "(No claims extracted)")
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
        except Exception:
            eval_data = {
                "score": 0.5,
                "decision": "FAIL",
                "reason": f"Failed to parse JSON response from LLM Judge. Raw: {judge_res['content']}"
            }
            
        eval_data["thread_id"] = thread_id
        eval_data["type"] = "extracted"
        eval_data["claims"] = actual_claims_text
        eval_data["passed"] = eval_data["decision"] == "PASS"
        results.append(eval_data)
        
        if eval_data["passed"]:
            print_success(f"Thread {thread_id} Claims: Score={eval_data['score']:.2f}")
        else:
            print_fail(f"Thread {thread_id} Claims: Score={eval_data['score']:.2f} -> {eval_data['reason']}")
            
    return results


def run_heartbeat_tests(harness: KBTestHarness) -> List[Dict]:
    """Phase 3: Runs the Heartbeat job and evaluates event classifications using LLM-as-a-Judge."""
    results = []
    print_info("Running Phase 3: Heartbeat Job & LLM-as-a-Judge Events Evaluation...")
    
    # 1. Run Heartbeat job (processes claims into events)
    stats = heartbeat.run(harness.db_session, harness.manager_id, client=harness.client)
    print_info(f"Heartbeat Job finished. Stats: {stats}")
    
    # 2. Evaluate events created
    for thread in harness.dataset:
        thread_id = thread["thread_id"]
        expected_class = thread["messages"][0]["expected_classification"]
        
        if expected_class in ("blocked", "noise"):
            # Blocked/noise threads shouldn't produce events
            continue
            
        # Get claims for this thread to find associated events
        claims = harness.db_session.scalars(
            select(Claim).where(Claim.thread_key == thread_id)
        ).all()
        claim_ids = [c.id for c in claims]
        
        # Find any event containing these claim_ids in its citation JSON array
        db_events = harness.db_session.scalars(select(Event)).all()
        matching_events = []
        for e in db_events:
            try:
                citation_ids = json.loads(e.claim_ids or "[]")
                if any(cid in citation_ids for cid in claim_ids):
                    matching_events.append(e)
            except Exception:
                pass
                
        # Call LLM as judge
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
        except Exception:
            eval_data = {
                "score": 0.5,
                "decision": "FAIL",
                "reason": f"Failed to parse JSON response from LLM Judge."
            }
            
        eval_data["thread_id"] = thread_id
        eval_data["events"] = [
            {"id": e.id, "title": e.title, "type": e.type, "severity": e.severity}
            for e in matching_events
        ]
        eval_data["passed"] = eval_data["decision"] == "PASS"
        results.append(eval_data)
        
        if eval_data["passed"]:
            print_success(f"Thread {thread_id} Event Judgment: Score={eval_data['score']:.2f}")
        else:
            print_fail(f"Thread {thread_id} Event Judgment: Score={eval_data['score']:.2f} -> {eval_data['reason']}")
            
    return results


def run_dream_tests(harness: KBTestHarness) -> Dict[str, Any]:
    """Phase 4: Runs the Dream job and evaluates memory synthesis + health scores."""
    print_section("Running Phase 4: Dream Job & Synthesis Evaluation")
    
    # 1. Run Dream job (synthesizes memory, personal events, project summary/health/suggestions)
    stats = dream.run(harness.db_session, harness.manager_id, client=harness.client)
    print_info(f"Dream Job finished. Stats: {stats}")
    
    # 2. Read synthesized files
    from app.tenancy.paths import manager_memory_md_path, manager_events_md_path
    
    memory_md = manager_memory_md_path(harness.manager_id).read_text(encoding="utf-8") if manager_memory_md_path(harness.manager_id).exists() else ""
    events_md = manager_events_md_path(harness.manager_id).read_text(encoding="utf-8") if manager_events_md_path(harness.manager_id).exists() else ""
    
    # Get project health log and suggestions/concerns
    project_evals = {}
    for name, pid in harness.project_mappings.items():
        psession = get_project_session(pid)
        try:
            suggestions = psession.scalars(select(Suggestion)).all()
            concerns = psession.scalars(select(Concern)).all()
            health_logs = psession.scalars(select(HealthLog).order_by(HealthLog.timestamp.desc())).all()
            
            project_evals[name] = {
                "id": pid,
                "suggestions": [s.text for s in suggestions],
                "concerns": [c.text for c in concerns],
                "health": health_logs[0].base_score + health_logs[0].llm_adjustment if health_logs else "No health calculated",
                "health_reason": health_logs[0].reason if health_logs else "N/A"
            }
        finally:
            psession.close()
            
    # LLM evaluation of personal memory synthesis
    prompt = (
        f"### Synthesized memory.md:\n{memory_md or '(empty)'}\n\n"
        f"### Synthesized personal events.md:\n{events_md or '(empty)'}\n\n"
        f"### Project health and recommendations:\n{json.dumps(project_evals, indent=2)}"
    )
    
    judge_res = harness.client.chat(
        model=SMART_MODEL,
        messages=[
            {"role": "system", "content": DREAM_JUDGE_SYSTEM},
            {"role": "user", "content": prompt}
        ],
        json_mode=True
    )
    
    try:
        eval_data = json.loads(judge_res["content"])
    except Exception:
        eval_data = {
            "score": 0.5,
            "decision": "FAIL",
            "reason": "Failed to parse JSON response from LLM Judge."
        }
        
    eval_data["memory_md"] = memory_md
    eval_data["events_md"] = events_md
    eval_data["project_evals"] = project_evals
    eval_data["passed"] = eval_data["decision"] == "PASS"
    
    if eval_data["passed"]:
        print_success(f"Dream Synthesis evaluation passed! Score={eval_data['score']:.2f}")
    else:
        print_fail(f"Dream Synthesis evaluation failed! Score={eval_data['score']:.2f} -> {eval_data['reason']}")
        
    return eval_data


def run_lint_tests(harness: KBTestHarness) -> Dict[str, Any]:
    """Phase 5: Runs the weekly deterministic lint checks."""
    print_section("Running Phase 5: Weekly Lint Integrity Check")
    
    stats = lint.run(harness.db_session, harness.manager_id)
    print_info(f"Lint job finished. Stats: {stats}")
    
    passed = stats.get("issues_found", 0) == 0
    return {
        "passed": passed,
        "issues_found": stats.get("issues_found", 0),
        "stats": stats
    }


# ─── PYTEST HOOKS AND INTERFACES ───────────────────────────────────────────────

@pytest.fixture(scope="module")
def judge_harness():
    """Module-level pytest fixture to setup and teardown the LLM judge environment."""
    if not GEMINI_API_KEY:
        pytest.skip("Skipping LLM as Judge tests: GEMINI_API_KEY not set")
        
    harness = KBTestHarness()
    harness.setup()
    yield harness
    harness.teardown()


def test_01_classification(judge_harness):
    """Test message classification correctness."""
    results = run_classification_tests(judge_harness)
    failed = [r for r in results if not r["passed"]]
    assert len(failed) == 0, f"Some messages misclassified: {failed}"


def test_02_ingestion_claims(judge_harness):
    """Test claim extraction semantic accuracy using LLM judge."""
    results = run_ingestion_tests(judge_harness)
    failed = [r for r in results if not r["passed"]]
    assert len(failed) == 0, f"Claims validation failed: {failed}"


def test_03_heartbeat_events(judge_harness):
    """Test event category and severity generation accuracy using LLM judge."""
    results = run_heartbeat_tests(judge_harness)
    failed = [r for r in results if not r["passed"]]
    assert len(failed) == 0, f"Events validation failed: {failed}"


def test_04_dream_synthesis(judge_harness):
    """Test Executive daily synthesis using LLM judge."""
    results = run_dream_tests(judge_harness)
    assert results["passed"], f"Dream synthesis validation failed: {results['reason']}"


def test_05_lint_check(judge_harness):
    """Test integrity linting checks."""
    results = run_lint_tests(judge_harness)
    assert results["passed"], f"Lint integrity checks found issues: {results['issues_found']}"


# ─── STANDALONE RUNNER MAIN ENTRYPOINT ─────────────────────────────────────────

def generate_markdown_report(
    class_res: List[Dict],
    ingest_res: List[Dict],
    heartbeat_res: List[Dict],
    dream_res: Dict,
    lint_res: Dict
):
    """Generates a complete, gorgeous Markdown document summary of our evaluations."""
    report_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "kb_test_report.md"))
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Pulse.ai Knowledge Base Pipeline Test Report\n")
        f.write(f"*Generated on: {now_str} (IST)*\n\n")
        
        # Executive Summary
        total_tests = len(class_res) + len(ingest_res) + len(heartbeat_res) + 2
        passed_tests = (
            sum(1 for r in class_res if r["passed"]) +
            sum(1 for r in ingest_res if r["passed"]) +
            sum(1 for r in heartbeat_res if r["passed"]) +
            (1 if dream_res["passed"] else 0) +
            (1 if lint_res["passed"] else 0)
        )
        pass_ratio = passed_tests / total_tests
        
        status_badge = "🟢 PASS" if pass_ratio == 1.0 else "🟡 PARTIAL PASS" if pass_ratio > 0.8 else "🔴 FAIL"
        
        f.write(f"## Executive Summary\n")
        f.write(f"- **Overall Status:** {status_badge}\n")
        f.write(f"- **Test Passing Rate:** **{pass_ratio * 100:.1f}%** ({passed_tests}/{total_tests})\n")
        f.write(f"- **Judge Model:** `{SMART_MODEL}` & `{FLASH_MODEL}`\n\n")
        
        # Phase 1: Classification
        f.write(f"## Phase 1: Deterministic Message Classification\n")
        f.write(f"| Message ID | Thread Key | Expected | Actual | Status |\n")
        f.write(f"| --- | --- | --- | --- | --- |\n")
        for r in class_res:
            status = "✅ PASS" if r["passed"] else "❌ FAIL"
            f.write(f"| `{r['msg_id']}` | `{r['thread_id']}` | `{r['expected']}` | `{r['actual']}` | {status} |\n")
        f.write(f"\n")
        
        # Phase 2: Ingest Claims
        f.write(f"## Phase 2: Claims Ingestion Evaluation (LLM-as-a-Judge)\n")
        for r in ingest_res:
            status = "✅ PASS" if r["passed"] else "❌ FAIL"
            score_bar = "▓" * int(r["score"] * 10) + "░" * (10 - int(r["score"] * 10))
            f.write(f"### Thread: `{r['thread_id']}`\n")
            f.write(f"- **Decision:** {status}\n")
            f.write(f"- **Auditor Score:** `{r['score']:.2f}` (`{score_bar}`)\n")
            if r.get("recall_critique"):
                f.write(f"- **Recall Critique:** {r['recall_critique']}\n")
            if r.get("precision_critique"):
                f.write(f"- **Precision Critique:** {r['precision_critique']}\n")
            f.write(f"- **Auditor Verdict:** {r.get('reason', 'N/A')}\n\n")
            f.write(f"**Extracted Claims:**\n")
            if r["claims"]:
                for c in r["claims"]:
                    f.write(f"- *{c}*\n")
            else:
                f.write(f"- *(No claims extracted)*\n")
            f.write(f"\n---\n\n")
            
        # Phase 3: Heartbeat Events
        f.write(f"## Phase 3: Heartbeat Events Evaluation (LLM-as-a-Judge)\n")
        for r in heartbeat_res:
            status = "✅ PASS" if r["passed"] else "❌ FAIL"
            score_bar = "▓" * int(r["score"] * 10) + "░" * (10 - int(r["score"] * 10))
            f.write(f"### Thread: `{r['thread_id']}`\n")
            f.write(f"- **Decision:** {status}\n")
            f.write(f"- **Auditor Score:** `{r['score']:.2f}` (`{score_bar}`)\n")
            f.write(f"- **Verdict:** {r.get('reason', 'N/A')}\n\n")
            f.write(f"**Generated Timeline Events:**\n")
            if r["events"]:
                for e in r["events"]:
                    f.write(f"- **[{e['type'].upper()} - Severity {e['severity']}]** {e['title']}\n")
            else:
                f.write(f"- *(No events generated)*\n")
            f.write(f"\n---\n\n")
            
        # Phase 4: Dream Synthesis
        f.write(f"## Phase 4: Dream Executive Synthesis Evaluation\n")
        dream_status = "✅ PASS" if dream_res["passed"] else "❌ FAIL"
        dream_bar = "▓" * int(dream_res["score"] * 10) + "░" * (10 - int(dream_res["score"] * 10))
        f.write(f"- **Decision:** {dream_status}\n")
        f.write(f"- **Auditor Score:** `{dream_res['score']:.2f}` (`{dream_bar}`)\n")
        f.write(f"- **Recommendations Quality:** `{dream_res.get('suggestions_quality', 'High')}`\n")
        f.write(f"- **Auditor Verdict:** {dream_res.get('reason', 'N/A')}\n\n")
        
        f.write(f"### Project health and recommendations log:\n")
        for proj_name, p_info in dream_res["project_evals"].items():
            f.write(f"#### Project: {proj_name}\n")
            f.write(f"- **Base Health Score:** `{p_info['health']}`\n")
            f.write(f"- **Health Reason:** *{p_info['health_reason']}*\n")
            f.write(f"- **Suggestions:**\n")
            for s in p_info["suggestions"]:
                f.write(f"  - {s}\n")
            f.write(f"- **Concerns:**\n")
            for c in p_info["concerns"]:
                f.write(f"  - {c}\n")
            f.write(f"\n")
            
        f.write(f"\n---\n\n")
        
        # Phase 5: Weekly Lint Check
        f.write(f"## Phase 5: Lint Integrity Checks\n")
        lint_status = "✅ PASS" if lint_res["passed"] else "❌ FAIL"
        f.write(f"- **Decision:** {lint_status}\n")
        f.write(f"- **Issues Found:** `{lint_res['issues_found']}`\n")
        f.write(f"- **Full Stats:** {json.dumps(lint_res['stats'])}\n\n")
        
    print_info(f"Markdown report generated successfully at: {report_path}!")


if __name__ == "__main__":
    if not GEMINI_API_KEY:
        print_fail("GEMINI_API_KEY is not set in environment or config.yaml. Cannot execute standalone test runner.")
        sys.exit(1)
        
    print_section("Pulse.ai Knowledge Base Pipeline Test Runner")
    print_info("Spinning up temporary isolated testing databases...")
    
    harness = KBTestHarness()
    try:
        harness.setup()
        
        # 1. Classification Phase
        print_section("Phase 1: Deterministic Classifier Check")
        class_res = run_classification_tests(harness)
        
        # 2. Ingestion Claims Extraction Phase
        print_section("Phase 2: Claims Ingestion Job (LLM-as-a-Judge)")
        ingest_res = run_ingestion_tests(harness)
        
        # 3. Heartbeat Event Generation Phase
        print_section("Phase 3: Heartbeat Dashboard Events (LLM-as-a-Judge)")
        heartbeat_res = run_heartbeat_tests(harness)
        
        # 4. Dream Daily Executive Synthesis Phase
        print_section("Phase 4: Dream Daily Synthesis (LLM-as-a-Judge)")
        dream_res = run_dream_tests(harness)
        
        # 5. Lint Verification Phase
        print_section("Phase 5: Deterministic weekly Integrity Linting")
        lint_res = run_lint_tests(harness)
        
        # Create consolidated Markdown report
        print_section("Consolidating Evaluation Report")
        generate_markdown_report(class_res, ingest_res, heartbeat_res, dream_res, lint_res)
        
        print_section("All Phases Finished Successfully")
        print_success("Testing script execution complete. Report generated at tests/kb_test_report.md")
        
    except Exception as e:
        print_fail(f"Testing crashed with error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print_info("Tearing down temporary isolated testing environments...")
        harness.teardown()
