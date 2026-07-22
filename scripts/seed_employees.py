"""Admin, one-time (re-runnable): loads employees.json (gitignored -- see
employees.json.example) and upserts each entry into the control-plane
Employee table by lowercase email (step 18 --
prompts/step_18_registry_and_scaffold.md). Mirrors scripts/seed_agents.py's
pattern. Manual admin process for now; Graph directory sync replaces this
later (spec/architecture_v2_kb.md §3).

Usage: .venv/bin/python3 -m scripts.seed_employees [path/to/employees.json]
"""
import json
import sys
import uuid
from pathlib import Path

from app.config import BASE_DIR
from app.controlplane.models import SessionLocal, Employee, init_controlplane_db


def seed(path: Path) -> None:
    init_controlplane_db()

    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)

    db = SessionLocal()
    try:
        for entry in entries:
            email = entry["email"].strip().lower()
            employee = db.query(Employee).filter(Employee.email == email).first()
            if employee is None:
                employee = Employee(id=uuid.uuid4().hex, email=email)
                db.add(employee)
            employee.name = entry["name"]
            employee.slack_id = entry.get("slack_id")
            employee.role = entry.get("role")
            employee.skills = json.dumps(entry["skills"]) if entry.get("skills") else None
            print(f"seeded employee: {email} ({entry['name']})")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "employees.json"
    seed(BASE_DIR / arg if not Path(arg).is_absolute() else Path(arg))
