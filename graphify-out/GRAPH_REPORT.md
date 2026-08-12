# Graph Report - manager-assistant-feature  (2026-08-10)

## Corpus Check
- 147 files · ~120,303 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2514 nodes · 6839 edges · 157 communities (113 shown, 44 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 444 edges (avg confidence: 0.52)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `32c75eee`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Entity
- tenancy/db.py
- kb/api.py
- get_project_session
- test_poll_completion.py
- Session
- test_outlook_auth.py
- get_employee_by_manager_id
- JobName
- TeamMember
- Event
- project_dir
- timeservice.py
- Frontend Dependencies
- UnifiedMessage
- OutlookConnector
- project_detail.py
- run
- test_slack_channels.py
- test_dream_job.py
- projects_registry.py
- test_kb_pipeline_judge.py
- outbound.py
- Connectors.tsx
- test_heartbeat_project_fanout.py
- controlplane/models.py
- api.ts
- GeminiClient
- test_home_backend.py
- constants.ts
- TypeScript Configuration
- config.py
- Employee
- _write_assignment_mirror
- test_connectors.py
- build_kb_context
- Project Task UI
- Agent Management Tests
- list_claims
- test_lint_job.py
- AgentSpec
- test_project_detail.py
- test_projectkb_scheduler.py
- Agent
- SlackConnector
- get_manager_session
- dashboard.py
- run_agent
- job_schedule.py
- Workload Visualization
- PortfolioDetail.tsx
- IterationBudget
- map_tools_to_gemini
- projectkb/scheduler.py
- Project Truth UI
- Home.tsx
- Portfolio Management Tests
- test_timeservice.py
- outlook_auth.py
- run_spec
- Meeting Calendar UI
- test_result_compaction.py
- global_exception_handler
- _engine_for
- App.tsx
- Chat Dock UI
- Chat Widget Components
- Frontend Routing
- Meeting Detail UI
- slack_webhook
- Briefs List UI
- Conflict Resolution UI
- Architecture Documentation
- Agent Notes Service
- home.py
- Feature Roadmap
- Agent Harness Documentation
- test_agent_select.py
- CLAUDE.md — Manager Assistant ("Harry" / Pulse.ai)
- test_agent_tools.py
- test_projects_registry.py
- Part 2: Agents (the bot pool)
- AI Assistant Branding
- Ingestion Feature Docs
- Slack Branding
- Teams Branding
- .normalize
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
- test_gemini_client_retry.py
- Integration Simulator
- Agent Messaging Identity
- Truth Synthesis Engine
- Workload Management Features
- System Architecture Specification
- User Authentication UI
- Project Timeline Visualization
- select.py
- Step 33 — Agentic runner foundation (Phase A)
- Connecting Outlook
- .fetch_since
- test_registry_allowlist.py
- ask_kb
- agent_heartbeat.py
- Part 1 — `Event.occurred_at`
- get_job_lock
- Step 35 — KB probe tools + agentic heartbeat (Phases C & D)
- Step 36 — Agentic dream job (Phase E)
- Step 38 — Consolidated test pass for the agentic KB refactor
- Pulse.ai — Manager Assistant ("Harry")
- Step 30 — remove `Manager` table; `Employee.id` is the identity key
- Step 37 — Lint job + Knowledge Synthesis agent (Phases F & G)
- _health_band
- test_ingest_channel_message_stored_without_any_list
- AGENTS.md
- test_channel_top_level_messages_after_gap_start_new_thread

## God Nodes (most connected - your core abstractions)
1. `GeminiClient` - 208 edges
2. `TeamMember` - 98 edges
3. `UnifiedMessage` - 97 edges
4. `Employee` - 85 edges
5. `Event` - 68 edges
6. `get_project_session()` - 67 edges
7. `get_employee_by_manager_id()` - 57 edges
8. `FakeTransport` - 57 edges
9. `Manager` - 55 edges
10. `Project` - 45 edges

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

## Communities (157 total, 44 thin omitted)

### Community 0 - "Entity"
Cohesion: 0.05
Nodes (78): get_client(), extract_from_message(), AttributedClaim, Conflict, Entity, TimelineEntry, _is_assertive_claim(), _judge_and_create_conflicts() (+70 more)

### Community 1 - "tenancy/db.py"
Cohesion: 0.08
Nodes (43): Brief, BriefResponse, Assembles project metrics, releases overnight pings, calls the LLM to write a…, run_morning_brief(), Base, DeclarativeBase, Followup, Compatibility shim. app/followups.py was deleted mid-refactor (see git history)… (+35 more)

### Community 2 - "kb/api.py"
Cohesion: 0.07
Nodes (41): create_or_update_team_member(), delete_team_member(), get_team_members(), BaseModel, delete, get, post, Session (+33 more)

### Community 3 - "get_project_session"
Cohesion: 0.07
Nodes (75): Owned + member projects (read scope -- broader than…, visible_projects_for_manager(), add_concerns_handler(), add_suggestions_handler(), _append_lines(), append_manager_events_handler(), append_project_events_handler(), apply_task_transitions_handler() (+67 more)

### Community 4 - "test_poll_completion.py"
Cohesion: 0.07
Nodes (50): BlockedChannelCreate, BlockedContactCreate, BlockedContactUpdate, create_blocked_channel(), create_blocked_contact(), delete_blocked_channel(), delete_blocked_contact(), get_blocklist() (+42 more)

### Community 5 - "Session"
Cohesion: 0.21
Nodes (17): approve_event(), delete_todo(), dismiss_event(), _event_dict(), list_events(), list_todos(), promote_event(), delete (+9 more)

### Community 6 - "test_outlook_auth.py"
Cohesion: 0.09
Nodes (28): Returns (purpose, manager_id) or (None, None) if invalid/tampered., Returns (purpose, manager_id) or (None, None) if invalid/tampered., _sign_state(), _state_secret(), _verify_state(), _login_and_connect_mail(), _mock_successful_exchange(), outlook_env() (+20 more)

### Community 7 - "get_employee_by_manager_id"
Cohesion: 0.07
Nodes (43): get_employee_by_manager_id(), One manager's own Slack user-token grant for message *tracking* -- deliberately…, Step 30: manager_id IS employees.id now -- this is a plain PK lookup, kept as a…, SlackReaderInstallation, Employee, get, post, Slack "Connect" for MESSAGE TRACKING -- a user-token-only OAuth flow,… (+35 more)

### Community 8 - "JobName"
Cohesion: 0.11
Nodes (34): ConflictSeverity, JobName, Heading text for timeline.md's three tiers. Both the template writer (paths.py)…, The fixed projectkb background jobs. Used as the key in job_schedule.json so…, TimelineSection, TodoStatus, Claim, Conflict (+26 more)

### Community 9 - "TeamMember"
Cohesion: 0.13
Nodes (36): Digest, Leave, Project, Table definitions shared by every manager's db.sqlite. There is no global…, ReassignmentSuggestion, Task, TeamMember, get_dm_channel_id() (+28 more)

### Community 10 - "Event"
Cohesion: 0.07
Nodes (56): Event, Condensed, judged output of the heartbeat agent (spec §2): typed, tagged,…, Condensed, judged output of the heartbeat agent (spec §2): typed, tagged,…, _append_lines(), _build_project_prompt(), _call_project_synthesis(), _call_user_synthesis(), _compute_base_health_score() (+48 more)

### Community 11 - "project_dir"
Cohesion: 0.12
Nodes (35): _engine_for(), get_project_engine(), init_project_db(), Per-project engine/session plumbing -- mirrors app/tenancy/db.py's engine-cache…, One SQLAlchemy engine per project db.sqlite path, cached so repeated calls for…, Schema create + column migrations for this project's db.sqlite --…, ensure_project_scaffold(), events_md_path() (+27 more)

### Community 12 - "timeservice.py"
Cohesion: 0.09
Nodes (37): advance(), api_advance_time(), api_reset_time(), api_set_time(), fire_time_change(), _get_sim_clock_path(), get_state(), get_time() (+29 more)

### Community 13 - "Frontend Dependencies"
Cohesion: 0.06
Nodes (34): autoprefixer, dependencies, react, react-dom, react-markdown, react-router-dom, devDependencies, autoprefixer (+26 more)

### Community 14 - "UnifiedMessage"
Cohesion: 0.20
Nodes (26): ActionItem, Meeting, UnifiedMessage, ActionItemResponse, Config, create_meeting(), get_meeting_detail(), get_meetings() (+18 more)

### Community 15 - "OutlookConnector"
Cohesion: 0.07
Nodes (28): outlook_mock_ingest(), outlook_poll(), OutlookConnector, Any, Employee, post, Response, Session (+20 more)

### Community 16 - "project_detail.py"
Cohesion: 0.17
Nodes (32): create_task(), delete_project(), download_vault_file(), get_doc(), get_insights(), _get_visible_project(), list_tasks(), list_vault() (+24 more)

### Community 17 - "run"
Cohesion: 0.09
Nodes (51): ClaimSource, Citation join: which unified_messages row(s) a claim came from., Citation join: which unified_messages row(s) a claim came from., _batch_by_thread(), _chunk_thread(), _content_hash(), _extract_claims_for_batch(), _format_party() (+43 more)

### Community 18 - "test_slack_channels.py"
Cohesion: 0.15
Nodes (16): Pure transform: one conversations.history result -> the same Events-API…, Pure transform: one conversations.history result -> the same Events-API…, Slack conversation handling: conversation_type on NormalizedMessage, the pure…, Two top-level (no thread_ts) channel messages close in time land in the SAME…, Threaded replies carry the parent's ts -- it must survive the reshaping so…, channel_join / message edits etc. carry a subtype -- not a real authored…, DM counterpart resolution's fast path now reads the manager's own…, test_channel_top_level_messages_within_gap_share_thread_key() (+8 more)

### Community 19 - "test_dream_job.py"
Cohesion: 0.15
Nodes (48): Agentic dream: un-dreamed Event rows -> one user-level agent run…, Dream job: un-dreamed Event rows -> per-user md synthesis + per-managed-project…, run(), manager_events_md_path(), manager_memory_md_path(), The dream job owns writing this -- durable per-user facts, Hermes-style long-…, Step 25 (dream job) owns writing this -- durable per-user facts, Hermes-style…, Dream-written: timestamped log of the user's own key events, append-only. (+40 more)

### Community 20 - "projects_registry.py"
Cohesion: 0.13
Nodes (47): _dashboard_add_project_member(), create_portfolio(), create_project(), delete_portfolio(), _employee_dict(), get_portfolio(), get_project(), _is_visible() (+39 more)

### Community 21 - "test_kb_pipeline_judge.py"
Cohesion: 0.07
Nodes (43): Project, Global projects registry (spec §3.1 -- "why projects are global"): teammates'…, Global projects registry (spec §3.1 -- "why projects are global"): teammates'…, Claim, Short structured statement extracted from message(s) by the ingest job.…, Short structured statement extracted from message(s) by the ingest job…, _dispose_claims(), _phase1_seed_message() (+35 more)

### Community 22 - "outbound.py"
Cohesion: 0.05
Nodes (54): get_connector(), _dispatch(), is_quiet_hours(), list_outbound_queue(), next_work_morning(), datetime, get, post (+46 more)

### Community 23 - "Connectors.tsx"
Cohesion: 0.09
Nodes (17): Outlook Icon, Slack Logo, Teams Logo, Connections, disconnectOutlook(), disconnectSlack(), getConnections(), goToOutlookConnectMail() (+9 more)

### Community 24 - "test_heartbeat_project_fanout.py"
Cohesion: 0.17
Nodes (30): _fanout_one_project(), Session, Applies task transitions/drafts + deterministic archive writes for one…, Runs the phase-2 agent for one owned project, then writes the deterministic…, Groups this tick's project-tagged events by project, restricts to projects this…, Groups this tick's project-tagged events by project, restricts to projects this…, _run_project_fanout(), _gemini_response_text() (+22 more)

### Community 25 - "controlplane/models.py"
Cohesion: 0.08
Nodes (45): Manual job triggers + message/claim/event chain visualization for local testing…, POST /api/kb/ask -- the manual, ad-hoc counterpart to the scheduled KB agents…, Agent pool claim flow. Wires up the pool's identity/isolation machinery: redeem…, connections(), _dev_auth_enabled(), dev_login(), DevLoginPayload, logout() (+37 more)

### Community 26 - "api.ts"
Cohesion: 0.10
Nodes (31): addManualMessage(), ApiError, claimAgent(), createPortfolio(), createProject(), Employee, getAvailableAgents(), getEmployees() (+23 more)

### Community 27 - "GeminiClient"
Cohesion: 0.17
Nodes (40): GeminiClient, Heartbeat job, user-level half: unprocessed Claim rows -> typed/…, Agentic heartbeat: unprocessed Claim rows -> typed/tagged/severity- scored…, run(), _add_claim(), _emit_events_run(), FakeTransport, _gemini_response() (+32 more)

### Community 28 - "test_home_backend.py"
Cohesion: 0.11
Nodes (21): _mk_event(), project(), fixture, Home-dashboard backend -- user-maintained todos CRUD + the Updates panel's…, Step 34: ordering must use COALESCE(occurred_at, created_at) -- a heartbeat-…, A failure while marking the linked task done (e.g. a locked project db) must…, A team project owned by the client's throwaway manager (the fan-out's approve-…, A failure while marking the linked task done (e.g. a locked project db) must… (+13 more)

### Community 29 - "constants.ts"
Cohesion: 0.16
Nodes (16): StatusFlower(), ALL_CLEAR, APP_NAME, HEALTH_COLORS, NO_DATA_COLOR, PANEL_PREVIEW_COUNT, PRIORITY_META, SEVERITY_META (+8 more)

### Community 30 - "TypeScript Configuration"
Cohesion: 0.08
Nodes (23): compilerOptions, allowImportingTsExtensions, baseUrl, isolatedModules, jsx, lib, module, moduleResolution (+15 more)

### Community 31 - "config.py"
Cohesion: 0.09
Nodes (40): ABC, OutlookInstallation, One manager's connected Outlook mailbox. token_cache_json is a serialized MSAL…, ChannelConnector, get_manager(), ingest(), manager_identifier(), NormalizedMessage (+32 more)

### Community 32 - "Employee"
Cohesion: 0.07
Nodes (48): ChatMessageResponse, ChatRequest, ChatResponse, clear_chat_history(), Config, force_heartbeat(), get_agent_notes(), get_chat_history() (+40 more)

### Community 33 - "_write_assignment_mirror"
Cohesion: 0.15
Nodes (17): claim_agent(), list_available_agents(), my_agent(), Employee, get, post, Unclaim this manager's agent (Agents tab "Remove"). Clears…, Unclaim this manager's agent (Agents tab "Remove" -- 2026-07-23). Clears… (+9 more)

### Community 34 - "test_connectors.py"
Cohesion: 0.15
Nodes (15): Step 20 inversion proof: a DM from someone with no TeamMember row and (in v1…, A DM from someone with no Employee row and no blocklist entry IS stored --…, Step 20: channel/group messages are stored too (v1 dropped them unless the…, Channel/group messages are stored too (an allowlist design dropped them unless…, DM-counterpart resolution's fast path compares the sender against the calling…, An installed Agent is required for webhook routing to resolve which manager's…, Connector resolution (SlackConnector/OutlookConnector._resolve_member) now…, _seed_employee() (+7 more)

### Community 35 - "build_kb_context"
Cohesion: 0.12
Nodes (31): Employees who are members of any project this manager touches -- not the whole…, Employees who are members of any project this manager touches -- not the whole…, team_roster_for_manager(), _blocker_counts_by_project(), build_kb_context(), ContextBudget, _memory_section(), _now_section() (+23 more)

### Community 36 - "Project Task UI"
Cohesion: 0.12
Nodes (16): approveEvent(), createProjectTask(), deleteProject(), EventItem, getProjectEvents(), getProjectInsights(), getProjectTasks(), patchProjectTask() (+8 more)

### Community 37 - "Agent Management Tests"
Cohesion: 0.20
Nodes (18): access_code_env(), _claim(), _login(), fixture, Users see the available agents WITHOUT the code; the code gates the claim, not…, _seed_agent(), test_available_lists_only_unassigned_agents_no_code_needed(), test_claim_already_claimed_by_someone_else_is_409() (+10 more)

### Community 38 - "list_claims"
Cohesion: 0.24
Nodes (11): list_claims(), list_jobs(), _message_preview(), Employee, get, post, Session, Claims + which messages they cite + processed flag -- the middle link in the… (+3 more)

### Community 39 - "test_lint_job.py"
Cohesion: 0.08
Nodes (51): _check_claim_sources(), _check_event_claim_ids(), _check_poll_health(), _check_project_orphans(), _check_staleness(), _emit(), _epoch_of(), Any (+43 more)

### Community 40 - "AgentSpec"
Cohesion: 0.11
Nodes (19): Any, Session, Registers a tool with its name, schema, and handler function., Returns OpenAI-compatible tool/function declarations for all registered tools., Registers a tool with its name, schema, handler function, and a per-tool…, Every registered tool name -- used by app.agent.runner.run_spec to validate an…, Returns OpenAI-compatible tool/function declarations. `names=None` (the…, Executes a registered tool handler with the parsed arguments, DB session, the… (+11 more)

### Community 41 - "test_project_detail.py"
Cohesion: 0.12
Nodes (12): _other_client(), project(), fixture, Project drill-down: per-project tasks, doc/notes, vault, insights, deletion,…, A team project owned by the client's throwaway manager, cleaned up from disk…, Upsert by email -- login (dev-login/Outlook) now also creates an Employee row,…, _seed_employee(), test_delete_project_manager_only() (+4 more)

### Community 42 - "test_projectkb_scheduler.py"
Cohesion: 0.10
Nodes (32): Outlook's arrival cadence -- distinct from the four KB extraction jobs. Graph…, run(), User-token DM/channel polling (reads the manager's own Slack grant from their…, run(), _cleanup(), _make_manager(), outlook_poll for a manager WITH an OutlookInstallation calls fetch_since scoped…, A job failing for one manager doesn't stop the loop from processing the next… (+24 more)

### Community 43 - "Agent"
Cohesion: 0.08
Nodes (20): Agent, init_controlplane_db(), Schema create + idempotent ALTER-TABLE migration checks -- same PRAGMA-based…, Schema create + idempotent ALTER-TABLE migration checks -- same PRAGMA-based…, Pool slot. Each row is a distinctly-named, separately-registered Slack app,…, Pool slot (step 17 piece 2a -- prompts/step_17_agent_pool.md). Each row is a…, Admin, one-time (re-runnable): loads agents_pool.json (gitignored -- see…, seed() (+12 more)

### Community 44 - "SlackConnector"
Cohesion: 0.16
Nodes (11): post, Resolves a Slack user id to a DM channel id via conversations.open (idempotent…, Resolves a Slack user id to a DM channel id via conversations.open (idempotent…, Which installed Agent's bot token to send as. Prefers the agent actually…, Which installed Agent's bot token to send as. Prefers the agent actually…, SlackConnector, test_ingest_channel_message_between_non_managers_stored(), test_ingest_threaded_reply_shares_thread_key_with_parent() (+3 more)

### Community 45 - "get_manager_session"
Cohesion: 0.14
Nodes (26): get_manager_engine(), get_manager_session(), init_manager_db(), DBSession, Open a new Session bound to this manager's own database. Caller is responsible…, Open a new Session bound to this manager's own database. Caller is responsible…, Schema create + column migrations + Harry seed, scoped to one manager's…, Schema create + column migrations + Harry seed + entity backfill + default job… (+18 more)

### Community 46 - "dashboard.py"
Cohesion: 0.16
Nodes (22): create_task(), dashboard_message_ingest(), get_dashboard_portfolio(), get_tasks(), get_unified_message_by_id(), get_unified_messages(), PortfolioProjectResponse, BaseModel (+14 more)

### Community 47 - "run_agent"
Cohesion: 0.10
Nodes (28): build_agent_context(), Session, Context assembly for the personal agent -- shared by both the agent heartbeat…, _read_if_exists(), _tail(), handle_agent_dm(), Any, Session (+20 more)

### Community 48 - "job_schedule.py"
Cohesion: 0.20
Nodes (15): _deep_merge_overrides(), get_last_run(), is_due(), load_job_schedule(), _load_job_state(), Config + last-run bookkeeping for the four fixed projectkb jobs…, Last real wall-clock time this job ran for this manager, or None if it has…, A job with no recorded last-run is due immediately (first boot). (+7 more)

### Community 49 - "Workload Visualization"
Cohesion: 0.19
Nodes (5): approveReassignment(), fetchWorkloadData(), mounted(), rejectReassignment(), submitLeave()

### Community 50 - "PortfolioDetail.tsx"
Cohesion: 0.21
Nodes (12): CreateProjectModal(), ProjectCard(), deletePortfolio(), getPortfolio(), getProjects(), patchPortfolio(), ProjectSummary, Home() (+4 more)

### Community 51 - "IterationBudget"
Cohesion: 0.17
Nodes (6): IterationBudget, Consumes a given amount from the budget. Raises ValueError if the budget is…, Refunds a given amount to the budget., Returns the remaining budget., A thread-safe iteration budget counter to prevent infinite agentic loops., Returns the original budget limit.

### Community 52 - "map_tools_to_gemini"
Cohesion: 0.09
Nodes (26): json_parse_if_string(), map_tools_to_gemini(), messages_to_gemini_contents(), Any, Maps OpenAI tools format to Gemini functionDeclarations., Maps OpenAI tools format to Gemini functionDeclarations., Recursively strips $schema, additionalProperties, and title from a JSON Schema…, Recursively strips $schema, additionalProperties, and title from a JSON Schema… (+18 more)

### Community 53 - "projectkb/scheduler.py"
Cohesion: 0.09
Nodes (27): list_provisioned_managers(), Every employee who has ever logged in as a manager (is_manager=True) -- used by…, Every manager who has ever logged in (a row created at first login) -- used by…, background_loop(), check_and_run_due_jobs(), _interval_minutes(), _is_due(), _load_last_run_at() (+19 more)

### Community 54 - "Project Truth UI"
Cohesion: 0.18
Nodes (3): fetchProjectData(), handler(), resolveConflict()

### Community 55 - "Home.tsx"
Cohesion: 0.20
Nodes (9): createTodo(), deleteTodo(), getTodos(), Manager, patchTodo(), TodoItem, fmtDue(), HEALTH_LABEL (+1 more)

### Community 56 - "Portfolio Management Tests"
Cohesion: 0.18
Nodes (6): _make_project(), Portfolios. A manager's personal, named grouping of their own visible projects…, A second manager cannot see or mutate the first manager's portfolio., test_add_and_remove_project(), test_add_project_re_add_is_noop(), test_portfolio_scoped_to_owner()

### Community 57 - "test_timeservice.py"
Cohesion: 0.10
Nodes (18): now_ist() is real IST wall-clock time -- the one call site every other module…, Automatically redirect SIM_CLOCK_PATH to a temporary file for every test to…, 7. Wall-clock guard: walk app/**/*.py, assert no occurrence of datetime.now( /…, 1. now_ist() tracks real wall-clock IST time (2026-07-23: sim time retired --…, Ingestion stamping tracks real wall-clock time: POST a dashboard message…, 2. set_time()/advance() still write the anchor file (the simulator UI's clock-…, 4. now_epoch() / now_utc_iso() agree with now_ist() (IST = UTC+5:30)., 5. API round-trip: GET/set/advance/reset via FastAPI TestClient still succeed… (+10 more)

### Community 58 - "outlook_auth.py"
Cohesion: 0.11
Nodes (31): _authority(), _confidential_app(), _handle_connect_mail_callback(), _handle_enable_send_callback(), _handle_login_callback(), outlook_callback(), outlook_connect_mail(), outlook_disconnect() (+23 more)

### Community 59 - "run_spec"
Cohesion: 0.19
Nodes (28): _echo_assistant_turn(), Any, Session, Gemini turn symmetry (harness.py:81-97, load-bearing, preserved verbatim here):…, Runs one agent to completion against `spec`'s budgets. System prompt is…, _repeat_key(), run_spec(), _empty_response() (+20 more)

### Community 60 - "Meeting Calendar UI"
Cohesion: 0.24
Nodes (5): fetchData(), formatDateLabel(), groupedMeetings(), mounted(), scheduleMeeting()

### Community 61 - "test_result_compaction.py"
Cohesion: 0.16
Nodes (23): _compact_dict(), _compact_list(), compact_tool_result(), _dumps(), _fallback_raw(), Any, Structure-aware tool-result compaction, replacing the old blind str[:8000] +…, Serializes `result` (already a Python value -- dict/list/str/etc, never pre-… (+15 more)

### Community 62 - "global_exception_handler"
Cohesion: 0.50
Nodes (4): global_exception_handler(), Exception, Request, exception_handler

### Community 63 - "_engine_for"
Cohesion: 0.67
Nodes (3): _engine_for(), One SQLAlchemy engine per manager db.sqlite path, cached so repeated calls for…, One SQLAlchemy engine per manager db.sqlite path, cached so repeated calls for…

### Community 64 - "App.tsx"
Cohesion: 0.23
Nodes (7): App(), Sidebar(), TopNav(), NAV_TABS, SIDEBAR_ITEMS, getMe(), logout()

### Community 65 - "Chat Dock UI"
Cohesion: 0.36
Nodes (5): fetchChatHistory(), mounted(), scrollToBottom(), sendMessage(), toggleCollapse()

### Community 66 - "Chat Widget Components"
Cohesion: 0.39
Nodes (7): ChatWidget(), clamp(), defaultPosition(), ChatMessage, getChatHistory(), postChatMessage(), Chat()

### Community 68 - "Meeting Detail UI"
Cohesion: 0.32
Nodes (3): fetchData(), handler(), submitMom()

### Community 69 - "slack_webhook"
Cohesion: 0.20
Nodes (8): Request, Slack's HMAC-SHA256 webhook signature scheme, checked against THIS agent's own…, Slack's HMAC-SHA256 webhook signature scheme, checked against THIS agent's own…, conversations.list object flags -> the Events-API channel_type string…, Slack calls this with no session cookie -- unlike every other manager-scoped…, Webhook routing: which manager (via which agent) does this api_app_id belong…, Webhook routing: which manager (via which agent) does this api_app_id belong…, slack_webhook()

### Community 71 - "Conflict Resolution UI"
Cohesion: 0.47
Nodes (3): fetchConflicts(), mounted(), resolveConflict()

### Community 72 - "Architecture Documentation"
Cohesion: 0.33
Nodes (6): Pulse.ai Architecture v2 — Knowledge Base & Job Pipeline, Feature 01: Project Status Tracking (Knowledge Base), Feature 06: Knowledge-Base Schema (Entities, Timeline, Claims, Conflicts) & Query APIs, gbrain Research Index, Dashboard Overview, Project Details & Tasks

### Community 73 - "Agent Notes Service"
Cohesion: 0.70
Nodes (4): AgentNote, Session, recent_notes(), record_note()

### Community 74 - "home.py"
Cohesion: 0.21
Nodes (16): add_manual_message(), create_todo(), ManualMessageIn, patch_todo(), BaseModel, Employee, patch, Home-dashboard backend: user-maintained todos CRUD + the Updates panel's query… (+8 more)

### Community 75 - "Feature Roadmap"
Cohesion: 0.50
Nodes (4): Feature 04: Backend Simulated-Time Service + LLM Model Config, Feature 09: Virtual Scheduler, Follow-up Engine, Project Health, and Morning Brief, Feature 11: Autonomous Follow-ups - Harry's Two-Tier Heartbeat, Feature 14: Demo Seed Scenario + End-to-End Pass

### Community 76 - "Agent Harness Documentation"
Cohesion: 0.67
Nodes (3): Feature 07: Gemini REST Client and Flash Claim Extraction, Feature 10: Agent Harness, Tools Registry, and Dashboard Chat Persistence, Hermes Agent Research Index

### Community 77 - "test_agent_select.py"
Cohesion: 0.20
Nodes (15): build_candidates(), AgentActionLog, Idempotency ledger for the personal agent's autonomous heartbeat heartbeat. The…, Idempotency ledger for the personal agent's autonomous heartbeat (step 28 --…, fixture, Deterministic candidate selection. Follows tests/test_projects_registry.py's…, pending_approval tasks are agent-drafted, awaiting the manager's own approval…, team_project() (+7 more)

### Community 78 - "CLAUDE.md — Manager Assistant ("Harry" / Pulse.ai)"
Cohesion: 0.12
Nodes (15): Agents, CLAUDE.md — Manager Assistant ("Harry" / Pulse.ai), Connectors, Critical bugs (prevent regressions), Data layout, Essential facts, Frontend (`frontend/`), Known gotchas (+7 more)

### Community 79 - "test_agent_tools.py"
Cohesion: 0.18
Nodes (7): _make_employee(), fixture, send_message, dashboard_action, and the AgentActionLog dedup they write., team_project(), test_send_message_slack_no_slack_id_errors(), test_send_message_slack_quiet_hours_holds_and_logs_candidate(), test_send_message_unlisted_candidate_ref_is_ignored()

### Community 80 - "test_projects_registry.py"
Cohesion: 0.22
Nodes (12): extra_login(), _make_employee(), fixture, Global projects registry, employees directory, project scaffold. Follows the…, Upsert by email -- since login (dev-login/Outlook) now also creates an Employee…, Yields a factory for logging in additional throwaway managers beyond the…, Yields a factory for logging in additional throwaway managers beyond the…, test_get_employees_returns_seeded_directory() (+4 more)

### Community 81 - "Part 2: Agents (the bot pool)"
Cohesion: 0.15
Nodes (12): 1. Create each pool app (one-time per agent, by the admin), 1. Create the app (one-time), 2. Environment variables, 2. Install each app to the workspace (one-time, admin, no OAuth code), 3. Connecting, 3. Seed the pool, 4. Claiming, 5. The manager's own team_members row (+4 more)

### Community 94 - ".normalize"
Cohesion: 0.21
Nodes (8): Any, Session, Resolves a Slack user id straight against the control-plane Employee directory…, Given one known participant of a DM channel, find the other one. normalize()…, Given one known participant of a DM channel, find the other one. normalize()…, `raw` is a Slack Events API `event` object (or the equivalent shape built from…, A channel/group top-level message (no thread_ts -- Slack gives no conversation…, `raw` is a Slack Events API `event` object (or the equivalent shape built from…

### Community 129 - "test_gemini_client_retry.py"
Cohesion: 0.20
Nodes (9): _FakeHTTPError, _FakeResponse, _ok_response(), Exception, GeminiClient.chat retry/backoff behavior (step 33): Retry-After header honored…, test_backoff_without_retry_after_has_jitter_and_only_two_sleeps(), test_chat_acquires_rate_limiter_slot(), test_json_mode_with_tools_raises_before_any_request() (+1 more)

### Community 137 - "select.py"
Cohesion: 0.36
Nodes (10): _already_logged(), Candidate, owned_projects_for_manager(), Session, Deterministic candidate selection for the personal agent's heartbeat. Same…, Projects this manager actually owns (write authority) -- manager-only project…, Projects this manager actually owns (write authority) -- manager-only project…, _select_conflicts() (+2 more)

### Community 138 - "Step 33 — Agentic runner foundation (Phase A)"
Cohesion: 0.18
Nodes (10): 1. `app/agent/rate_limit.py` (new), 2. `app/agent/kb_context.py` (new) — context engineering, 3. Tool-result size limits + compaction (`app/agent/registry.py`), 4. `app/agent/runner.py` (new) — the generic loop, 5. Rewire `harness.run_agent` as a wrapper, 6. Config (`app/config.py`), 7. Regression test: the Gemini schema bug (CLAUDE.md critical bug #1), Definition of done (+2 more)

### Community 139 - "Connecting Outlook"
Cohesion: 0.22
Nodes (8): 1. Register an Azure AD app (both modes), 2a. App-only mode setup, 2b. Delegated mode setup, 3. Real arrival is polling, not a webhook, 4. The manager's own team_members row, 5. Tracked contacts, Connecting Outlook, Which mode to use

### Community 140 - ".fetch_since"
Cohesion: 0.22
Nodes (6): User-token polling (step 17 piece 1) for a manager's own DMs -- the read-path…, conversations.list object flags -> the Events-API channel_type string…, User-token polling for a manager's own DMs -- the read-path analogue of…, This manager's own Slack reading grant, if any -- NOT the Agent pool; a manager…, This manager's own Slack reading grant, if any (redesigned 2026-07-23 --…, test_conversation_object_type_mapping()

### Community 141 - "test_registry_allowlist.py"
Cohesion: 0.39
Nodes (8): _make_registry(), ToolRegistry allowlist filtering (get_tool_definitions(names=...)) and…, test_execute_allows_call_within_allowlist(), test_execute_rejects_call_outside_allowlist_with_correctable_message(), test_execute_unrestricted_when_allowed_names_none(), test_get_tool_definitions_filtered_by_names(), test_get_tool_definitions_filtered_ignores_unknown_names_silently(), test_get_tool_definitions_unfiltered_returns_all()

### Community 142 - "ask_kb"
Cohesion: 0.29
Nodes (8): ask_kb(), KBAskRequest, KBAskResponse, BaseModel, Employee, post, Session, Runs the Knowledge Synthesis agent (app.agent.synthesis.run_synthesis) against…

### Community 143 - "agent_heartbeat.py"
Cohesion: 0.32
Nodes (6): Session, Personal agent heartbeat. A 5th tick, not a re-detection pass:…, run(), End-to-end agent heartbeat job with a fake LLM client: seeded meeting -> a…, test_no_candidates_skips_llm_call(), test_pre_meeting_brief_tick_sends_and_logs()

### Community 144 - "Part 1 — `Event.occurred_at`"
Cohesion: 0.25
Nodes (7): Definition of done, Migration + read sites (this is the part that's easy to half-do), Part 1 — `Event.occurred_at`, Part 2 — Scheduler hardening, Step 34 — `Event.occurred_at` + scheduler hardening (Phase B), The change, The problem

### Community 145 - "get_job_lock"
Cohesion: 0.29
Nodes (7): get_job_lock(), The process-wide lock for this job name, shared by the scheduler's own pass and…, Lock, Defect 4: a per-job-name lock, held for the job's global pass -- the scheduler…, Same lock guard applies to POST /api/heartbeat/run -- agent_heartbeat is…, test_agent_heartbeat_manual_endpoint_409_when_locked(), test_held_lock_makes_scheduler_skip_and_manual_endpoint_409()

### Community 146 - "Step 35 — KB probe tools + agentic heartbeat (Phases C & D)"
Cohesion: 0.29
Nodes (6): Definition of done, Part 1 — `app/agent/kb_tools.py` (read-only probes), Part 2 — Heartbeat write tools (in `app/agent/kb_tools.py` or a sibling), Part 3 — The agentic heartbeat job, Step 35 — KB probe tools + agentic heartbeat (Phases C & D), Why

### Community 147 - "Step 36 — Agentic dream job (Phase E)"
Cohesion: 0.29
Nodes (6): Definition of done, Part 1 — Dream write tools, Part 2 — The agentic dream job, Part 3 — Remove `dump.md`, Step 36 — Agentic dream job (Phase E), Why

### Community 148 - "Step 38 — Consolidated test pass for the agentic KB refactor"
Cohesion: 0.33
Nodes (5): Rules, Starting state, Step 38 — Consolidated test pass for the agentic KB refactor, The one thing you must get right: faking a tool-calling agent, What to cover (main paths only)

### Community 149 - "Pulse.ai — Manager Assistant ("Harry")"
Cohesion: 0.33
Nodes (5): Codebase structure, Pulse.ai — Manager Assistant ("Harry"), Run, Setup docs, Test

### Community 150 - "Step 30 — remove `Manager` table; `Employee.id` is the identity key"
Cohesion: 0.40
Nodes (4): Changes, Step 30 — remove `Manager` table; `Employee.id` is the identity key, Verification, Why

### Community 151 - "Step 37 — Lint job + Knowledge Synthesis agent (Phases F & G)"
Cohesion: 0.40
Nodes (4): Definition of done, Part 1 — The lint job (`app/projectkb/jobs/lint.py`), Part 2 — Knowledge Synthesis agent, Step 37 — Lint job + Knowledge Synthesis agent (Phases F & G)

### Community 152 - "_health_band"
Cohesion: 0.67
Nodes (3): _health_band(), Bands a HealthLog.final_score into the frontend's green/yellow/red glyph --…, Bands a HealthLog.final_score into the frontend's green/yellow/red glyph --…

### Community 153 - "test_ingest_channel_message_stored_without_any_list"
Cohesion: 0.67
Nodes (3): v1 required the channel on an allowlist; step 20 stores everything., No allowlist -- every channel message is stored., test_ingest_channel_message_stored_without_any_list()

## Knowledge Gaps
- **170 isolated node(s):** `name`, `private`, `version`, `type`, `dev` (+165 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **44 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `GeminiClient` connect `GeminiClient` to `Entity`, `tenancy/db.py`, `test_gemini_client_retry.py`, `test_lint_job.py`, `AgentSpec`, `TeamMember`, `Event`, `UnifiedMessage`, `run_agent`, `agent_heartbeat.py`, `run`, `test_dream_job.py`, `map_tools_to_gemini`, `test_kb_pipeline_judge.py`, `test_heartbeat_project_fanout.py`, `run_spec`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `UnifiedMessage` connect `UnifiedMessage` to `Entity`, `tenancy/db.py`, `kb/api.py`, `get_project_session`, `test_poll_completion.py`, `TeamMember`, `OutlookConnector`, `run`, `test_kb_pipeline_judge.py`, `outbound.py`, `controlplane/models.py`, `GeminiClient`, `config.py`, `test_lint_job.py`, `test_project_detail.py`, `SlackConnector`, `get_manager_session`, `dashboard.py`, `test_timeservice.py`, `home.py`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Why does `Employee` connect `Employee` to `tenancy/db.py`, `get_project_session`, `test_poll_completion.py`, `test_outlook_auth.py`, `get_employee_by_manager_id`, `Event`, `project_dir`, `ask_kb`, `OutlookConnector`, `project_detail.py`, `test_dream_job.py`, `projects_registry.py`, `test_kb_pipeline_judge.py`, `outbound.py`, `controlplane/models.py`, `config.py`, `test_connectors.py`, `build_kb_context`, `test_project_detail.py`, `test_projectkb_scheduler.py`, `Agent`, `get_manager_session`, `run_agent`, `test_timeservice.py`, `outlook_auth.py`, `home.py`, `test_agent_tools.py`, `test_projects_registry.py`?**
  _High betweenness centrality (0.051) - this node is a cross-community bridge._
- **Are the 24 inferred relationships involving `GeminiClient` (e.g. with `AgentRunResult` and `AgentSpec`) actually correct?**
  _`GeminiClient` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Are the 36 inferred relationships involving `TeamMember` (e.g. with `PortfolioProjectResponse` and `UnifiedMessageResponse`) actually correct?**
  _`TeamMember` has 36 INFERRED edges - model-reasoned connections that need verification._
- **Are the 32 inferred relationships involving `UnifiedMessage` (e.g. with `PortfolioProjectResponse` and `UnifiedMessageResponse`) actually correct?**
  _`UnifiedMessage` has 32 INFERRED edges - model-reasoned connections that need verification._
- **Are the 34 inferred relationships involving `Employee` (e.g. with `ChatMessageResponse` and `ChatRequest`) actually correct?**
  _`Employee` has 34 INFERRED edges - model-reasoned connections that need verification._