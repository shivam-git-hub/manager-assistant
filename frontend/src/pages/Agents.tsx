import { useCallback, useEffect, useState } from "react";
import {
  getEvents,
  dismissEvent,
  type EventsResponse,
  listWorkflows,
  createWorkflow,
  toggleWorkflow,
  deleteWorkflow,
  listFollowups,
  listAgentActions,
  type Workflow,
  type Followup,
  type AgentAction
} from "@/lib/api";
import { ALL_CLEAR, SEVERITY_META, UPDATES_MIN_SEVERITY, PANEL_PREVIEW_COUNT } from "@/constants";

// ── Shared panel chrome (Updates / Events) ──

function UpdatesPanel() {
  const [data, setData] = useState<EventsResponse | null>(null);
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(() => {
    getEvents({ minSeverity: UPDATES_MIN_SEVERITY }).then(setData).catch(() => setData(null));
  }, []);
  useEffect(load, [load]);

  const events = data?.events ?? [];
  const shown = expanded ? events : events.slice(0, PANEL_PREVIEW_COUNT);
  const hidden = events.length - shown.length;
  const status =
    data?.max_severity != null ? SEVERITY_META[data.max_severity] ?? ALL_CLEAR : ALL_CLEAR;

  async function handleDismiss(id: string) {
    await dismissEvent(id);
    load();
  }

  return (
    <section className="flex flex-col rounded-xl border border-zinc-200 bg-white overflow-hidden shadow-sm">
      <div className="border-b border-zinc-100 bg-zinc-50/50 px-5 py-3.5 flex items-center justify-between">
        <h3 className="font-bold text-zinc-950 text-sm">System Events & Updates</h3>
        <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-zinc-100 border border-zinc-200">
          <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: status.color }} />
          <span className="text-[10px] font-bold tracking-wide uppercase text-zinc-500">
            {status.label}
          </span>
        </div>
      </div>
      <div className="p-5 flex-1">
        {events.length === 0 ? (
          <p className="text-xs text-zinc-500 py-4">
            No active events. Once ingestion runs, Harry logs system blockers and clarifications here.
          </p>
        ) : (
          <>
            <ul className="space-y-3">
              {shown.map((ev) => (
                <li key={ev.id} className="group flex items-start gap-2.5 text-xs text-zinc-700 leading-normal">
                  <span
                    className="mt-1.5 h-1.5 w-1.5 rounded-full shrink-0"
                    style={{ background: SEVERITY_META[ev.severity]?.color }}
                    aria-label={SEVERITY_META[ev.severity]?.label}
                  />
                  <span className="font-medium flex-1 min-w-0 text-zinc-800">{ev.title}</span>
                  <button
                    onClick={() => handleDismiss(ev.id)}
                    aria-label={`Dismiss: ${ev.title}`}
                    className="opacity-0 group-hover:opacity-100 text-zinc-400 hover:text-zinc-600 transition-opacity px-1"
                  >
                    &times;
                  </button>
                </li>
              ))}
            </ul>
            <div className="mt-4 flex items-center gap-3 text-[11px] text-zinc-500">
              {hidden > 0 && <span>+{hidden} more..</span>}
              {events.length > PANEL_PREVIEW_COUNT && (
                <button onClick={() => setExpanded(!expanded)} className="underline hover:text-zinc-800 transition-colors">
                  {expanded ? "Show less" : "View All"}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}

export default function Agents() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [followups, setFollowups] = useState<Followup[]>([]);
  const [actions, setActions] = useState<AgentAction[]>([]);
  
  const [adding, setAdding] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newCron, setNewCron] = useState("30m");
  const [newPrompt, setNewPrompt] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newType, setNewType] = useState("custom");

  const [loading, setLoading] = useState(true);

  const loadData = useCallback(() => {
    setLoading(true);
    Promise.all([listWorkflows(), listFollowups(), listAgentActions()])
      .then(([wfs, fls, acts]) => {
        setWorkflows(wfs);
        setFollowups(fls);
        setActions(acts);
      })
      .catch((err) => console.error("Failed to load agents data", err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadData();
    // Poll every 10 seconds for real-time status updates on followups and workflows
    const interval = setInterval(loadData, 10000);
    return () => clearInterval(interval);
  }, [loadData]);

  async function handleToggle(id: number) {
    try {
      await toggleWorkflow(id);
      loadData();
    } catch (err) {
      console.error(err);
    }
  }

  async function handleDelete(id: number) {
    if (!window.confirm("Are you sure you want to delete this workflow and its scheduled crons?")) return;
    try {
      await deleteWorkflow(id);
      loadData();
    } catch (err) {
      console.error(err);
    }
  }

  async function handleCreateWorkflow() {
    if (!newTitle.trim() || !newPrompt.trim() || !newCron.trim()) return;
    try {
      await createWorkflow({
        name: newTitle.trim(),
        cron_expression: newCron.trim(),
        prompt: newPrompt.trim(),
        description: newDesc.trim() || undefined,
        task_type: newType
      });
      setNewTitle("");
      setNewCron("30m");
      setNewPrompt("");
      setNewDesc("");
      setNewType("custom");
      setAdding(false);
      loadData();
    } catch (err) {
      console.error(err);
    }
  }

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <div className="border-b border-zinc-200 pb-5 mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-zinc-900">Agents</h1>
          <p className="mt-1.5 text-sm text-zinc-500">
            Configure agent orchestration behaviors, trigger workflows, and review active followup updates.
          </p>
        </div>
        <button
          onClick={() => setAdding(true)}
          className="rounded-lg bg-zinc-950 hover:bg-zinc-900 text-white text-xs font-semibold px-4 py-2.5 transition-colors shadow-sm"
        >
          + New Workflow
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 items-start">
        {/* Left/Main Column: Workflows & Followups */}
        <div className="lg:col-span-2 space-y-8">
          
          {/* Section 1: Automated Workflows */}
          <div>
            <div className="flex items-center justify-between border-b border-zinc-200 pb-3 mb-4">
              <h2 className="text-lg font-bold text-zinc-900">Automated Workflows</h2>
              <span className="text-xs text-zinc-400 font-mono uppercase tracking-wider">Recurring Tasks</span>
            </div>

            {loading && workflows.length === 0 ? (
              <p className="text-xs text-zinc-500 py-4">Loading active workflows...</p>
            ) : workflows.length === 0 ? (
              <p className="text-xs text-zinc-500 py-4 bg-zinc-50/50 border border-dashed border-zinc-200 rounded-xl px-5 text-center">
                No active workflows. Click "+ New Workflow" to schedule a recurring task for your Chief of Staff agent.
              </p>
            ) : (
              <div className="space-y-4">
                {workflows.map((wf) => (
                  <div
                    key={wf.id}
                    className="bg-white border border-zinc-200 rounded-xl p-5 shadow-sm flex items-start justify-between gap-6 transition-all hover:border-zinc-300"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3">
                        <h3 className="font-bold text-zinc-950 text-sm">
                          {wf.name}
                        </h3>
                        <span
                          className={`inline-block h-2 w-2 rounded-full ${
                            wf.status === "active" ? "bg-emerald-500" : "bg-zinc-300"
                          }`}
                        />
                        <span className="text-[10px] bg-zinc-100 border border-zinc-200 text-zinc-600 px-1.5 py-0.5 rounded font-medium">
                          Cron: {wf.cron_expression}
                        </span>
                      </div>
                      <p className="mt-2 text-xs text-zinc-500 leading-relaxed">
                        {wf.description || "No description provided."}
                      </p>
                    </div>

                    <div className="shrink-0 flex items-center gap-2.5">
                      <button
                        onClick={() => handleToggle(wf.id)}
                        className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition-all border ${
                          wf.status === "active"
                            ? "bg-rose-50 border-rose-200 text-rose-700 hover:bg-rose-100"
                            : "bg-emerald-50 border-emerald-200 text-emerald-700 hover:bg-emerald-100"
                        }`}
                      >
                        {wf.status === "active" ? "Pause" : "Activate"}
                      </button>
                      <button
                        onClick={() => handleDelete(wf.id)}
                        className="rounded-lg border border-zinc-200 text-zinc-500 hover:bg-zinc-50 px-3 py-1.5 text-xs font-semibold transition-colors"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Section 2: Delegated Followup Agents */}
          <div>
            <div className="flex items-center justify-between border-b border-zinc-200 pb-3 mb-4">
              <h2 className="text-lg font-bold text-zinc-900">Delegated Followup Chat Agents</h2>
              <span className="text-xs text-zinc-400 font-mono uppercase tracking-wider">Asynchronous Team Followups</span>
            </div>

            {loading && followups.length === 0 ? (
              <p className="text-xs text-zinc-500 py-4">Loading active followups...</p>
            ) : followups.length === 0 ? (
              <p className="text-xs text-zinc-500 py-4 bg-zinc-50/50 border border-dashed border-zinc-200 rounded-xl px-5 text-center">
                No active followups. Your Chief of Staff agent will spin up dedicated chat agents to follow up with team members asynchronously.
              </p>
            ) : (
              <div className="space-y-4">
                {followups.map((fl) => (
                  <div
                    key={fl.id}
                    className="bg-white border border-zinc-200 rounded-xl p-5 shadow-sm space-y-3.5 transition-all hover:border-zinc-300"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <div className="flex items-center gap-2">
                          <h3 className="font-bold text-zinc-950 text-sm">
                            Chat Agent ➜ {fl.recipient_name}
                          </h3>
                          <span
                            className={`inline-block px-2 py-0.5 rounded text-[10px] font-bold tracking-wide uppercase border ${
                              fl.status === "active"
                                ? "bg-emerald-50 border-emerald-200 text-emerald-700"
                                : fl.status === "reported"
                                ? "bg-blue-50 border-blue-200 text-blue-700"
                                : "bg-zinc-100 border-zinc-200 text-zinc-500"
                            }`}
                          >
                            {fl.status}
                          </span>
                        </div>
                        <p className="mt-1.5 text-xs text-zinc-500 font-medium">
                          Instructions: <span className="text-zinc-700 font-normal">{fl.instructions}</span>
                        </p>
                      </div>
                      <div className="text-[10px] text-zinc-400 text-right">
                        <div>Proj Scope: {fl.scoped_projects.join(", ")}</div>
                        {fl.last_message_received_at && (
                          <div className="mt-0.5">Last msg: {new Date(fl.last_message_received_at).toLocaleTimeString()}</div>
                        )}
                      </div>
                    </div>

                    {/* Report Summary (if reported back) */}
                    {fl.report_summary && (
                      <div className="bg-zinc-50 border border-zinc-150 rounded-lg p-3 text-xs leading-relaxed text-zinc-700">
                        <strong className="text-zinc-950 font-bold block mb-1">📢 Reported Update:</strong>
                        {fl.report_summary}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

        </div>

        {/* Right Column: Dynamic System Updates & Action Logs */}
        <div className="lg:col-span-1 space-y-6">
          <UpdatesPanel />

          {/* Action Log Panel */}
          <section className="flex flex-col rounded-xl border border-zinc-200 bg-white overflow-hidden shadow-sm">
            <div className="border-b border-zinc-100 bg-zinc-50/50 px-5 py-3.5 flex items-center justify-between">
              <h3 className="font-bold text-zinc-950 text-sm">Recent Agent Actions</h3>
              <span className="text-[10px] font-bold tracking-wide uppercase text-zinc-400 font-mono">Telemetry</span>
            </div>
            <div className="p-5 flex-1 max-h-[300px] overflow-y-auto">
              {actions.length === 0 ? (
                <p className="text-xs text-zinc-500 py-4">No actions logged yet.</p>
              ) : (
                <ul className="space-y-4">
                  {actions.map((act) => (
                    <li key={act.id} className="text-xs text-zinc-600 leading-normal flex flex-col gap-0.5 border-b border-zinc-50 pb-2.5 last:border-0 last:pb-0">
                      <div className="flex items-center justify-between text-[10px] text-zinc-400 font-medium">
                        <span className="font-mono uppercase text-zinc-500 font-bold tracking-wider">{act.action_type}</span>
                        <span>{new Date(act.created_at).toLocaleTimeString()}</span>
                      </div>
                      <p className="text-zinc-700 font-normal mt-1">{act.detail}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>
      </div>

      {/* Add New Workflow Modal */}
      {adding && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-950/40 backdrop-blur-sm">
          <div className="w-full max-w-md bg-white border border-zinc-200 rounded-xl p-6 shadow-xl flex flex-col gap-5">
            <div>
              <h3 className="text-base font-black text-zinc-950">Add New Workflow</h3>
              <p className="text-xs text-zinc-500 mt-1">Configure real-time automated loops and agent prompts.</p>
            </div>

            <div className="space-y-3.5">
              <label className="block">
                <span className="text-xs font-semibold text-zinc-700 uppercase tracking-wide">Workflow Title</span>
                <input
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="e.g. Morning Project Followups"
                  className="mt-1 w-full rounded-lg border border-zinc-200 px-3 py-2 text-xs focus:outline-none focus:border-zinc-950"
                />
              </label>

              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-xs font-semibold text-zinc-700 uppercase tracking-wide">Schedule (Cron)</span>
                  <input
                    value={newCron}
                    onChange={(e) => setNewCron(e.target.value)}
                    placeholder="e.g. 0 9 * * * or 30m"
                    className="mt-1 w-full rounded-lg border border-zinc-200 px-3 py-2 text-xs focus:outline-none focus:border-zinc-950 font-mono"
                  />
                </label>

                <label className="block">
                  <span className="text-xs font-semibold text-zinc-700 uppercase tracking-wide">Task Category</span>
                  <select
                    value={newType}
                    onChange={(e) => setNewType(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-zinc-200 px-3 py-2 text-xs focus:outline-none focus:border-zinc-950 bg-white"
                  >
                    <option value="followup">Followup</option>
                    <option value="morning_brief">Morning Brief</option>
                    <option value="custom">Custom</option>
                  </select>
                </label>
              </div>

              <label className="block">
                <span className="text-xs font-semibold text-zinc-700 uppercase tracking-wide">Trigger Prompt / Instructions</span>
                <textarea
                  value={newPrompt}
                  onChange={(e) => setNewPrompt(e.target.value)}
                  placeholder="e.g. Scans for overdue tasks, spins up chat agents, and delivers Slack updates."
                  rows={3}
                  className="mt-1 w-full rounded-lg border border-zinc-200 px-3 py-2 text-xs focus:outline-none focus:border-zinc-950 resize-none font-normal leading-relaxed text-zinc-800"
                />
              </label>

              <label className="block">
                <span className="text-xs font-semibold text-zinc-700 uppercase tracking-wide">Short Description (for UI)</span>
                <input
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  placeholder="e.g. Runs daily at 9:00 AM IST to collect updates."
                  className="mt-1 w-full rounded-lg border border-zinc-200 px-3 py-2 text-xs focus:outline-none focus:border-zinc-950"
                />
              </label>
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-zinc-100 pt-4">
              <button
                onClick={() => setAdding(false)}
                className="rounded-lg border border-zinc-200 text-zinc-500 hover:bg-zinc-50 font-semibold text-xs px-4 py-2 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateWorkflow}
                disabled={!newTitle.trim() || !newPrompt.trim() || !newCron.trim()}
                className="rounded-lg bg-zinc-950 hover:bg-zinc-900 text-white font-semibold text-xs px-4 py-2 transition-colors disabled:opacity-50"
              >
                Create Workflow
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
