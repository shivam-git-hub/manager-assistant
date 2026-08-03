# Graph Report - .  (2026-07-29)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1877 nodes · 5065 edges · 136 communities (94 shown, 42 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 360 edges (avg confidence: 0.53)
- Token cost: 6,891 input · 1,542 output

## Graph Freshness
- Built from commit: `0c606e18`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Gemini Client and Extraction
- Morning Briefing Service
- Team Management API
- Candidate Selection Logic
- Contact Blocklist API
- Event and Todo Management
- Outlook Authentication
- Slack Authentication
- Shared Enums and Models
- Database Schema Definitions
- Project Synthesis Engine
- Project Database Plumbing
- Simulated Time Service
- Frontend Dependencies
- Meetings and Action Items
- Outlook Connector Implementation
- Project Detail API
- Claim Ingestion Pipeline
- Slack Connector Implementation
- User Synthesis Job
- Portfolio and Project Registry
- Control Plane Models
- Work Hours and Scheduling
- Connection Management UI
- Project Heartbeat Fanout
- Manager Authentication API
- Project Creation UI
- User Heartbeat Job
- Dashboard Backend Tests
- Navigation and Layout Components
- TypeScript Configuration
- Base Connector Interface
- Agent Chat API
- Agent Assignment Flow
- HTML Processing Utilities
- Meeting Brief Handlers
- Project Task UI
- Agent Management Tests
- Developer Debug Tools
- Project Knowledge Models
- Agent Tool Registry
- Project Detail Tests
- Message Polling Jobs
- Database Initialization and Auth Tests
- Slack API Utilities
- Manager Data Tenancy
- Dashboard API
- Agent Context Assembly
- Job Scheduling Logic
- Workload Visualization
- Portfolio Management UI
- Iteration Budgeting
- Gemini API Mapping
- Background Job Scheduler
- Project Truth UI
- Todo List UI
- Portfolio Management Tests
- Time Service Tests
- Test Configuration and Fixtures
- Pollable Connector Protocol
- Meeting Calendar UI
- Agent Direct Messaging
- App Lifecycle and Exceptions
- Manager Database Plumbing
- Login and Auth UI
- Chat Dock UI
- Chat Widget Components
- Frontend Routing
- Meeting Detail UI
- Slack Webhook Handling
- Briefs List UI
- Conflict Resolution UI
- Architecture Documentation
- Agent Notes Service
- Feature Roadmap
- Agent Harness Documentation
- Clock Mocking Fixtures
- Clock Mocking Fixtures
- Clock Mocking Fixtures
- Clock Mocking Fixtures
- Clock Mocking Fixtures
- AI Assistant Branding
- Ingestion Feature Docs
- Slack Branding
- Teams Branding
- Extraction Pipeline Overview
- Agent Identity and Messaging
- Knowledge Base Schema
- AI Claim Extraction
- Truth Synthesis and Validation
- Project Scheduling and Health
- Agent Tooling and Dashboard
- Project Dashboard UI
- Detailed Dashboard Views
- Meeting and Calendar Integration
- Workload and Resource Management
- Demo and Testing Scenarios
- Autonomous Follow-up System
- Multi-Tenant Auth Infrastructure
- Manager Data Partitioning
- Manager Specific Scheduling
- Manager Agent Pool
- Global Project Directory
- Home Dashboard Backend
- Poll and Thread Tracking
- Project Detail Views
- Data Ingestion and Extraction
- User Heartbeat Analysis
- Project Heartbeat Updates
- Synthesis and Health Scoring
- Data Integrity and Linting
- Frontend Development Tasks
- Personal Agent Interface
- Pulse AI Platform
- Integration Simulator
- Agent Messaging Identity
- Truth Synthesis Engine
- Workload Management Features
- System Architecture Specification
- User Authentication UI
- Project Timeline Visualization

## God Nodes (most connected - your core abstractions)
1. `GeminiClient` - 141 edges
2. `TeamMember` - 89 edges
3. `UnifiedMessage` - 78 edges
4. `Manager` - 55 edges
5. `Event` - 51 edges
6. `get_project_session()` - 49 edges
7. `Project` - 44 edges
8. `Entity` - 42 edges
9. `request()` - 42 edges
10. `Task` - 36 edges

## Surprising Connections (you probably didn't know these)
- `Harry (Manager Assistant)` --references--> `AI Assistant Icon`  [INFERRED]
  CLAUDE.md → assets/ai-assistant.png
- `Outlook Icon` --conceptually_related_to--> `Outlook Sign-In — Authorization-Code Redirect Flow`  [INFERRED]
  frontend/src/assets/outlook_icon.png → prompts/step_13_connect_outlook.md
- `Slack Logo` --conceptually_related_to--> `Slack Workspace Connect — Multi-Tenant OAuth + team_id Webhook Routing`  [INFERRED]
  frontend/src/assets/slack_logo.png → prompts/step_14_connect_slack.md
- `Dashboard Overview` --conceptually_related_to--> `Pulse.ai Architecture v2 — Knowledge Base & Job Pipeline`  [INFERRED]
  wireframes/PULSE/4.png → spec/architecture_v2_kb.md
- `Connectors Management` --conceptually_related_to--> `Feature 02: Unified Connectors and Message Ingestion`  [INFERRED]
  wireframes/PULSE/10.png → spec/feature_02_connectors.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Architecture v2 Pipeline Flow** — prompts_step_22_ingest_job, prompts_step_23_heartbeat_user, prompts_step_24_heartbeat_project_fanout, prompts_step_25_dream_job, prompts_step_26_lint_job, prompts_step_28_personal_agent [EXTRACTED 1.00]
- **Core Knowledge Base System** — spec_feature_06_kb, spec_feature_07_extraction, spec_feature_08_dream_cycle [EXTRACTED 1.00]
- **Agent Harness & Reasoning Loop** — spec_feature_10_agent, spec_feature_11_autonomous_followups, spec_research_hermes_index [EXTRACTED 0.95]
- **Simulation & Demo Infrastructure** — spec_feature_03_simulator, spec_feature_04_sim_time, spec_feature_14_demo_seed [EXTRACTED 0.90]

## Communities (136 total, 42 thin omitted)

### Community 0 - "Gemini Client and Extraction"
Cohesion: 0.06
Nodes (82): GeminiClient, get_client(), UnifiedMessage, extract_from_message(), Session, AttributedClaim, Conflict, Entity (+74 more)

### Community 1 - "Morning Briefing Service"
Cohesion: 0.05
Nodes (68): Brief, get_brief_by_date(), get_briefs(), datetime, get, Session, Assembles project metrics, releases overnight pings, calls the LLM to write a…, run_morning_brief() (+60 more)

### Community 2 - "Team Management API"
Cohesion: 0.09
Nodes (53): create_or_update_team_member(), delete_team_member(), get_team_members(), delete, get, post, Session, append_timeline_entry() (+45 more)

### Community 3 - "Candidate Selection Logic"
Cohesion: 0.09
Nodes (51): _already_logged(), build_candidates(), Candidate, owned_projects_for_manager(), Session, Deterministic candidate selection for the personal agent's heartbeat (step 28…, Projects this manager actually owns (write authority) -- manager-only project…, _select_conflicts() (+43 more)

### Community 4 - "Contact Blocklist API"
Cohesion: 0.08
Nodes (46): BlockedChannelCreate, BlockedContactCreate, BlockedContactUpdate, create_blocked_channel(), create_blocked_contact(), delete_blocked_channel(), delete_blocked_contact(), get_blocklist() (+38 more)

### Community 5 - "Event and Todo Management"
Cohesion: 0.09
Nodes (37): add_manual_message(), approve_event(), create_todo(), delete_todo(), dismiss_event(), _event_dict(), list_events(), list_todos() (+29 more)

### Community 6 - "Outlook Authentication"
Cohesion: 0.09
Nodes (37): _authority(), _confidential_app(), _handle_enable_send_callback(), _handle_login_callback(), outlook_callback(), outlook_disconnect(), outlook_enable_send(), outlook_login() (+29 more)

### Community 7 - "Slack Authentication"
Cohesion: 0.08
Nodes (37): One manager's own Slack user-token grant for message *tracking* -- deliberately…, SlackReaderInstallation, get, Manager, post, Slack "Connect" for MESSAGE TRACKING -- a user-token-only OAuth flow,…, authed_user.id IS the manager's own Slack user id -- without writing it onto…, Revokes the user token via Slack's auth.revoke (best-effort) and forgets our… (+29 more)

### Community 8 - "Shared Enums and Models"
Cohesion: 0.11
Nodes (36): ConflictSeverity, JobName, Heading text for timeline.md's three tiers. Both the template writer (paths.py)…, The four fixed projectkb background jobs. Used as the key in job_schedule.json…, TimelineSection, TodoStatus, Claim, Conflict (+28 more)

### Community 9 - "Database Schema Definitions"
Cohesion: 0.18
Nodes (37): PortfolioProjectResponse, BaseModel, BriefResponse, BaseModel, Base, Digest, Leave, Project (+29 more)

### Community 10 - "Project Synthesis Engine"
Cohesion: 0.12
Nodes (33): Event, Condensed, judged output of the heartbeat agent (spec §2): typed, tagged,…, _append_lines(), _build_project_prompt(), _call_project_synthesis(), _call_user_synthesis(), _compute_base_health_score(), _days_since_last_progress() (+25 more)

### Community 11 - "Project Database Plumbing"
Cohesion: 0.13
Nodes (33): _engine_for(), get_project_engine(), Per-project engine/session plumbing -- mirrors app/tenancy/db.py's engine-cache…, One SQLAlchemy engine per project db.sqlite path, cached so repeated calls for…, ensure_project_scaffold(), events_md_path(), _events_md_template(), notes_md_path() (+25 more)

### Community 12 - "Simulated Time Service"
Cohesion: 0.10
Nodes (35): advance(), api_advance_time(), api_reset_time(), api_set_time(), fire_time_change(), _get_sim_clock_path(), get_state(), get_time() (+27 more)

### Community 13 - "Frontend Dependencies"
Cohesion: 0.06
Nodes (34): autoprefixer, dependencies, react, react-dom, react-markdown, react-router-dom, devDependencies, autoprefixer (+26 more)

### Community 14 - "Meetings and Action Items"
Cohesion: 0.17
Nodes (31): ActionItem, Meeting, Followup, ActionItemResponse, Config, create_meeting(), get_meeting_detail(), get_meetings() (+23 more)

### Community 15 - "Outlook Connector Implementation"
Cohesion: 0.09
Nodes (20): outlook_mock_ingest(), OutlookConnector, Any, datetime, Manager, post, Response, Session (+12 more)

### Community 16 - "Project Detail API"
Cohesion: 0.18
Nodes (31): create_task(), delete_project(), download_vault_file(), get_doc(), get_insights(), _get_visible_project(), list_tasks(), list_vault() (+23 more)

### Community 17 - "Claim Ingestion Pipeline"
Cohesion: 0.16
Nodes (28): Claim, ClaimSource, Short structured statement extracted from message(s) by the ingest job…, Citation join: which unified_messages row(s) a claim came from., _batch_by_thread(), _content_hash(), _extract_claims_for_batch(), _mark_skipped() (+20 more)

### Community 18 - "Slack Connector Implementation"
Cohesion: 0.10
Nodes (27): ingest(), Shared policy step: normalize, dedup, insert. Stores EVERYTHING (step 20's…, datetime, Pure transform: one conversations.history result -> the same Events-API…, User-token polling (step 17 piece 1) for a manager's own DMs -- the read-path…, conversations.list object flags -> the Events-API channel_type string…, This manager's own Slack reading grant, if any (redesigned 2026-07-23 --…, SlackConnector (+19 more)

### Community 19 - "User Synthesis Job"
Cohesion: 0.23
Nodes (29): Dream job: un-dreamed Event rows -> per-user md synthesis + per-managed-project…, run(), _add_event(), _add_task(), FakeTransport, _gemini_response(), _project_response(), Step 25 (prompts/step_25_dream_job.md): per-user md synthesis… (+21 more)

### Community 20 - "Portfolio and Project Registry"
Cohesion: 0.20
Nodes (29): create_portfolio(), create_project(), delete_portfolio(), _employee_dict(), get_portfolio(), get_project(), _health_band(), _is_visible() (+21 more)

### Community 21 - "Control Plane Models"
Cohesion: 0.19
Nodes (27): DocPutIn, BaseModel, TaskCreateIn, TaskPatchIn, MemberInput, PortfolioCreateIn, PortfolioPatchIn, ProjectCreateIn (+19 more)

### Community 22 - "Work Hours and Scheduling"
Cohesion: 0.08
Nodes (28): is_quiet_hours(), next_work_morning(), datetime, Returns True if dt is outside working hours [WORK_HOURS_START, WORK_HOURS_END)…, Returns the next datetime at 09:00 on a weekday strictly after quiet hours…, fixture, Outlook connector.send() validation error -> ok=False, error="invalidRequest"., 5. is_quiet_hours: 08:59 -> True, 09:00 -> False, 18:59 -> False, 19:00 ->… (+20 more)

### Community 23 - "Connection Management UI"
Cohesion: 0.09
Nodes (20): Slack Logo, Teams Logo, ApiError, Connections, createPortfolio(), disconnectOutlook(), disconnectSlack(), getConnections() (+12 more)

### Community 24 - "Project Heartbeat Fanout"
Cohesion: 0.20
Nodes (25): _fanout_one_project(), Session, Applies task transitions/drafts + deterministic archive writes for one…, Groups this tick's project-tagged events by project, restricts to projects this…, _run_project_fanout(), get_project_session(), DBSession, Open a new Session bound to this project's own database. Caller is responsible… (+17 more)

### Community 25 - "Manager Authentication API"
Cohesion: 0.14
Nodes (25): connections(), _dev_auth_enabled(), dev_login(), DevLoginPayload, logout(), _manager_dict(), me(), BaseModel (+17 more)

### Community 26 - "Project Creation UI"
Cohesion: 0.12
Nodes (23): CreateProjectModal(), addManualMessage(), claimAgent(), createProject(), Employee, getAvailableAgents(), getEmployees(), getMyAgent() (+15 more)

### Community 27 - "User Heartbeat Job"
Cohesion: 0.25
Nodes (22): _manager_projects(), Heartbeat job, user-level half: unprocessed Claim rows -> typed/…, This manager's own + member-of projects (registry rows), just id/name/kind --…, run(), _add_claim(), FakeTransport, _gemini_response(), _make_owned_project() (+14 more)

### Community 28 - "Dashboard Backend Tests"
Cohesion: 0.12
Nodes (18): _mk_event(), project(), fixture, Step 19 (prompts/step_19_home_backend.md): home-dashboard backend -- user-…, A failure while marking the linked task done (e.g. a locked project db) must…, A team project owned by the client's throwaway manager (step 24's approve-with-…, test_events_approve_reject_400_on_non_request_types(), test_events_approve_request_excludes_from_default_query() (+10 more)

### Community 29 - "Navigation and Layout Components"
Cohesion: 0.11
Nodes (13): ALL_CLEAR, HEALTH_COLORS, NAV_TABS, PRIORITY_META, SEVERITY_META, SIDEBAR_ITEMS, TASK_STATE_META, dismissEvent() (+5 more)

### Community 30 - "TypeScript Configuration"
Cohesion: 0.08
Nodes (23): compilerOptions, allowImportingTsExtensions, baseUrl, isolatedModules, jsx, lib, module, moduleResolution (+15 more)

### Community 31 - "Base Connector Interface"
Cohesion: 0.25
Nodes (18): ABC, OutlookInstallation, One manager's connected Outlook mailbox. token_cache_json is a serialized MSAL…, ChannelConnector, manager_identifier(), NormalizedMessage, Shared connector interface + ingestion policy for all channels. Split of…, Do we have real credentials for this channel right now? Lets the app boot and… (+10 more)

### Community 32 - "Agent Chat API"
Cohesion: 0.16
Nodes (21): ChatMessageResponse, ChatRequest, ChatResponse, clear_chat_history(), Config, force_heartbeat(), get_agent_notes(), get_chat_history() (+13 more)

### Community 33 - "Agent Assignment Flow"
Cohesion: 0.15
Nodes (21): claim_agent(), ClaimRequest, list_available_agents(), my_agent(), BaseModel, get, Manager, post (+13 more)

### Community 34 - "HTML Processing Utilities"
Cohesion: 0.11
Nodes (11): clean_html(), HTMLToMarkdown, HTMLParser, Step 20 inversion proof: a DM from someone with no TeamMember row and (in v1…, Step 20: channel/group messages are stored too (v1 dropped them unless the…, An installed Agent is required for webhook routing (step 17 piece 2b) to…, _seed_slack_installation(), test_html_cleaner() (+3 more)

### Community 35 - "Meeting Brief Handlers"
Cohesion: 0.14
Nodes (19): pre_meeting_brief_handler(), Scheduler cron execution handler for 'pre_meeting_brief:<id>'. Compiles…, get_or_create_entity(), Session, slugify(), lazy_pre_meeting_brief(), init_manager_db(), Schema create + column migrations + Harry seed + entity backfill + default job… (+11 more)

### Community 36 - "Project Task UI"
Cohesion: 0.12
Nodes (16): approveEvent(), createProjectTask(), deleteProject(), EventItem, getProjectEvents(), getProjectInsights(), getProjectTasks(), patchProjectTask() (+8 more)

### Community 37 - "Agent Management Tests"
Cohesion: 0.20
Nodes (18): access_code_env(), _claim(), _login(), fixture, Shivam 2026-07-23: users see the available agents WITHOUT the code; the code…, _seed_agent(), test_available_lists_only_unassigned_agents_no_code_needed(), test_claim_already_claimed_by_someone_else_is_409() (+10 more)

### Community 38 - "Developer Debug Tools"
Cohesion: 0.14
Nodes (18): list_claims(), list_jobs(), _message_preview(), get, Manager, post, Session, Manual job triggers + message/claim/event chain visualization for local testing… (+10 more)

### Community 39 - "Project Knowledge Models"
Cohesion: 0.19
Nodes (18): ArchiveEntry, Concern, Conflict, HealthLog, ProjectBase, DeclarativeBase, Per-project db.sqlite tables (spec/architecture_v2_kb.md §3, step 18 --…, Auditable health history (spec §4.4): a deterministic rubric produces… (+10 more)

### Community 40 - "Agent Tool Registry"
Cohesion: 0.15
Nodes (13): Any, Session, Registers a tool with its name, schema, and handler function., Returns OpenAI-compatible tool/function declarations for all registered tools., Executes a registered tool handler with the parsed arguments, DB session, the…, ToolEntry, ToolRegistry, Session (+5 more)

### Community 41 - "Project Detail Tests"
Cohesion: 0.12
Nodes (11): _other_client(), project(), fixture, Step 21 (prompts/step_21_project_drilldown.md): per-project tasks, doc/notes,…, A team project owned by the client's throwaway manager, cleaned up from disk…, _seed_employee(), test_delete_project_manager_only(), test_delete_project_removes_rows_and_dir() (+3 more)

### Community 42 - "Message Polling Jobs"
Cohesion: 0.14
Nodes (22): User-token DM/channel polling (redesigned 2026-07-23 -- reads the manager's own…, run(), get_manager_session(), DBSession, Open a new Session bound to this manager's own database. Caller is responsible…, _get_manager_id(), main(), One-off: seeds unified_messages for the real manager (Shivam Kanojia) to… (+14 more)

### Community 43 - "Database Initialization and Auth Tests"
Cohesion: 0.13
Nodes (7): init_controlplane_db(), Schema create + idempotent ALTER-TABLE migration checks -- same PRAGMA-based…, Path, Admin, one-time (re-runnable): loads agents_pool.json (gitignored -- see…, seed(), test_connections_reflects_outlook_installation(), test_init_controlplane_db_is_idempotent()

### Community 44 - "Slack API Utilities"
Cohesion: 0.15
Nodes (7): Any, post, Session, Resolves a Slack user id to a DM channel id via conversations.open (idempotent…, Given one known participant of a DM channel, find the other one. normalize()…, `raw` is a Slack Events API `event` object (or the equivalent shape built from…, Which installed Agent's bot token to send as. Prefers the agent actually…

### Community 45 - "Manager Data Tenancy"
Cohesion: 0.26
Nodes (15): ensure_manager_scaffold(), manager_db_path(), manager_dir(), manager_dump_md_path(), manager_events_md_path(), manager_memory_md_path(), manager_projects_dir(), Path (+7 more)

### Community 46 - "Dashboard API"
Cohesion: 0.28
Nodes (14): create_task(), dashboard_message_ingest(), get_dashboard_portfolio(), get_tasks(), get_unified_message_by_id(), get_unified_messages(), get, patch (+6 more)

### Community 47 - "Agent Context Assembly"
Cohesion: 0.22
Nodes (12): build_agent_context(), Session, Context assembly for the personal agent (step 28 §4) -- shared by both the…, Owned + member projects (read scope -- broader than…, Employees who are members of any project this manager touches -- not the whole…, _read_if_exists(), _tail(), team_roster_for_manager() (+4 more)

### Community 48 - "Job Scheduling Logic"
Cohesion: 0.31
Nodes (12): get_last_run(), is_due(), _load_job_state(), datetime, Config + last-run bookkeeping for the four fixed projectkb jobs…, Last real wall-clock time this job ran for this manager, or None if it has…, A job with no recorded last-run is due immediately (first boot)., _save_job_state() (+4 more)

### Community 49 - "Workload Visualization"
Cohesion: 0.19
Nodes (5): approveReassignment(), fetchWorkloadData(), mounted(), rejectReassignment(), submitLeave()

### Community 50 - "Portfolio Management UI"
Cohesion: 0.22
Nodes (11): ProjectCard(), deletePortfolio(), getPortfolio(), getProjects(), patchPortfolio(), ProjectSummary, Home(), PortfolioDetail() (+3 more)

### Community 51 - "Iteration Budgeting"
Cohesion: 0.17
Nodes (6): IterationBudget, Consumes a given amount from the budget. Raises ValueError if the budget is…, Refunds a given amount to the budget., Returns the remaining budget., A thread-safe iteration budget counter to prevent infinite agentic loops., Returns the original budget limit.

### Community 52 - "Gemini API Mapping"
Cohesion: 0.22
Nodes (10): json_parse_if_string(), map_tools_to_gemini(), messages_to_gemini_contents(), Any, Maps OpenAI tools format to Gemini functionDeclarations., Recursively strips $schema, additionalProperties, and title from a JSON Schema…, Translates OpenAI-shaped messages array to Gemini's contents +…, sanitize_gemini_schema() (+2 more)

### Community 53 - "Background Job Scheduler"
Cohesion: 0.18
Nodes (11): list_provisioned_managers(), Every manager who has ever logged in (a row created at first login) -- used by…, Outlook's arrival cadence -- distinct from the four KB extraction jobs. Unlike…, run(), background_loop(), check_and_run_due_jobs(), _check_and_run_jobs_for_manager(), Real wall-clock runner for the fixed projectkb jobs -- entirely independent of… (+3 more)

### Community 54 - "Project Truth UI"
Cohesion: 0.18
Nodes (3): fetchProjectData(), handler(), resolveConflict()

### Community 55 - "Todo List UI"
Cohesion: 0.18
Nodes (8): createTodo(), deleteTodo(), getTodos(), patchTodo(), TodoItem, fmtDue(), HEALTH_LABEL, TodosPanel()

### Community 56 - "Portfolio Management Tests"
Cohesion: 0.18
Nodes (6): _make_project(), Step 27 sub-item -- portfolios (wireframes 4.png, 8.png). A manager's personal,…, A second manager cannot see or mutate the first manager's portfolio., test_add_and_remove_project(), test_add_project_re_add_is_noop(), test_portfolio_scoped_to_owner()

### Community 57 - "Time Service Tests"
Cohesion: 0.15
Nodes (12): 6. Ingestion stamping tracks real wall-clock time now (sim time retired): POST…, 7. Wall-clock guard: walk app/**/*.py, assert no occurrence of datetime.now( /…, 1. now_ist() tracks real wall-clock IST time (2026-07-23: sim time retired --…, 2. set_time()/advance() still write the anchor file (the simulator UI's clock-…, 4. now_epoch() / now_utc_iso() agree with now_ist() (IST = UTC+5:30)., 5. API round-trip: GET/set/advance/reset via FastAPI TestClient still succeed…, test_01_initialization_defaults(), test_02_set_time_and_advance_are_inert_for_now_ist() (+4 more)

### Community 58 - "Test Configuration and Fixtures"
Cohesion: 0.23
Nodes (11): clean_controlplane_db(), cleanup_projects(), client(), db_session(), manager_employee_id(), fixture, Tracks project ids created during a test and rmtree's their projects/<id>/ dir…, The control-plane DB (data/controlplane.sqlite) is its own engine, not swapped… (+3 more)

### Community 59 - "Pollable Connector Protocol"
Cohesion: 0.18
Nodes (8): PollableConnector, Any, datetime, Session, Format (to, content, subject) into this channel's real wire payload and…, Only channels without push delivery implement this (Outlook)., raw channel payload -> common shape, or None if this payload isn't a real…, Protocol

### Community 60 - "Meeting Calendar UI"
Cohesion: 0.24
Nodes (5): fetchData(), formatDateLabel(), groupedMeetings(), mounted(), scheduleMeeting()

### Community 61 - "Agent Direct Messaging"
Cohesion: 0.22
Nodes (9): handle_agent_dm(), Any, Session, Slack DM replies to Harry (step 28 §6B, confirmed in scope 2026-07-23). The…, Called by the Slack webhook right after a successful ingest(), only for…, Any, Session, Core Hermes-style converse-and-execute loop for Harry. Compiles the dynamic… (+1 more)

### Community 62 - "App Lifecycle and Exceptions"
Cohesion: 0.24
Nodes (9): global_exception_handler(), lifespan(), Request, init_project_db(), Schema create + column migrations for this project's db.sqlite --…, list_provisioned_manager_ids(), Every manager who has ever logged in -- used by background jobs…, Exception (+1 more)

### Community 63 - "Manager Database Plumbing"
Cohesion: 0.67
Nodes (3): _engine_for(), get_manager_engine(), One SQLAlchemy engine per manager db.sqlite path, cached so repeated calls for…

### Community 64 - "Login and Auth UI"
Cohesion: 0.27
Nodes (7): App(), Outlook Icon, getMe(), goToOutlookLogin(), Manager, Login(), Outlook Sign-In — Authorization-Code Redirect Flow

### Community 65 - "Chat Dock UI"
Cohesion: 0.36
Nodes (5): fetchChatHistory(), mounted(), scrollToBottom(), sendMessage(), toggleCollapse()

### Community 66 - "Chat Widget Components"
Cohesion: 0.39
Nodes (7): ChatWidget(), clamp(), defaultPosition(), ChatMessage, getChatHistory(), postChatMessage(), Chat()

### Community 68 - "Meeting Detail UI"
Cohesion: 0.32
Nodes (3): fetchData(), handler(), submitMom()

### Community 69 - "Slack Webhook Handling"
Cohesion: 0.29
Nodes (5): Request, Slack's HMAC-SHA256 webhook signature scheme, checked against THIS agent's own…, Slack calls this with no session cookie -- unlike every other manager-scoped…, Webhook routing: which manager (via which agent) does this api_app_id belong…, slack_webhook()

### Community 71 - "Conflict Resolution UI"
Cohesion: 0.47
Nodes (3): fetchConflicts(), mounted(), resolveConflict()

### Community 72 - "Architecture Documentation"
Cohesion: 0.33
Nodes (6): Pulse.ai Architecture v2 — Knowledge Base & Job Pipeline, Feature 01: Project Status Tracking (Knowledge Base), Feature 06: Knowledge-Base Schema (Entities, Timeline, Claims, Conflicts) & Query APIs, gbrain Research Index, Dashboard Overview, Project Details & Tasks

### Community 73 - "Agent Notes Service"
Cohesion: 0.70
Nodes (4): AgentNote, Session, recent_notes(), record_note()

### Community 75 - "Feature Roadmap"
Cohesion: 0.50
Nodes (4): Feature 04: Backend Simulated-Time Service + LLM Model Config, Feature 09: Virtual Scheduler, Follow-up Engine, Project Health, and Morning Brief, Feature 11: Autonomous Follow-ups - Harry's Two-Tier Heartbeat, Feature 14: Demo Seed Scenario + End-to-End Pass

### Community 76 - "Agent Harness Documentation"
Cohesion: 0.67
Nodes (3): Feature 07: Gemini REST Client and Flash Claim Extraction, Feature 10: Agent Harness, Tools Registry, and Dashboard Chat Persistence, Hermes Agent Research Index

### Community 77 - "Clock Mocking Fixtures"
Cohesion: 0.67
Nodes (3): fixture, Automatically redirect SIM_CLOCK_PATH to a temporary file for every test to…, setup_tmp_clock()

### Community 78 - "Clock Mocking Fixtures"
Cohesion: 0.67
Nodes (3): fixture, Automatically redirect SIM_CLOCK_PATH to a temporary file for every test to…, setup_tmp_clock()

### Community 79 - "Clock Mocking Fixtures"
Cohesion: 0.67
Nodes (3): fixture, Automatically redirect SIM_CLOCK_PATH to a temporary file for every test to…, setup_tmp_clock()

### Community 80 - "Clock Mocking Fixtures"
Cohesion: 0.67
Nodes (3): fixture, Automatically redirect SIM_CLOCK_PATH to a temporary file for every test to…, setup_tmp_clock()

### Community 81 - "Clock Mocking Fixtures"
Cohesion: 0.67
Nodes (3): fixture, Automatically redirect SIM_CLOCK_PATH to a temporary file for every test to…, setup_tmp_clock()

## Knowledge Gaps
- **99 isolated node(s):** `name`, `private`, `version`, `type`, `dev` (+94 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **42 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `GeminiClient` connect `Gemini Client and Extraction` to `Morning Briefing Service`, `Candidate Selection Logic`, `Meeting Brief Handlers`, `Agent Tool Registry`, `Database Schema Definitions`, `Project Synthesis Engine`, `Meetings and Action Items`, `Claim Ingestion Pipeline`, `Iteration Budgeting`, `Gemini API Mapping`, `User Synthesis Job`, `Project Heartbeat Fanout`, `User Heartbeat Job`, `Agent Direct Messaging`?**
  _High betweenness centrality (0.083) - this node is a cross-community bridge._
- **Why does `TeamMember` connect `Database Schema Definitions` to `Gemini Client and Extraction`, `Morning Briefing Service`, `Team Management API`, `HTML Processing Utilities`, `Meeting Brief Handlers`, `Contact Blocklist API`, `Slack Authentication`, `Slack API Utilities`, `Manager Data Tenancy`, `Dashboard API`, `Outlook Connector Implementation`, `Meetings and Action Items`, `Slack Connector Implementation`, `Work Hours and Scheduling`, `Time Service Tests`, `Pollable Connector Protocol`, `Base Connector Interface`?**
  _High betweenness centrality (0.070) - this node is a cross-community bridge._
- **Why does `UnifiedMessage` connect `Gemini Client and Extraction` to `Morning Briefing Service`, `Team Management API`, `Contact Blocklist API`, `Event and Todo Management`, `Database Schema Definitions`, `Meetings and Action Items`, `Outlook Connector Implementation`, `Claim Ingestion Pipeline`, `Slack Connector Implementation`, `Work Hours and Scheduling`, `Base Connector Interface`, `HTML Processing Utilities`, `Meeting Brief Handlers`, `Developer Debug Tools`, `Project Detail Tests`, `Message Polling Jobs`, `Slack API Utilities`, `Dashboard API`, `Time Service Tests`, `Pollable Connector Protocol`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **Are the 17 inferred relationships involving `GeminiClient` (e.g. with `Brief` and `BriefResponse`) actually correct?**
  _`GeminiClient` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 31 inferred relationships involving `TeamMember` (e.g. with `PortfolioProjectResponse` and `Brief`) actually correct?**
  _`TeamMember` has 31 INFERRED edges - model-reasoned connections that need verification._
- **Are the 29 inferred relationships involving `UnifiedMessage` (e.g. with `PortfolioProjectResponse` and `ManualMessageIn`) actually correct?**
  _`UnifiedMessage` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 28 inferred relationships involving `Manager` (e.g. with `ChatMessageResponse` and `ChatRequest`) actually correct?**
  _`Manager` has 28 INFERRED edges - model-reasoned connections that need verification._