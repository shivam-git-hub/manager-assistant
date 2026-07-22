"""Shared "which of this manager's OWNED projects does each event touch"
grouping -- manager-only project truth (spec §4.3) applies identically to
heartbeat's project fan-out (step 24) and dream's project-level synthesis
(step 25), so this lives in one place rather than twice."""
import json
from typing import Dict, List

from app.database import Event


def manager_owned_projects_with_events(manager_id: str, events: List[Event]) -> Dict[str, List[Event]]:
    """Groups events by project_id (an event tagged to N projects appears
    under all N), then keeps only the projects this manager actually
    manages. Jobs run outside any FastAPI request, so this opens its own
    short-lived control-plane session rather than borrowing a
    Depends(get_controlplane_db)."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Project as RegistryProject

    events_by_project: Dict[str, List[Event]] = {}
    for event in events:
        for project_id in json.loads(event.project_ids) if event.project_ids else []:
            events_by_project.setdefault(project_id, []).append(event)

    if not events_by_project:
        return {}

    cdb = ControlPlaneSessionLocal()
    try:
        managed_project_ids = {
            p.id
            for p in cdb.query(RegistryProject)
            .filter(RegistryProject.id.in_(events_by_project.keys()), RegistryProject.manager_user_id == manager_id)
            .all()
        }
    finally:
        cdb.close()

    return {pid: evs for pid, evs in events_by_project.items() if pid in managed_project_ids}
