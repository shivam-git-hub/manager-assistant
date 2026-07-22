# Task: Scheduler Runs Per-Manager

## Context

Repo: `manager-assistant`. Depends on step 15 (per-manager DBs/paths) being
done — this step is the payoff: making the existing job machinery actually
iterate managers instead of assuming one.

First read: `app/projectkb/scheduler.py`, `app/projectkb/job_schedule.py`,
`app/projectkb/jobs/*.py`, `app/controlplane/models.py` (`Manager`,
`OutlookInstallation`).

**Why last:** everything this step needs (per-manager `job_state.json`,
per-manager `db.sqlite`/session, per-manager `OutlookInstallation` for
polling) only exists after step 15. Doing this earlier would mean building
against paths/sessions that don't exist yet.

## Subproblem 1 — Enumerate managers

`app/controlplane/models.py` already has `Manager` (step 12). Add a helper
`list_provisioned_managers(controlplane_db) -> List[Manager]` — anyone with
a row (created at first login). Called once per scheduler tick.

## Subproblem 2 — `check_and_run_due_jobs` becomes manager-aware

Current shape (`scheduler.py`): one scan over 5 jobs (ingestion/heartbeat/
dream/lint/outlook_poll), each checked against a single global
`job_state.json`. New shape:

```python
def check_and_run_due_jobs(controlplane_db):
    for manager in list_provisioned_managers(controlplane_db):
        manager_db = get_manager_session(manager.id)
        try:
            _check_and_run_jobs_for_manager(manager, manager_db)
        finally:
            manager_db.close()

def _check_and_run_jobs_for_manager(manager, manager_db):
    cfg = load_job_schedule(manager.id)          # reads managers/<id>/job_schedule.json
                                                    # if present, else the global defaults
    for job_name, job_module in _JOBS.items():
        ...
        if is_due(manager.id, job_name, interval_minutes):   # reads managers/<id>/job_state.json
            job_module.run(manager_db, manager_id=manager.id)  # jobs get manager context now
            set_last_run(manager.id, job_name)
```

`job_schedule.load_job_schedule`/`get_last_run`/`set_last_run`/`is_due` all
gain a `manager_id` param and resolve their file path via
`manager_dir(manager_id)` (step 15 already relocated the files; this step
updates the function signatures to use them).

## Subproblem 3 — `outlook_poll` job becomes genuinely per-manager

Today's stub calls the single global `OutlookConnector.fetch_since`. New:
for the given `manager_id`, look up their `OutlookInstallation` (skip if
none — this manager hasn't connected Outlook), build/reuse a connector
instance scoped to that installation's token cache, `fetch_since`, `ingest`
into that manager's `db.sqlite` (via the `manager_db` session already
passed in).

## Subproblem 4 — `background_loop` unchanged in shape, different call

`app/main.py`'s lifespan still starts one `asyncio.create_task` polling
every `PROJECTKB_POLL_SECONDS` — it now calls `check_and_run_due_jobs
(controlplane_db)` (from step 12's control-plane session) instead of a
bare `db`. No new loop-per-manager needed; the fan-out happens inside
`check_and_run_due_jobs`.

## Test plan

- Two managers, each with their own `job_state.json`: advancing one's
  last-run doesn't affect the other's due-check.
- `outlook_poll` for a manager with no `OutlookInstallation` is a no-op
  (not an error) — most managers early on will have Slack connected but
  not Outlook, or vice versa; the loop must tolerate partial connection.
- A job failing for one manager (exception in `run()`) doesn't stop the
  loop from processing the next manager — same isolation guarantee
  `check_and_run_due_jobs` already has per-job, now also per-manager.
