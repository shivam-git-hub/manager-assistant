export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
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
  window.location.href = "/auth/outlook/login";
}

export function goToOutlookEnableSend() {
  window.location.href = "/auth/outlook/enable-send";
}

export function goToSlackInstall() {
  window.location.href = "/auth/slack/install";
}

// ── Connections (Connectors page) ────────────────────────

export interface OutlookConnection {
  connected: boolean;
  mailbox_email?: string;
  send_enabled?: boolean;
}

export interface SlackConnection {
  agent_id: string;
  agent_name: string;
  installed: boolean;
  team_id: string | null;
  read_enabled: boolean;
}

export interface Connections {
  outlook: OutlookConnection;
  slack: SlackConnection | null;
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

// ── Projects registry ────────────────────────────────────

export interface ProjectSummary {
  id: string;
  name: string;
  description: string | null;
  kind: "team" | "personal";
  is_manager: boolean;
  member_count: number;
}

export interface ProjectMemberDetail {
  employee_id: string;
  name: string;
  email: string;
  role: string | null;
}

export interface ProjectDetail extends Omit<ProjectSummary, "member_count"> {
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
  description?: string;
  kind: "team" | "personal";
  member_employee_ids?: { employee_id: string; role?: string }[];
}): Promise<ProjectDetail> {
  return request("/api/projects", { method: "POST", body: JSON.stringify(payload) });
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
