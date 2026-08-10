export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const isProductionGateway = window.location.port === "3000" || window.location.pathname.includes("/manager-assistant");
const API_PREFIX = isProductionGateway ? "/api/manager-assistant" : "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const fullPath = path.startsWith("http") ? path : `${API_PREFIX}${path}`;
  const res = await fetch(fullPath, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, body || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Auth ─────────────────────────────────────────────────

export interface Manager {
  id: string;
  email: string;
  name: string;
}

export function getMe(): Promise<Manager> {
  return request("/api/auth/me");
}

export function logout(): Promise<void> {
  return request("/api/auth/logout", { method: "POST" });
}

// Full-page navigations, not fetches -- these kick off an OAuth redirect
// round trip, so the browser itself needs to leave the SPA.
export function goToOutlookLogin() {
  window.location.href = `${API_PREFIX}/auth/outlook/login`;
}

// Step 29: separate from login -- this is the Connectors-page grant that
// actually requests mailbox read access (Mail.Read). Signing in no longer
// implies this.
export function goToOutlookConnectMail() {
  window.location.href = `${API_PREFIX}/auth/outlook/connect-mail`;
}

export function goToOutlookEnableSend() {
  window.location.href = `${API_PREFIX}/auth/outlook/enable-send`;
}

export function goToSlackInstall() {
  window.location.href = `${API_PREFIX}/auth/slack/install`;
}

// ── Connections (Connectors page) ────────────────────────

export interface OutlookConnection {
  connected: boolean;
  mailbox_email?: string;
  send_enabled?: boolean;
}

// Slack "reading" only -- tracking this manager's own messages, entirely
// unrelated to whether they've claimed an agent (see PoolAgent/getMyAgent
// below for that).
export interface SlackConnection {
  connected: boolean;
  team_id?: string | null;
  team_name?: string | null;
}

export interface Connections {
  outlook: OutlookConnection;
  slack: SlackConnection;
}

export function getConnections(): Promise<Connections> {
  return request("/api/auth/connections");
}

export function disconnectOutlook(): Promise<void> {
  return request("/auth/outlook/disconnect", { method: "POST" });
}

// Our-side send revoke: Pulse stops using send access (the outbound gate
// closes); Microsoft's consent record itself stays until the user removes
// it at account.live.com/consent.
export function revokeOutlookSend(): Promise<void> {
  return request("/auth/outlook/revoke-send", { method: "POST" });
}

export function disconnectSlack(): Promise<void> {
  return request("/auth/slack/disconnect", { method: "POST" });
}

// ── Agent pool ───────────────────────────────────────────

export interface PoolAgent {
  id: string;
  name: string;
}

// The bot this manager has claimed, if any -- separate from Connections'
// `slack` key (message tracking). `installed` is purely informational:
// whether the admin has installed this agent's Slack app to the
// workspace yet -- nothing for the manager to do about it either way.
export interface MyAgent {
  agent_id: string;
  agent_name: string;
  installed: boolean;
  team_id: string | null;
}

export function getMyAgent(): Promise<MyAgent | null> {
  return request("/api/agents/mine");
}

// Available agents are visible to any logged-in user; the admin's access
// code gates the CLAIM, not the view.
export function getAvailableAgents(): Promise<{ agents: PoolAgent[] }> {
  return request("/api/agents/available");
}

export function claimAgent(agentId: string, code: string): Promise<{ agent_id: string; agent_name: string }> {
  return request("/api/agents/claim", {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, code }),
  });
}

export function releaseAgent(): Promise<{ status: string }> {
  return request("/api/agents/release", { method: "POST" });
}

// ── Todos ────────────────────────────────────────────────

export interface TodoItem {
  id: string;
  text: string;
  due: string | null;
  status: "open" | "done";
  created_at: string | null;
  updated_at: string | null;
}

export function getTodos(status?: "open" | "done"): Promise<TodoItem[]> {
  return request(`/api/todos${status ? `?status=${status}` : ""}`);
}

export function createTodo(text: string, due?: string): Promise<TodoItem> {
  return request("/api/todos", {
    method: "POST",
    body: JSON.stringify({ text, due: due || null }),
  });
}

export function patchTodo(
  id: string,
  patch: Partial<Pick<TodoItem, "text" | "due" | "status">>,
): Promise<TodoItem> {
  return request(`/api/todos/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function deleteTodo(id: string): Promise<void> {
  return request(`/api/todos/${id}`, { method: "DELETE" });
}

// ── Events (Updates panel) ───────────────────────────────

export interface EventItem {
  id: string;
  type: string;
  severity: number;
  title: string;
  body: string | null;
  project_ids: string[];
  task_ids: string[];
  claim_ids: string[];
  general: boolean;
  ui_state: "shown" | "dismissed" | "promoted";
  created_at: string | null;
}

export interface EventsResponse {
  events: EventItem[];
  total_matching: number;
  max_severity: number | null;
}

export function getEvents(opts?: {
  minSeverity?: number;
  limit?: number;
  includeDismissed?: boolean;
}): Promise<EventsResponse> {
  const params = new URLSearchParams();
  if (opts?.minSeverity !== undefined) params.set("min_severity", String(opts.minSeverity));
  if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts?.includeDismissed) params.set("include_dismissed", "true");
  const qs = params.toString();
  return request(`/api/events${qs ? `?${qs}` : ""}`);
}

export function dismissEvent(id: string): Promise<EventItem> {
  return request(`/api/events/${id}/dismiss`, { method: "POST" });
}

export function promoteEvent(id: string): Promise<EventItem> {
  return request(`/api/events/${id}/promote`, { method: "POST" });
}

// Requests-panel actions (step 24) -- request events only; approving one
// tied to a specific task (via the project fan-out's request->task
// linkage) also marks that task done on the backend.
export function approveEvent(id: string): Promise<EventItem> {
  return request(`/api/events/${id}/approve`, { method: "POST" });
}

export function rejectEvent(id: string): Promise<EventItem> {
  return request(`/api/events/${id}/reject`, { method: "POST" });
}

// ── Projects registry ────────────────────────────────────

export interface ProjectSummary {
  id: string;
  name: string;
  description: string | null;
  kind: "team" | "personal";
  is_manager: boolean;
  member_count: number;
  health: "green" | "yellow" | "red" | null;
  blockers_count: number;
  actions_count: number;
}

export interface ProjectMemberDetail {
  employee_id: string;
  name: string;
  email: string;
  role: string | null;
}

export interface ProjectDetail extends Omit<ProjectSummary, "member_count" | "health" | "blockers_count" | "actions_count"> {
  manager_user_id: string;
  supervisors: string[];
  members: ProjectMemberDetail[];
  created_at: string;
}

export function getProjects(): Promise<ProjectSummary[]> {
  return request("/api/projects");
}

export function createProject(payload: {
  name: string;
  description: string;
  kind: "team" | "personal";
  member_employee_ids?: { employee_id: string; role?: string }[];
}): Promise<ProjectDetail> {
  return request("/api/projects", { method: "POST", body: JSON.stringify(payload) });
}

export function getProject(id: string): Promise<ProjectDetail> {
  return request(`/api/projects/${id}`);
}

export function patchProjectMembers(
  id: string,
  payload: {
    add_member_employee_ids?: { employee_id: string; role?: string }[];
    remove_member_employee_ids?: string[];
  }
): Promise<ProjectDetail> {
  return request(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function deleteProject(id: string): Promise<void> {
  return request(`/api/projects/${id}`, { method: "DELETE" });
}

// ── Portfolios ───────────────────────────────────────────

export interface Portfolio {
  id: string;
  name: string;
  created_at: string;
  projects: ProjectSummary[];
}

export function getPortfolios(): Promise<Portfolio[]> {
  return request("/api/portfolios");
}

export function getPortfolio(id: string): Promise<Portfolio> {
  return request(`/api/portfolios/${id}`);
}

export function createPortfolio(name: string): Promise<Portfolio> {
  return request("/api/portfolios", { method: "POST", body: JSON.stringify({ name }) });
}

export function patchPortfolio(
  id: string,
  payload: { name?: string; add_project_ids?: string[]; remove_project_ids?: string[] }
): Promise<Portfolio> {
  return request(`/api/portfolios/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function deletePortfolio(id: string): Promise<void> {
  return request(`/api/portfolios/${id}`, { method: "DELETE" });
}

// ── Project drill-down (step 21) ─────────────────────────

export interface ProjectTask {
  id: string;
  parent_task_id: string | null;
  title: string;
  description: string | null;
  priority: "low" | "medium" | "high";
  assignee_employee_id: string | null;
  assignee_name: string | null;
  status: "todo" | "in_progress" | "blocked" | "done" | "pending_approval";
  due: string | null;
  created_by: string;
  approved_by: string | null;
  schedule_state: "on_schedule" | "overdue" | "blocked" | "done" | "pending_approval";
  days_overdue: number;
  subtasks: ProjectTask[];
}

export function getProjectTasks(projectId: string): Promise<{ tasks: ProjectTask[]; my_tasks: ProjectTask[] }> {
  return request(`/api/projects/${projectId}/tasks`);
}

export interface TaskInput {
  title?: string;
  description?: string;
  priority?: string;
  assignee_employee_id?: string | null;
  due?: string | null;
  parent_task_id?: string;
  status?: string;
}

export function createProjectTask(projectId: string, input: TaskInput): Promise<ProjectTask> {
  return request(`/api/projects/${projectId}/tasks`, { method: "POST", body: JSON.stringify(input) });
}

export function patchProjectTask(projectId: string, taskId: string, input: TaskInput): Promise<ProjectTask> {
  return request(`/api/projects/${projectId}/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify(input) });
}

export interface ProjectDoc {
  project_md: string;
  notes_md: string;
}

export function getProjectDoc(projectId: string): Promise<ProjectDoc> {
  return request(`/api/projects/${projectId}/doc`);
}

export function putProjectDoc(projectId: string, doc: Partial<ProjectDoc>): Promise<ProjectDoc> {
  return request(`/api/projects/${projectId}/doc`, { method: "PUT", body: JSON.stringify(doc) });
}

export interface VaultFile {
  name: string;
  size: number;
}

export function getVaultFiles(projectId: string): Promise<{ files: VaultFile[] }> {
  return request(`/api/projects/${projectId}/vault`);
}

export async function uploadVaultFile(projectId: string, file: File): Promise<VaultFile> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`/api/projects/${projectId}/vault`, {
    method: "POST",
    credentials: "include",
    body: form, // no Content-Type header -- the browser sets the multipart boundary
  });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json();
}

export function vaultDownloadUrl(projectId: string, name: string): string {
  return `/api/projects/${projectId}/vault/${encodeURIComponent(name)}`;
}

export interface ProjectInsights {
  conflicts: { id: number; claim_a_ref: string; claim_b_ref: string; severity: string }[];
  suggestions: { id: number; text: string }[];
  concerns: { id: number; text: string }[];
  health: { final_score: number; base_score: number; reason: string | null } | null;
}

export function getProjectInsights(projectId: string): Promise<ProjectInsights> {
  return request(`/api/projects/${projectId}/insights`);
}

export function getProjectEvents(projectId: string): Promise<EventsResponse> {
  return request(`/api/events?min_severity=0&project_id=${projectId}`);
}

export function addManualMessage(content: string, subject?: string, projectId?: string): Promise<{ id: number }> {
  return request("/api/messages/manual", {
    method: "POST",
    body: JSON.stringify({ content, subject: subject || null, project_id: projectId || null }),
  });
}

// ── Employees directory ──────────────────────────────────

export interface Employee {
  id: string;
  email: string;
  slack_id: string | null;
  name: string;
  role: string | null;
  skills: string[];
}

export function getEmployees(): Promise<Employee[]> {
  return request("/api/employees");
}

// ── Chat (Harry, the personal agent) ─────────────────────

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  tool_trace?: Record<string, unknown>[] | null;
  created_at: string;
}

export function getChatHistory(limit = 50): Promise<ChatMessage[]> {
  return request(`/api/chat/history?limit=${limit}`);
}

export function postChatMessage(message: string): Promise<{ reply: string; tool_trace: Record<string, unknown>[]; created_at: string }> {
  return request("/api/chat", { method: "POST", body: JSON.stringify({ message }) });
}
