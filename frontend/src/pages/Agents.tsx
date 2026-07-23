import { useCallback, useEffect, useState } from "react";
import { getEvents, dismissEvent, type EventsResponse } from "@/lib/api";
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

// ── Static Workflows (Static Demo) ──

interface StaticWorkflow {
  id: string;
  title: string;
  description: string;
  enabled: boolean;
}

export default function Agents() {
  const [workflows, setWorkflows] = useState<StaticWorkflow[]>([
    {
      id: "wf-1",
      title: "Pre-Meeting Brief Generation",
      description: "Autonomously scans your calendar 2 hours before meetings, compiles relevant project context, milestones, and health logs, and delivers a consolidated briefing note over Slack.",
      enabled: true,
    },
    {
      id: "wf-2",
      title: "Morning Daily Followups",
      description: "Runs daily at 9:00 AM IST to identify blocked or overdue tasks across all active projects, nudge assignees directly on Slack, and log status responses back to the dashboard.",
      enabled: true,
    },
  ]);

  const [adding, setAdding] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");

  function toggleWorkflow(id: string) {
    setWorkflows((wfs) =>
      wfs.map((w) => (w.id === id ? { ...w, enabled: !w.enabled } : w))
    );
  }

  function handleCreateWorkflow() {
    if (!newTitle.trim() || !newDesc.trim()) return;
    const newWf: StaticWorkflow = {
      id: `wf-${Date.now()}`,
      title: newTitle.trim(),
      description: newDesc.trim(),
      enabled: true,
    };
    setWorkflows((wfs) => [...wfs, newWf]);
    setNewTitle("");
    setNewDesc("");
    setAdding(false);
  }

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <div className="border-b border-zinc-200 pb-5 mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-zinc-900">Agents</h1>
          <p className="mt-1.5 text-sm text-zinc-500">
            Configure agent orchestration behaviors, trigger workflows, and review active system updates.
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
        {/* Left/Main Column: Workflows Orchestration */}
        <div className="lg:col-span-2 space-y-6">
          <div className="flex items-center justify-between border-b border-zinc-200 pb-3 mb-4">
            <h2 className="text-lg font-bold text-zinc-900">Automated Workflows</h2>
            <span className="text-xs text-zinc-400 font-mono uppercase tracking-wider">Demo Simulation</span>
          </div>

          <div className="space-y-4">
            {workflows.map((wf) => (
              <div
                key={wf.id}
                className="bg-white border border-zinc-200 rounded-xl p-6 shadow-sm flex items-start justify-between gap-6 transition-all"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3">
                    <h3 className="font-bold text-zinc-900 text-sm">
                      {wf.title}
                    </h3>
                    <span
                      className={`inline-block h-2 w-2 rounded-full ${
                        wf.enabled ? "bg-emerald-500" : "bg-zinc-300"
                      }`}
                    />
                  </div>
                  <p className="mt-2 text-xs text-zinc-500 leading-relaxed max-w-xl">
                    {wf.description}
                  </p>
                </div>

                <div className="shrink-0 flex items-center gap-3">
                  <button
                    onClick={() => toggleWorkflow(wf.id)}
                    className={`rounded-lg px-4 py-1.5 text-xs font-semibold transition-all border ${
                      wf.enabled
                        ? "bg-rose-50 border-rose-200 text-rose-700 hover:bg-rose-100"
                        : "bg-emerald-50 border-emerald-200 text-emerald-700 hover:bg-emerald-100"
                    }`}
                  >
                    {wf.enabled ? "Disable" : "Enable"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right Column: Dynamic System Updates */}
        <div className="lg:col-span-1">
          <UpdatesPanel />
        </div>
      </div>

      {/* Add New Workflow Modal */}
      {adding && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-950/40 backdrop-blur-sm">
          <div className="w-full max-w-md bg-white border border-zinc-200 rounded-xl p-6 shadow-xl flex flex-col gap-5">
            <div>
              <h3 className="text-base font-black text-zinc-950">Add New Workflow</h3>
              <p className="text-xs text-zinc-500 mt-1">Define static behaviors for demonstration purposes.</p>
            </div>

            <div className="space-y-3">
              <label className="block">
                <span className="text-xs font-semibold text-zinc-700 uppercase tracking-wide">Workflow Title</span>
                <input
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="e.g. Weekly Conflict Synthesis"
                  className="mt-1 w-full rounded-lg border border-zinc-200 px-3 py-2 text-xs focus:outline-none focus:border-zinc-950"
                />
              </label>

              <label className="block">
                <span className="text-xs font-semibold text-zinc-700 uppercase tracking-wide">Description</span>
                <textarea
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  placeholder="e.g. Scans active workspace conflicts and emails a summary of contradictions."
                  rows={3}
                  className="mt-1 w-full rounded-lg border border-zinc-200 px-3 py-2 text-xs focus:outline-none focus:border-zinc-950 resize-none"
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
                disabled={!newTitle.trim() || !newDesc.trim()}
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
