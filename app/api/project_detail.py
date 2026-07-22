"""Project drill-down APIs (step 21 -- prompts/step_21_project_drilldown.md,
wireframes 6/7): per-project tasks, doc/notes markdown, vault files,
insights bundle, and project deletion. Visibility follows the registry
rule (manager or member-by-email); writes are manager-only.

Sim-time rule: schedule_state ("Overdue: N days") is computed against
app.timeservice, never the wall clock.
"""
import logging
import shutil
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from app import timeservice
from app.api.projects_registry import _is_visible
from app.controlplane.auth import get_current_manager
from app.controlplane.models import (
    get_controlplane_db,
    Employee,
    Manager,
    Project as RegistryProject,
    ProjectMember,
)
from app.projects.db import get_project_session
from app.projects.models import Concern, Conflict, HealthLog, Suggestion, Task
from app.projects.paths import (
    notes_md_path,
    project_dir,
    project_md_path,
    vault_dir,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["Project Detail"])

MAX_VAULT_FILE_BYTES = 25 * 1024 * 1024

TASK_STATUSES = ("todo", "in_progress", "blocked", "done", "pending_approval")
TASK_PRIORITIES = ("low", "medium", "high")


# ────────────────────────────────────────────────────────
# Access helpers
# ────────────────────────────────────────────────────────

def _get_visible_project(
    project_id: str, manager: Manager, db: DBSession
) -> RegistryProject:
    project = db.get(RegistryProject, project_id)
    if project is None or not _is_visible(db, project, manager):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _require_manager(project: RegistryProject, manager: Manager) -> None:
    if project.manager_user_id != manager.id:
        raise HTTPException(status_code=403, detail="Only the project manager can do this")


def _my_employee_ids(db: DBSession, manager: Manager) -> set:
    rows = db.query(Employee).all()
    email = manager.email.lower()
    return {e.id for e in rows if (e.email or "").lower() == email}


# ────────────────────────────────────────────────────────
# Tasks
# ────────────────────────────────────────────────────────

class TaskCreateIn(BaseModel):
    title: str
    description: Optional[str] = None
    priority: Optional[str] = None
    assignee_employee_id: Optional[str] = None
    due: Optional[datetime] = None
    parent_task_id: Optional[str] = None


class TaskPatchIn(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    assignee_employee_id: Optional[str] = None
    due: Optional[datetime] = None
    status: Optional[str] = None


def _schedule_state(task: Task, now: datetime) -> dict:
    """The status chip (wireframe 6): explicit states win; otherwise the
    due date against SIM time decides overdue/on-schedule."""
    if task.status == "done":
        return {"schedule_state": "done", "days_overdue": 0}
    if task.status == "blocked":
        return {"schedule_state": "blocked", "days_overdue": 0}
    if task.status == "pending_approval":
        return {"schedule_state": "pending_approval", "days_overdue": 0}
    if task.due and task.due < now:
        return {"schedule_state": "overdue", "days_overdue": max(1, (now - task.due).days)}
    return {"schedule_state": "on_schedule", "days_overdue": 0}


def _task_dict(task: Task, employees_by_id: dict, now: datetime) -> dict:
    assignee = employees_by_id.get(task.assignee_employee_id)
    return {
        "id": task.id,
        "parent_task_id": task.parent_task_id,
        "title": task.title,
        "description": task.description,
        "priority": task.priority,
        "assignee_employee_id": task.assignee_employee_id,
        "assignee_name": assignee.name if assignee else None,
        "status": task.status,
        "due": task.due.isoformat() if task.due else None,
        "created_by": task.created_by,
        "approved_by": task.approved_by,
        **_schedule_state(task, now),
        "subtasks": [],
    }


@router.get("/tasks")
def list_tasks(
    project_id: str,
    manager: Manager = Depends(get_current_manager),
    cp_db: DBSession = Depends(get_controlplane_db),
) -> dict:
    _get_visible_project(project_id, manager, cp_db)
    employees_by_id = {e.id: e for e in cp_db.query(Employee).all()}
    mine = _my_employee_ids(cp_db, manager)
    now = timeservice.now_ist()

    pdb = get_project_session(project_id)
    try:
        rows = pdb.query(Task).order_by(Task.created_at).all()
    finally:
        pdb.close()

    by_id = {t.id: _task_dict(t, employees_by_id, now) for t in rows}
    top_level: List[dict] = []
    for t in rows:
        d = by_id[t.id]
        if t.parent_task_id and t.parent_task_id in by_id:
            by_id[t.parent_task_id]["subtasks"].append(d)
        else:
            top_level.append(d)

    my_tasks = [by_id[t.id] for t in rows if t.assignee_employee_id in mine]
    return {"tasks": top_level, "my_tasks": my_tasks}


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
def create_task(
    project_id: str,
    payload: TaskCreateIn,
    manager: Manager = Depends(get_current_manager),
    cp_db: DBSession = Depends(get_controlplane_db),
) -> dict:
    project = _get_visible_project(project_id, manager, cp_db)
    _require_manager(project, manager)
    if payload.priority is not None and payload.priority not in TASK_PRIORITIES:
        raise HTTPException(status_code=400, detail=f"priority must be one of {TASK_PRIORITIES}")

    pdb = get_project_session(project_id)
    try:
        if payload.parent_task_id and pdb.get(Task, payload.parent_task_id) is None:
            raise HTTPException(status_code=400, detail="Unknown parent_task_id")
        task = Task(
            id=uuid.uuid4().hex,
            parent_task_id=payload.parent_task_id,
            title=payload.title,
            description=payload.description,
            priority=payload.priority or "medium",
            assignee_employee_id=payload.assignee_employee_id,
            due=payload.due,
            created_by="manager",
        )
        pdb.add(task)
        pdb.commit()
        pdb.refresh(task)
        employees_by_id = {e.id: e for e in cp_db.query(Employee).all()}
        return _task_dict(task, employees_by_id, timeservice.now_ist())
    finally:
        pdb.close()


@router.patch("/tasks/{task_id}")
def patch_task(
    project_id: str,
    task_id: str,
    payload: TaskPatchIn,
    manager: Manager = Depends(get_current_manager),
    cp_db: DBSession = Depends(get_controlplane_db),
) -> dict:
    project = _get_visible_project(project_id, manager, cp_db)
    _require_manager(project, manager)
    if payload.status is not None and payload.status not in TASK_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {TASK_STATUSES}")
    if payload.priority is not None and payload.priority not in TASK_PRIORITIES:
        raise HTTPException(status_code=400, detail=f"priority must be one of {TASK_PRIORITIES}")

    pdb = get_project_session(project_id)
    try:
        task = pdb.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        approving = task.status == "pending_approval" and payload.status not in (None, "pending_approval")
        for field in ("title", "description", "priority", "assignee_employee_id", "due", "status"):
            value = getattr(payload, field)
            if value is not None:
                setattr(task, field, value)
        if approving:
            task.approved_by = manager.id
        task.updated_at = timeservice.now_ist()
        pdb.commit()
        pdb.refresh(task)
        employees_by_id = {e.id: e for e in cp_db.query(Employee).all()}
        return _task_dict(task, employees_by_id, timeservice.now_ist())
    finally:
        pdb.close()


# ────────────────────────────────────────────────────────
# Doc / notes markdown
# ────────────────────────────────────────────────────────

class DocPutIn(BaseModel):
    project_md: Optional[str] = None
    notes_md: Optional[str] = None


@router.get("/doc")
def get_doc(
    project_id: str,
    manager: Manager = Depends(get_current_manager),
    cp_db: DBSession = Depends(get_controlplane_db),
) -> dict:
    _get_visible_project(project_id, manager, cp_db)
    pmd = project_md_path(project_id)
    nmd = notes_md_path(project_id)
    return {
        "project_md": pmd.read_text(encoding="utf-8") if pmd.exists() else "",
        "notes_md": nmd.read_text(encoding="utf-8") if nmd.exists() else "",
    }


@router.put("/doc")
def put_doc(
    project_id: str,
    payload: DocPutIn,
    manager: Manager = Depends(get_current_manager),
    cp_db: DBSession = Depends(get_controlplane_db),
) -> dict:
    project = _get_visible_project(project_id, manager, cp_db)
    _require_manager(project, manager)
    if payload.project_md is not None:
        project_md_path(project_id).write_text(payload.project_md, encoding="utf-8")
    if payload.notes_md is not None:
        notes_md_path(project_id).write_text(payload.notes_md, encoding="utf-8")
    return get_doc(project_id, manager, cp_db)


# ────────────────────────────────────────────────────────
# Vault
# ────────────────────────────────────────────────────────

def _safe_vault_name(filename: str) -> str:
    """Basename-only; anything that could traverse is rejected outright."""
    name = (filename or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", "..") or name.startswith(".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return name


@router.get("/vault")
def list_vault(
    project_id: str,
    manager: Manager = Depends(get_current_manager),
    cp_db: DBSession = Depends(get_controlplane_db),
) -> dict:
    _get_visible_project(project_id, manager, cp_db)
    vdir = vault_dir(project_id)
    files = []
    if vdir.exists():
        for p in sorted(vdir.iterdir()):
            if p.is_file():
                files.append({"name": p.name, "size": p.stat().st_size})
    return {"files": files}


@router.post("/vault", status_code=status.HTTP_201_CREATED)
async def upload_vault_file(
    project_id: str,
    file: UploadFile,
    manager: Manager = Depends(get_current_manager),
    cp_db: DBSession = Depends(get_controlplane_db),
) -> dict:
    """Members can dump files (spec §3: teammates/managers/agents use the
    vault; agents don't READ it in v2)."""
    _get_visible_project(project_id, manager, cp_db)
    name = _safe_vault_name(file.filename)
    content = await file.read()
    if len(content) > MAX_VAULT_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (25 MB max)")
    vdir = vault_dir(project_id)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / name).write_bytes(content)
    return {"name": name, "size": len(content)}


@router.get("/vault/{filename}")
def download_vault_file(
    project_id: str,
    filename: str,
    manager: Manager = Depends(get_current_manager),
    cp_db: DBSession = Depends(get_controlplane_db),
):
    _get_visible_project(project_id, manager, cp_db)
    name = _safe_vault_name(filename)
    path = vault_dir(project_id) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such file")
    return FileResponse(path, filename=name)


# ────────────────────────────────────────────────────────
# Insights bundle (pipeline-populated later; honest-empty today)
# ────────────────────────────────────────────────────────

@router.get("/insights")
def get_insights(
    project_id: str,
    manager: Manager = Depends(get_current_manager),
    cp_db: DBSession = Depends(get_controlplane_db),
) -> dict:
    _get_visible_project(project_id, manager, cp_db)
    pdb = get_project_session(project_id)
    try:
        conflicts = pdb.query(Conflict).filter(Conflict.status == "open").all()
        suggestions = pdb.query(Suggestion).filter(Suggestion.status == "open").all()
        concerns = pdb.query(Concern).filter(Concern.status == "open").all()
        health = pdb.query(HealthLog).order_by(HealthLog.ts.desc()).first()
    finally:
        pdb.close()
    return {
        "conflicts": [
            {"id": c.id, "claim_a_ref": c.claim_a_ref, "claim_b_ref": c.claim_b_ref, "severity": c.severity}
            for c in conflicts
        ],
        "suggestions": [{"id": s.id, "text": s.text} for s in suggestions],
        "concerns": [{"id": c.id, "text": c.text} for c in concerns],
        "health": (
            {"final_score": health.final_score, "base_score": health.base_score, "reason": health.reason}
            if health
            else None
        ),
    }


# ────────────────────────────────────────────────────────
# Delete project (wireframe 6)
# ────────────────────────────────────────────────────────

@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    manager: Manager = Depends(get_current_manager),
    cp_db: DBSession = Depends(get_controlplane_db),
) -> None:
    project = _get_visible_project(project_id, manager, cp_db)
    _require_manager(project, manager)
    cp_db.query(ProjectMember).filter(ProjectMember.project_id == project_id).delete()
    cp_db.delete(project)
    cp_db.commit()
    shutil.rmtree(project_dir(project_id), ignore_errors=True)
    logger.info(f"[projects] manager={manager.id} deleted project={project_id}")
