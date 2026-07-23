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
from typing import Dict, List, Optional

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
    Portfolio,
    PortfolioProject,
)
from app.database import Event
from app.projects.db import get_project_session
from app.projects.models import HealthLog
from app.projects.paths import ensure_project_scaffold, project_db_path
from app.tenancy.db import get_manager_db

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


def _health_band(score: int) -> str:
    """Bands a HealthLog.final_score into the frontend's green/yellow/red
    glyph -- same thresholds documented in
    app.projectkb.jobs.dream._compute_base_health_score's docstring
    (>=70 green, >=40 yellow, else red)."""
    if score >= 70:
        return "green"
    if score >= 40:
        return "yellow"
    return "red"


def _project_card_stats(db: DBSession, mdb: DBSession, projects: List[RegistryProject]) -> Dict[str, dict]:
    """health band + open blocker/clarification count + open request count
    per project, for the Home/Projects card flowers. Health lives in each
    project's OWN db.sqlite (HealthLog, written by the dream job); blocker/
    request counts live in the MANAGER's own db (Event.project_ids, written
    by heartbeat) -- same split every other insights read in this codebase
    follows (see project_detail.get_insights vs app/api/home.py)."""
    from app.api.home import RESOLVED_UI_STATES

    stats: Dict[str, dict] = {p.id: {"health": None, "blockers_count": 0, "actions_count": 0} for p in projects}

    events = (
        mdb.query(Event)
        .filter(Event.type.in_(["blocker", "clarification", "request"]))
        .filter(Event.ui_state.notin_(RESOLVED_UI_STATES))
        .all()
    )
    for e in events:
        event_project_ids = json.loads(e.project_ids) if e.project_ids else []
        for pid in event_project_ids:
            if pid not in stats:
                continue
            if e.type == "request":
                stats[pid]["actions_count"] += 1
            else:
                stats[pid]["blockers_count"] += 1

    for p in projects:
        if not project_db_path(p.id).exists():
            # A registry row with no scaffolded db.sqlite yet -- real code
            # paths always call ensure_project_scaffold at creation time
            # (see create_project below), but some test fixtures insert
            # RegistryProject rows directly. Same "no data yet" outcome as
            # a project the dream job simply hasn't reached.
            continue
        pdb = get_project_session(p.id)
        try:
            health = pdb.query(HealthLog).order_by(HealthLog.ts.desc()).first()
        finally:
            pdb.close()
        if health is not None:
            stats[p.id]["health"] = _health_band(health.final_score)

    return stats


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
    mdb: DBSession = Depends(get_manager_db),
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

    all_projects = owned + member_projects
    stats = _project_card_stats(db, mdb, all_projects)

    result = []
    for p in all_projects:
        member_count = db.query(ProjectMember).filter(ProjectMember.project_id == p.id).count()
        result.append({
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "kind": p.kind,
            "is_manager": p.manager_user_id == manager.id,
            "member_count": member_count,
            "health": stats[p.id]["health"],
            "blockers_count": stats[p.id]["blockers_count"],
            "actions_count": stats[p.id]["actions_count"],
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


# ────────────────────────────────────────────────────────
# Portfolios (wireframes 4.png, 8.png) -- a manager's personal grouping of
# their own projects. Real entity (named, independently deletable,
# projects added/removed one at a time), not a saved filter/view, per the
# step_27 prompt's explicit fork -- see Portfolio's model docstring.
# ────────────────────────────────────────────────────────

class PortfolioCreateIn(BaseModel):
    name: str


class PortfolioPatchIn(BaseModel):
    name: Optional[str] = None
    add_project_ids: Optional[List[str]] = None
    remove_project_ids: Optional[List[str]] = None


def _visible_project_ids(db: DBSession, manager: Manager) -> set:
    owned_ids = {
        p.id for p in db.query(RegistryProject).filter(RegistryProject.manager_user_id == manager.id).all()
    }
    member_ids = {
        row.project_id
        for row in (
            db.query(ProjectMember.project_id)
            .join(Employee, ProjectMember.employee_id == Employee.id)
            .filter(func.lower(Employee.email) == manager.email.lower())
            .all()
        )
    }
    return owned_ids | member_ids


def _portfolio_detail(db: DBSession, mdb: DBSession, portfolio: Portfolio, manager: Manager) -> dict:
    rows = (
        db.query(RegistryProject)
        .join(PortfolioProject, PortfolioProject.project_id == RegistryProject.id)
        .filter(PortfolioProject.portfolio_id == portfolio.id)
        .all()
    )
    stats = _project_card_stats(db, mdb, rows)
    projects = []
    for p in rows:
        member_count = db.query(ProjectMember).filter(ProjectMember.project_id == p.id).count()
        projects.append({
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "kind": p.kind,
            "is_manager": p.manager_user_id == manager.id,
            "member_count": member_count,
            "health": stats[p.id]["health"],
            "blockers_count": stats[p.id]["blockers_count"],
            "actions_count": stats[p.id]["actions_count"],
        })
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "created_at": portfolio.created_at,
        "projects": projects,
    }


@router.post("/portfolios", status_code=status.HTTP_201_CREATED)
def create_portfolio(
    payload: PortfolioCreateIn,
    manager: Manager = Depends(get_current_manager),
    db: DBSession = Depends(get_controlplane_db),
    mdb: DBSession = Depends(get_manager_db),
):
    portfolio = Portfolio(id=uuid.uuid4().hex, name=payload.name, manager_user_id=manager.id)
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return _portfolio_detail(db, mdb, portfolio, manager)


@router.get("/portfolios")
def list_portfolios(
    manager: Manager = Depends(get_current_manager),
    db: DBSession = Depends(get_controlplane_db),
    mdb: DBSession = Depends(get_manager_db),
):
    portfolios = (
        db.query(Portfolio)
        .filter(Portfolio.manager_user_id == manager.id)
        .order_by(Portfolio.created_at)
        .all()
    )
    return [_portfolio_detail(db, mdb, p, manager) for p in portfolios]


@router.get("/portfolios/{portfolio_id}")
def get_portfolio(
    portfolio_id: str,
    manager: Manager = Depends(get_current_manager),
    db: DBSession = Depends(get_controlplane_db),
    mdb: DBSession = Depends(get_manager_db),
):
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None or portfolio.manager_user_id != manager.id:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return _portfolio_detail(db, mdb, portfolio, manager)


@router.patch("/portfolios/{portfolio_id}")
def patch_portfolio(
    portfolio_id: str,
    payload: PortfolioPatchIn,
    manager: Manager = Depends(get_current_manager),
    db: DBSession = Depends(get_controlplane_db),
    mdb: DBSession = Depends(get_manager_db),
):
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None or portfolio.manager_user_id != manager.id:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    if payload.name is not None:
        portfolio.name = payload.name

    if payload.add_project_ids:
        visible_ids = _visible_project_ids(db, manager)
        for project_id in payload.add_project_ids:
            if project_id not in visible_ids:
                raise HTTPException(status_code=400, detail=f"Unknown or inaccessible project_id: {project_id}")
            existing = (
                db.query(PortfolioProject)
                .filter(PortfolioProject.portfolio_id == portfolio.id, PortfolioProject.project_id == project_id)
                .first()
            )
            if not existing:
                db.add(PortfolioProject(id=uuid.uuid4().hex, portfolio_id=portfolio.id, project_id=project_id))

    for project_id in payload.remove_project_ids or []:
        db.query(PortfolioProject).filter(
            PortfolioProject.portfolio_id == portfolio.id, PortfolioProject.project_id == project_id
        ).delete()

    db.commit()
    db.refresh(portfolio)
    return _portfolio_detail(db, mdb, portfolio, manager)


@router.delete("/portfolios/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(
    portfolio_id: str,
    manager: Manager = Depends(get_current_manager),
    db: DBSession = Depends(get_controlplane_db),
):
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None or portfolio.manager_user_id != manager.id:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    db.query(PortfolioProject).filter(PortfolioProject.portfolio_id == portfolio.id).delete()
    db.delete(portfolio)
    db.commit()
