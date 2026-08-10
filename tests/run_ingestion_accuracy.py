"""Standalone accuracy check for the ingestion job only (classify_message +
claim extraction), run against real Gemini for the first N threads of
tests/kb_test_dataset.json. Not a pytest test -- run directly:

    .venv/bin/python3 tests/run_ingestion_accuracy.py [N]

Prints a per-thread PASS/FAIL (via LLM-as-a-judge on extracted claims vs.
the dataset's gold `expected_claims`) and an overall accuracy summary.
"""
import os
import sys
import json
import uuid
import shutil
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import timeservice
from app.config import SMART_MODEL, GEMINI_API_KEY
from app.database import UnifiedMessage, Claim
from app.agent.gemini_client import get_client
from app.projectkb.jobs import ingestion
from app.projectkb import blocklist
from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    Employee,
    init_controlplane_db,
)
from sqlalchemy import select

DATASET_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "kb_test_dataset.json"))

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
    '  "recall_critique": "...",\n'
    '  "precision_critique": "...",\n'
    '  "reason": "..."\n'
    "}\n\n"
    "Rule for PASS: score >= 0.8. High semantic similarity in business value passes even if phrasing differs."
)


def main():
    n_threads = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY not set -- cannot run.")
        sys.exit(1)

    os.environ["CONTROLPLANE_DB_FILENAME"] = "test_controlplane_ingest_accuracy.sqlite"
    init_controlplane_db()

    manager_id = None
    try:
        cdb = ControlPlaneSessionLocal()
        emp = Employee(
            id=uuid.uuid4().hex,
            email="ingest-accuracy@test.local",
            name="Ingest Accuracy Tester",
            is_manager=True,
            created_at=timeservice.now_ist(),
        )
        cdb.add(emp)
        cdb.commit()
        manager_id = emp.id
        cdb.close()

        from app.tenancy.db import init_manager_db, get_manager_session
        from app.tenancy.paths import manager_dir

        init_manager_db(manager_id)
        db = get_manager_session(manager_id)

        with open(DATASET_PATH, "r", encoding="utf-8") as f:
            dataset = json.load(f)[:n_threads]

        print(f"Loaded {len(dataset)} threads from {DATASET_PATH}")

        for thread in dataset:
            for msg_data in thread["messages"]:
                um = UnifiedMessage(
                    platform_msg_id=msg_data["platform_msg_id"],
                    source=thread["source"],
                    sender_raw_id=msg_data["sender_raw_id"],
                    sender_mapped_name=msg_data["sender_mapped_name"],
                    receiver_raw_id=msg_data["receiver_raw_id"],
                    receiver_mapped_name=msg_data["receiver_mapped_name"],
                    channel_raw_id=thread["channel_raw_id"],
                    thread_id=thread["thread_id"],
                    subject=thread["subject"],
                    content=msg_data["content"],
                    timestamp=datetime.strptime(msg_data["timestamp"], "%Y-%m-%d %H:%M:%S"),
                    created_at=timeservice.now_ist(),
                    is_processed=False,
                    raw_metadata=msg_data["raw_metadata"],
                )
                db.add(um)
            db.commit()

        client = get_client()
        stats = ingestion.run(db, manager_id, client=client)
        print(f"Ingestion job stats: {stats}")

        results = []
        for thread in dataset:
            thread_id = thread["thread_id"]
            expected_class = thread["messages"][0]["expected_classification"]

            db_claims = db.scalars(select(Claim).where(Claim.thread_key == thread_id)).all()
            actual_claims_text = [c.text for c in db_claims]

            if expected_class in ("blocked", "noise"):
                passed = len(db_claims) == 0
                results.append({"thread_id": thread_id, "type": "skipped", "passed": passed, "score": 1.0 if passed else 0.0})
                status = "PASS" if passed else "FAIL"
                print(f"[{status}] {thread_id} (expected no claims, got {len(db_claims)})")
                continue

            prompt = (
                "### Original Thread:\n"
                + "\n".join(f"{m['sender_mapped_name']}: {m['content']}" for m in thread["messages"])
                + "\n\n### Expected Reference Claims:\n"
                + "\n".join(f"- {c}" for c in thread["expected_claims"])
                + "\n\n### System Extracted Claims:\n"
                + ("\n".join(f"- {c}" for c in actual_claims_text) if actual_claims_text else "(No claims extracted)")
            )

            judge_res = client.chat(
                model=SMART_MODEL,
                messages=[
                    {"role": "system", "content": CLAIMS_JUDGE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                json_mode=True,
            )
            try:
                eval_data = json.loads(judge_res["content"])
            except Exception:
                eval_data = {"score": 0.0, "decision": "FAIL", "reason": "Judge JSON parse failure"}

            eval_data["thread_id"] = thread_id
            eval_data["passed"] = eval_data["decision"] == "PASS"
            results.append(eval_data)

            status = "PASS" if eval_data["passed"] else "FAIL"
            print(f"[{status}] {thread_id} score={eval_data.get('score')} -> {eval_data.get('reason')}")

        passed_n = sum(1 for r in results if r["passed"])
        print(f"\nAccuracy: {passed_n}/{len(results)} ({passed_n/len(results)*100:.1f}%)")

    finally:
        try:
            db.close()
        except Exception:
            pass
        from app.controlplane.models import CONTROLPLANE_DB_PATH, engine
        engine.dispose()
        if CONTROLPLANE_DB_PATH.exists():
            try:
                CONTROLPLANE_DB_PATH.unlink()
            except OSError:
                pass
        if manager_id:
            from app.tenancy.paths import manager_dir
            shutil.rmtree(manager_dir(manager_id), ignore_errors=True)


if __name__ == "__main__":
    main()
