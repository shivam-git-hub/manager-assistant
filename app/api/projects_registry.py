"""Global projects registry + employees directory APIs (step 18 --
prompts/step_18_registry_and_scaffold.md, spec/architecture_v2_kb.md §3,
§7 item 1). Cookie-auth via the same app.controlplane.auth.get_current_manager
dependency every other app/api/ router uses; reads/writes the control-plane
db (app.controlplane.models) directly via get_controlplane_db, same pairing
app.controlplane.api uses for /connections.

Route note: this reuses the path /api/projects, which app/api/dashboard.py
used to serve for the old v1 per-manager Project table. That collision was
resolved by deleting the two v1 handlers there (see dashboard.py's comment)
-- grep found only tests/test_kb.py depending on them, not the simulator or
the old Vue dashboard's JS.
"""
import json
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.controlplane.auth import get_current_manager
from app.controlplane.models import (
    get_controlplane_db,
    Manager,
    Employee,
    Project as RegistryProject,
    ProjectMember,
)
from app.projects.paths import ensure_project_scaffold

router = APIRouter(prefix="/api", tags=["Projects Registry"])


# ────────────────────────────────────────────────────────
# Schemas
# ────────────────────────────────────────────────────────

class MemberInput(BaseModel):
    employee_id: str
    role: Optional[str] = None


class ProjectCreateIn(BaseModel):
    name: str
    description: Optional[str] = None
    kind: str  # "team" | "personal"
    member_employee_ids: Optional[List[MemberInput]] = None
    supervisors: Optional[List[str]] = None


class ProjectPatchIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    supervisors: Optional[List[str]] = None
    add_member_employee_ids: Optional[List[MemberInput]] = None
    remove_member_employee_ids: Optional[List[str]] = None


# ────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────

def _is_visible(db: DBSession, project: RegistryProject, manager: Manager) -> bool:
    if project.manager_user_id == manager.id:
        return True
    if project.kind == "personal":
        return False
    match = (
        db.query(ProjectMember)
        .join(Employee, ProjectMember.employee_id == Employee.id)
        .filter(ProjectMember.project_id == project.id)
        .filter(func.lower(Employee.email) == manager.email.lower())
        .first()
    )
    return match is not None


def _project_detail(db: DBSession, project: RegistryProject, manager: Manager) -> dict:
    rows = (
        db.query(ProjectMember, Employee)
        .join(Employee, ProjectMember.employee_id == Employee.id)
        .filter(ProjectMember.project_id == project.id)
        .all()
    )
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "kind": project.kind,
        "manager_user_id": project.manager_user_id,
        "is_manager": project.manager_user_id == manager.id,
        "supervisors": json.loads(project.supervisors) if project.supervisors else [],
        "members": [
            {"employee_id": e.id, "name": e.name, "email": e.email, "role": pm.role}
            for pm, e in rows
        ],
        "created_at": project.created_at,
    }


def _employee_dict(e: Employee) -> dict:
    return {
        "id": e.id,
        "email": e.email,
        "slack_id": e.slack_id,
        "name": e.name,
        "role": e.role,
        "skills": json.loads(e.skills) if e.skills else [],
    }


# ────────────────────────────────────────────────────────
# Employees directory
# ────────────────────────────────────────────────────────

@router.get("/employees")
def list_employees(
    manager: Manager = Depends(get_current_manager),
    db: DBSession = Depends(get_controlplane_db),
):
    employees = db.query(Employee).order_by(Employee.name).all()
    return [_employee_dict(e) for e in employees]


# ────────────────────────────────────────────────────────
# Projects registry
# ────────────────────────────────────────────────────────

@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreateIn,
    manager: Manager = Depends(get_current_manager),
    db: DBSession = Depends(get_controlplane_db),
):
    if payload.kind not in ("team", "personal"):
        raise HTTPException(status_code=400, detail="kind must be 'team' or 'personal'")

    if payload.kind == "personal" and payload.member_employee_ids:
        raise HTTPException(status_code=400, detail="personal projects cannot have members")

    for m in payload.member_employee_ids or []:
        if db.get(Employee, m.employee_id) is None:
            raise HTTPException(status_code=400, detail=f"Unknown employee_id: {m.employee_id}")

    project = RegistryProject(
        id=uuid.uuid4().hex,
        name=payload.name,
        description=payload.description,
        kind=payload.kind,
        manager_user_id=manager.id,
        supervisors=json.dumps(payload.supervisors) if payload.supervisors else None,
    )
    db.add(project)

    for m in payload.member_employee_ids or []:
        db.add(ProjectMember(id=uuid.uuid4().hex, project_id=project.id, employee_id=m.employee_id, role=m.role))

    db.commit()
    db.refresh(project)

    # Filesystem + project db.sqlite scaffold -- only after the registry
    # row/members are durably committed.
    ensure_project_scaffold(project.id, project.name, project.description)

    return _project_detail(db, project, manager)


@router.get("/projects")
def list_projects(
    manager: Manager = Depends(get_current_manager),
    db: DBSession = Depends(get_controlplane_db),
):
    owned = db.query(RegistryProject).filter(RegistryProject.manager_user_id == manager.id).all()
    owned_ids = {p.id for p in owned}

    member_project_ids = {
        row.project_id
        for row in (
            db.query(ProjectMember.project_id)
            .join(Employee, ProjectMember.employee_id == Employee.id)
            .filter(func.lower(Employee.email) == manager.email.lower())
            .all()
        )
    }
    member_only_ids = member_project_ids - owned_ids
    member_projects = []
    if member_only_ids:
        member_projects = (
            db.query(RegistryProject)
            .filter(RegistryProject.id.in_(member_only_ids), RegistryProject.kind == "team")
            .all()
        )

    result = []
    for p in owned + member_projects:
        member_count = db.query(ProjectMember).filter(ProjectMember.project_id == p.id).count()
        result.append({
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "kind": p.kind,
            "is_manager": p.manager_user_id == manager.id,
            "member_count": member_count,
        })
    return result


@router.get("/projects/{project_id}")
def get_project(
    project_id: str,
    manager: Manager = Depends(get_current_manager),
    db: DBSession = Depends(get_controlplane_db),
):
    project = db.get(RegistryProject, project_id)
    if project is None or not _is_visible(db, project, manager):
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_detail(db, project, manager)


@router.patch("/projects/{project_id}")
def patch_project(
    project_id: str,
    payload: ProjectPatchIn,
    manager: Manager = Depends(get_current_manager),
    db: DBSession = Depends(get_controlplane_db),
):
    project = db.get(RegistryProject, project_id)
    if project is None or not _is_visible(db, project, manager):
        raise HTTPException(status_code=404, detail="Project not found")
    if project.manager_user_id != manager.id:
        raise HTTPException(status_code=403, detail="Only the project manager can edit this project")

    if payload.name is not None:
        project.name = payload.name
    if payload.description is not None:
        project.description = payload.description
    if payload.supervisors is not None:
        project.supervisors = json.dumps(payload.supervisors)

    wants_member_change = bool(payload.add_member_employee_ids or payload.remove_member_employee_ids)
    if wants_member_change and project.kind != "team":
        raise HTTPException(status_code=400, detail="Only team projects support member changes")

    for m in payload.add_member_employee_ids or []:
        employee = db.get(Employee, m.employee_id)
        if employee is None:
            raise HTTPException(status_code=400, detail=f"Unknown employee_id: {m.employee_id}")
        existing = (
            db.query(ProjectMember)
            .filter(ProjectMember.project_id == project.id, ProjectMember.employee_id == m.employee_id)
            .first()
        )
        if existing:
            existing.role = m.role
        else:
            db.add(ProjectMember(id=uuid.uuid4().hex, project_id=project.id, employee_id=m.employee_id, role=m.role))

    for employee_id in payload.remove_member_employee_ids or []:
        db.query(ProjectMember).filter(
            ProjectMember.project_id == project.id, ProjectMember.employee_id == employee_id
        ).delete()

    db.commit()
    db.refresh(project)
    return _project_detail(db, project, manager)
