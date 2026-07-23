from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import os
import asyncio
import sys

from app.config import PORT, HOST
from app.integrations import slack, outlook
from app.api import dashboard, team as team_api, projects_registry as projects_registry_api, home as home_api, project_detail as project_detail_api, dev_tools as dev_tools_api
from app import timeservice, outbound, scheduler, followups, brief
from app.kb import api as kb_api
from app.kb import meetings as meetings_api
from app.kb import workload as workload_api
from app.agent import api as agent_api
from app.projectkb import scheduler as projectkb_scheduler
from app.projectkb import api as projectkb_api
from app.controlplane import api as auth_api
from app.controlplane import outlook_auth
from app.controlplane import slack_auth
from app.controlplane import agents as agents_api
from app.controlplane.models import init_controlplane_db, SessionLocal as ControlPlaneSessionLocal, Project as RegistryProject
from app.tenancy.db import list_provisioned_manager_ids
from app.tenancy.paths import ensure_manager_scaffold
from app.projects.db import init_project_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Control-plane DB (identity/sessions/installations) is the one global
    # piece of state -- initialized once here, same as before step 15.
    init_controlplane_db()

    # Per-manager db.sqlite files are created at provisioning time (dev-login,
    # Outlook sign-in), not at app boot -- but boot still needs to run this
    # step's idempotent migration checks (schema create_all + ALTER-TABLE
    # backfills) against every manager who already exists, since a schema
    # change now has to be swept across N manager DBs instead of one. See
    # app.tenancy.db.init_manager_db.
    for manager_id in list_provisioned_manager_ids():
        ensure_manager_scaffold(manager_id)

    # Same reasoning, one level over: each registry project has its own
    # db.sqlite (app.projects.db), created at project-creation time only --
    # a schema change added after a project already existed (e.g.
    # Task.priority, step 21) never reached it since nothing re-ran
    # init_project_db for pre-existing projects. Discovered live 2026-07-23
    # (task creation 500ing on an old project with "no column named
    # priority"). Sweep every registry project at boot, mirroring the
    # manager-db sweep above.
    db = ControlPlaneSessionLocal()
    try:
        project_ids = [p.id for p in db.query(RegistryProject.id).all()]
    finally:
        db.close()
    for project_id in project_ids:
        init_project_db(project_id)

    if "pytest" not in sys.modules:
        # projectkb job scheduler: real wall-clock cadence (ingestion/heartbeat/
        # dream/lint). The product runs on real time only -- the old sim-time
        # tick loop and its scheduler.tick() wiring have been removed; sim
        # time is a simulator/testing concern, not something production code
        # depends on. As of step 15 it iterates every provisioned manager's
        # own db.sqlite -- there's no single shared session to hand it anymore.
        projectkb_task = asyncio.create_task(projectkb_scheduler.background_loop())

        yield

        # Clean shutdown
        projectkb_task.cancel()
        try:
            await projectkb_task
        except Exception:
            pass
    else:
        yield

app = FastAPI(
    title="Manager Assistant Integration Portal",
    description="Backend services for tracking team status across Slack, Outlook, and Dashboard.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth_api.router)
app.include_router(outlook_auth.router)
app.include_router(slack_auth.router)
app.include_router(agents_api.router)
app.include_router(timeservice.router)
app.include_router(team_api.router)
app.include_router(slack.router)
app.include_router(outlook.router)
app.include_router(dashboard.router)
app.include_router(outbound.router)
app.include_router(kb_api.router)
app.include_router(scheduler.router)
app.include_router(followups.router)
app.include_router(brief.router)
app.include_router(agent_api.router)
app.include_router(agent_api.heartbeat_router)
app.include_router(meetings_api.router)
app.include_router(workload_api.router)
app.include_router(projectkb_api.router)
app.include_router(projects_registry_api.router)
app.include_router(home_api.router)
app.include_router(project_detail_api.router)
app.include_router(dev_tools_api.router)

# Ensure static files directory exists
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# Mount static files to serve the Simulator UI
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": f"Internal Server Error: {str(exc)}"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
