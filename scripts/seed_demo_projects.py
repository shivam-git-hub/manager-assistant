"""One-off (re-runnable-ish, but not idempotent) demo data seeder: 6 team
projects + 3 personal tasks for the real manager (Shivam Kanojia,
shivamk.iitd@outlook.com). Two projects ("AI chief of Staff", "Pulse.ai
Frontend Rebuild") are demo-ready: rich project.md/notes.md/summary.md/
events.md, tasks+subtasks across every status/priority, archive entries,
suggestions/concerns, a health_log row, and both real employees (Shivam
Kanojia, Ally K) as members alongside fake teammates. The other four
projects get lighter but real data (description, 2-3 members, a few
tasks). Not part of the app's own boot/test path -- run by hand:

    .venv/bin/python3 -m scripts.seed_demo_projects
"""
import uuid
from datetime import timedelta

from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    Employee,
    Manager,
    Project as RegistryProject,
    ProjectMember,
    init_controlplane_db,
)
from app.projects.db import get_project_session
from app.projects.models import ArchiveEntry, Concern, HealthLog, Suggestion, Task
from app.projects.paths import (
    ensure_project_scaffold,
    events_md_path,
    notes_md_path,
    project_md_path,
    summary_md_path,
)
from app import timeservice

MANAGER_EMAIL = "shivamk.iitd@outlook.com"


def _get_manager(db) -> Manager:
    m = db.query(Manager).filter(Manager.email == MANAGER_EMAIL).first()
    if m is None:
        raise SystemExit(f"manager {MANAGER_EMAIL} not found -- log in once first")
    return m


def _employee_id(db, email: str) -> str:
    e = db.query(Employee).filter(Employee.email == email).first()
    if e is None:
        raise SystemExit(f"employee {email} not found -- run scripts/seed_employees.py first")
    return e.id


def _create_project(db, manager, name, description, member_emails, employee_ids):
    project = RegistryProject(
        id=uuid.uuid4().hex,
        name=name,
        description=description,
        kind="team",
        manager_user_id=manager.id,
    )
    db.add(project)
    db.flush()
    for email in member_emails:
        db.add(ProjectMember(
            id=uuid.uuid4().hex,
            project_id=project.id,
            employee_id=employee_ids[email],
            role=None,
        ))
    db.commit()
    db.refresh(project)
    ensure_project_scaffold(project.id, project.name, project.description)
    print(f"created project: {name} ({project.id})")
    return project


def _add_task(pdb, title, status, priority, assignee_id, description=None, due_days=None, created_by="manager", parent_id=None):
    now = timeservice.now_ist()
    task = Task(
        id=uuid.uuid4().hex,
        parent_task_id=parent_id,
        title=title,
        description=description,
        assignee_employee_id=assignee_id,
        status=status,
        priority=priority,
        due=(now + timedelta(days=due_days)) if due_days is not None else None,
        created_by=created_by,
        approved_by=None,
    )
    pdb.add(task)
    pdb.commit()
    pdb.refresh(task)
    return task


def main():
    init_controlplane_db()
    db = ControlPlaneSessionLocal()
    try:
        manager = _get_manager(db)

        emails = [
            "shivamk.iitd@outlook.com",
            "realityescapesk@outlook.com",
            "alice@example.com",
            "bob@example.com",
            "carol@example.com",
            "dave@example.com",
        ]
        employee_ids = {email: _employee_id(db, email) for email in emails}
        alice, bob, carol, dave = (employee_ids[e] for e in emails[2:])
        shivam_emp, ally_emp = employee_ids["shivamk.iitd@outlook.com"], employee_ids["realityescapesk@outlook.com"]

        # ── existing project: enrich into demo-ready #1 ──
        existing = db.query(RegistryProject).filter(RegistryProject.name == "AI chief of Staff").first()
        if existing is None:
            existing = _create_project(
                db, manager, "AI chief of Staff",
                "Pulse.ai itself -- the manager-assistant product Shivam is building for himself.",
                ["realityescapesk@outlook.com", "alice@example.com", "bob@example.com"], employee_ids,
            )
        else:
            current_member_emails = {
                e.email for pm, e in db.query(ProjectMember, Employee)
                .join(Employee, ProjectMember.employee_id == Employee.id)
                .filter(ProjectMember.project_id == existing.id).all()
            }
            for email in ["realityescapesk@outlook.com", "alice@example.com", "bob@example.com"]:
                if email not in current_member_emails:
                    db.add(ProjectMember(id=uuid.uuid4().hex, project_id=existing.id, employee_id=employee_ids[email], role=None))
            db.commit()
            ensure_project_scaffold(existing.id, existing.name, existing.description)
            print(f"reused existing project: AI chief of Staff ({existing.id})")
        aicos = existing

        # ── demo-ready #2 ──
        pulse = _create_project(
            db, manager, "Pulse.ai Frontend Rebuild",
            "React + Vite rebuild of the manager-facing dashboard, page by page against Shivam's wireframes.",
            ["realityescapesk@outlook.com", "carol@example.com", "bob@example.com"], employee_ids,
        )

        # ── lighter projects ──
        market = _create_project(
            db, manager, "Q3 Marketing Campaign",
            "Cross-channel push for the Q3 product launch -- website, socials, and a partner webinar.",
            ["carol@example.com", "dave@example.com"], employee_ids,
        )
        mobile = _create_project(
            db, manager, "Mobile App Redesign",
            "Visual + navigation overhaul of the companion mobile app, aligning it with the new web UI.",
            ["bob@example.com", "carol@example.com"], employee_ids,
        )
        onboarding = _create_project(
            db, manager, "Customer Onboarding Revamp",
            "Cut new-customer time-to-value by simplifying the first-run setup flow.",
            ["alice@example.com", "dave@example.com"], employee_ids,
        )
        infra = _create_project(
            db, manager, "Infra Migration to Kubernetes",
            "Move the staging + prod fleet off single VMs onto a managed Kubernetes cluster.",
            ["alice@example.com", "bob@example.com"], employee_ids,
        )

        # ── personal tasks (kind=personal, no members) ──
        for title, desc in [
            ("Renew AWS certificate", "SSL cert for api.pulse.ai expires end of month -- renew via ACM."),
            ("Prepare Q3 board deck", "Pull metrics from the dream job's health logs across all projects."),
            ("Review Ally's 1:1 notes", "Catch up before Thursday's sync."),
        ]:
            p = RegistryProject(
                id=uuid.uuid4().hex, name=title, description=desc,
                kind="personal", manager_user_id=manager.id,
            )
            db.add(p)
            db.commit()
            db.refresh(p)
            ensure_project_scaffold(p.id, p.name, p.description)
            print(f"created personal task: {title} ({p.id})")

        ids = {
            "aicos": aicos.id, "aicos_name": aicos.name,
            "pulse": pulse.id, "pulse_name": pulse.name,
            "market": market.id, "market_name": market.name,
            "mobile": mobile.id, "mobile_name": mobile.name,
            "onboarding": onboarding.id, "onboarding_name": onboarding.name,
            "infra": infra.id, "infra_name": infra.name,
        }
    finally:
        db.close()

    class _Ref:
        def __init__(self, id_, name):
            self.id = id_
            self.name = name

    aicos = _Ref(ids["aicos"], ids["aicos_name"])
    pulse = _Ref(ids["pulse"], ids["pulse_name"])
    market = _Ref(ids["market"], ids["market_name"])
    mobile = _Ref(ids["mobile"], ids["mobile_name"])
    onboarding = _Ref(ids["onboarding"], ids["onboarding_name"])
    infra = _Ref(ids["infra"], ids["infra_name"])

    # ── per-project md files + tasks/archive/health ──

    now = timeservice.now_ist()

    # AI chief of Staff -- demo-ready, rich
    project_md_path(aicos.id).write_text(f"""# {aicos.name}

Pulse.ai is the manager-assistant product itself: connects to Slack +
Outlook, maintains a knowledge base from every message, and acts
autonomously (follow-ups, deadlock detection, status inference, manager
updates).

## Overview

Building the v2 architecture end to end -- ingest/heartbeat/dream job
pipeline, per-project truth stores, and a rebuilt React frontend. Currently
in the frontend-rebuild phase, page by page against wireframes.

## Milestones

- [x] Control-plane registry + per-project scaffold (step 18)
- [x] Ingest + heartbeat + dream jobs (steps 22-25)
- [x] Home, Projects, Portfolios, Tasks, Connectors, Agents pages
- [ ] Lint job (step 26)
- [ ] Remaining frontend gaps: blocklist settings UI, workload view

## KPIs

- Demo-readiness: 8-minute storyline, all beats working end to end
- Test suite: 269 passing, 3 known pre-existing failures (v1 heartbeat)
""", encoding="utf-8")
    notes_md_path(aicos.id).write_text("""# Notes

- Spec-first workflow: every step gets a prompt file under prompts/ before code.
- No LangChain/LangGraph -- harness written from scratch, Hermes-inspired.
- All datetimes naive IST; sim-time service only, wall-clock reads forbidden in new code.
""", encoding="utf-8")
    summary_md_path(aicos.id).write_text("""# Summary

Pulse.ai's own build is on track. Backend pipeline (ingest -> claim ->
event -> notification) is fully implemented through the dream job; the
React frontend rebuild has covered every core page except the lint-job
surfaces and a few settings screens. No open blockers; two open
suggestions from the last dream cycle below.
""", encoding="utf-8")
    events_md_path(aicos.id).write_text(f"""# Events

- {now.strftime('%Y-%m-%d')}: Portfolios feature shipped end to end (backend + frontend).
- {(now - timedelta(days=1)).strftime('%Y-%m-%d')}: Fixed project-db migration sweep bug found in live testing.
- {(now - timedelta(days=2)).strftime('%Y-%m-%d')}: Slack reader app + two pool bots (Nova-bot, kettle-bot) credentials configured.
""", encoding="utf-8")

    # Pulse.ai Frontend Rebuild -- demo-ready, rich
    project_md_path(pulse.id).write_text(f"""# {pulse.name}

React + Vite + TypeScript + Tailwind rebuild of the manager-facing product
UI, replacing the Vue CDN simulator aesthetic with a polished B2B SaaS
look. Built page by page, driven by Shivam's own wireframes.

## Overview

Each page ships against the real backend (no mocking), verified via
headless-Chrome screenshots against the source wireframe before being
marked done.

## Milestones

- [x] Login, Home, Projects grid, Connectors, Agents
- [x] Project Overview + Dashboard drill-down
- [x] Portfolios (wireframes 4.png/8.png)
- [x] Tasks grid + Team view toggle
- [ ] Blocklist settings UI
- [ ] Workload view

## KPIs

- npm run build: clean
- tsc --noEmit: clean
""", encoding="utf-8")
    notes_md_path(pulse.id).write_text("""# Notes

- Assets: never invent logos/icons -- pull from /assets or ask Shivam.
- Node 20.3.1 on the dev machine is too old for `npm create vite`; scaffold was hand-written.
- Unbuilt nav items render as plain text, never dead links.
""", encoding="utf-8")
    summary_md_path(pulse.id).write_text("""# Summary

Core navigation and every primary page are built and live-verified. Two
scoped items remain (blocklist settings, workload view) with no committed
date. Design system (StatusFlower glyph, Segoe UI stack, severity palette)
is consistent across all shipped pages.
""", encoding="utf-8")
    events_md_path(pulse.id).write_text(f"""# Events

- {now.strftime('%Y-%m-%d')}: Team view + Tasks grid shipped, project-card routing fixed to open the dashboard directly.
- {(now - timedelta(days=1)).strftime('%Y-%m-%d')}: Quick-add-todo popup replaced the old Todo-button-navigates-home behavior.
""", encoding="utf-8")

    # Light projects -- short project.md only, template summary/events/notes are fine
    light_bodies = {
        market.id: (market.name, "Launch push spans the marketing site refresh, a social content calendar, and one partner webinar.", [
            "- [ ] Website hero + pricing page refresh",
            "- [ ] Social content calendar (4 weeks)",
            "- [ ] Partner webinar scheduled",
        ]),
        mobile.id: (mobile.name, "Bring the mobile app's navigation and visual language in line with the new web dashboard.", [
            "- [ ] Navigation IA reworked to match web tabs",
            "- [ ] New component library applied to top 5 screens",
        ]),
        onboarding.id: (onboarding.name, "Simplify first-run setup so a new customer reaches first value faster.", [
            "- [ ] Audit current onboarding funnel drop-off",
            "- [ ] Cut required setup steps from 7 to 4",
        ]),
        infra.id: (infra.name, "Move staging and prod off single VMs onto a managed Kubernetes cluster.", [
            "- [ ] Staging cluster provisioned",
            "- [ ] CI/CD pipeline retargeted",
            "- [ ] Prod cutover scheduled",
        ]),
    }
    for pid, (name, desc, milestones) in light_bodies.items():
        project_md_path(pid).write_text(
            f"# {name}\n\n{desc}\n\n## Overview\n\n{desc}\n\n## Milestones\n\n" + "\n".join(milestones) + "\n\n## KPIs\n\n_Not yet defined._\n",
            encoding="utf-8",
        )

    # ── tasks per project ──

    def seed_tasks(project_id, entries):
        pdb = get_project_session(project_id)
        try:
            for e in entries:
                parent = _add_task(pdb, **e["parent"])
                for sub in e.get("subtasks", []):
                    _add_task(pdb, parent_id=parent.id, **sub)
        finally:
            pdb.close()

    seed_tasks(aicos.id, [
        {
            "parent": dict(title="Ship lint job (step 26)", status="in_progress", priority="high",
                            assignee_id=alice, description="Deterministic integrity checks + optional coherence pass.", due_days=5),
            "subtasks": [
                dict(title="Write deterministic checks", status="done", priority="medium", assignee_id=alice, due_days=-1),
                dict(title="Wire optional LLM coherence pass", status="todo", priority="medium", assignee_id=alice, due_days=4),
            ],
        },
        {
            "parent": dict(title="Blocklist settings UI", status="todo", priority="medium",
                            assignee_id=bob, description="Frontend for app/projectkb/blocklist.py's CRUD API.", due_days=10),
        },
        {
            "parent": dict(title="Workload view", status="todo", priority="low", assignee_id=bob, due_days=14),
        },
        {
            "parent": dict(title="Fix Slack channel polling coverage", status="blocked", priority="high",
                            assignee_id=ally_emp, description="Needs groups:/channels:/mpim:history scopes consented on the pool app.", due_days=3),
        },
        {
            "parent": dict(title="Demo dry run", status="pending_approval", priority="high",
                            assignee_id=shivam_emp, description="Full 8-minute storyline rehearsal.", due_days=2, created_by="agent"),
        },
    ])

    seed_tasks(pulse.id, [
        {
            "parent": dict(title="Workload view page", status="in_progress", priority="medium",
                            assignee_id=carol, description="Wireframe not yet supplied -- rough out from Home's data first.", due_days=7),
            "subtasks": [
                dict(title="Design rough layout", status="done", priority="medium", assignee_id=carol, due_days=-2),
                dict(title="Wire to /api/workload endpoints", status="todo", priority="medium", assignee_id=bob, due_days=6),
            ],
        },
        {
            "parent": dict(title="Blocklist settings UI", status="todo", priority="medium", assignee_id=carol, due_days=9),
        },
        {
            "parent": dict(title="Accessibility pass on modals", status="todo", priority="low", assignee_id=ally_emp, due_days=12),
        },
        {
            "parent": dict(title="Ship Team view + Tasks grid", status="done", priority="high", assignee_id=bob, due_days=-1),
        },
    ])

    seed_tasks(market.id, [
        {"parent": dict(title="Website hero + pricing refresh", status="in_progress", priority="high", assignee_id=carol, due_days=6)},
        {"parent": dict(title="Draft social content calendar", status="todo", priority="medium", assignee_id=dave, due_days=10)},
        {"parent": dict(title="Confirm partner webinar date", status="blocked", priority="medium", assignee_id=carol,
                         description="Waiting on partner's marketing team to confirm availability.", due_days=4)},
    ])

    seed_tasks(mobile.id, [
        {"parent": dict(title="Rework navigation IA", status="todo", priority="high", assignee_id=bob, due_days=8)},
        {"parent": dict(title="Apply component library to top 5 screens", status="todo", priority="medium", assignee_id=carol, due_days=15)},
    ])

    seed_tasks(onboarding.id, [
        {"parent": dict(title="Audit onboarding funnel drop-off", status="in_progress", priority="high", assignee_id=alice, due_days=5)},
        {"parent": dict(title="Cut setup steps from 7 to 4", status="todo", priority="high", assignee_id=dave, due_days=12)},
    ])

    seed_tasks(infra.id, [
        {"parent": dict(title="Provision staging cluster", status="done", priority="high", assignee_id=alice, due_days=-3)},
        {"parent": dict(title="Retarget CI/CD pipeline", status="in_progress", priority="high", assignee_id=bob, due_days=7)},
        {"parent": dict(title="Schedule prod cutover", status="todo", priority="high", assignee_id=alice, due_days=20)},
    ])

    # ── archive + suggestions/concerns/health for the two demo-ready projects ──

    def seed_richness(project_id, archive_entries, suggestions, concerns, health):
        pdb = get_project_session(project_id)
        try:
            for kind, content in archive_entries:
                pdb.add(ArchiveEntry(kind=kind, content=content, source_ref=None))
            for text in suggestions:
                pdb.add(Suggestion(text=text, status="open"))
            for text in concerns:
                pdb.add(Concern(text=text, status="open"))
            base, adj, reason = health
            pdb.add(HealthLog(rubric_inputs=None, base_score=base, llm_adjustment=adj, reason=reason, final_score=base + adj))
            pdb.commit()
        finally:
            pdb.close()

    seed_richness(
        aicos.id,
        archive_entries=[
            ("event", "Portfolios feature shipped end to end (backend + frontend)."),
            ("event", "Project-db migration sweep bug found and fixed after live testing."),
            ("mom", "Sync with Ally: agreed to prioritize the lint job over the workload view for the next sprint."),
        ],
        suggestions=[
            "Consider batching the blocklist settings UI with the workload view -- both touch the same Home data.",
            "Slack channel polling is blocked on scope consent; worth flagging to the workspace admin directly instead of waiting on a task.",
        ],
        concerns=[
            "Lint job (step 26) has no committed ship date yet despite being the last core-pipeline piece.",
        ],
        health=(78, 3, "Recent shipped features (Portfolios, Team view) outweigh the one open blocker."),
    )

    seed_richness(
        pulse.id,
        archive_entries=[
            ("event", "Team view + Tasks grid shipped, project-card routing fixed."),
            ("event", "Quick-add-todo popup replaced Todo-button-navigates-home."),
        ],
        suggestions=[
            "Workload view has no wireframe yet -- worth a quick sketch review with Shivam before Carol builds further.",
        ],
        concerns=[
            "Accessibility pass on modals is still unscheduled with several modals already shipped.",
        ],
        health=(82, 0, "Steady page-by-page delivery, no open blockers."),
    )

    print("done.")


if __name__ == "__main__":
    main()
