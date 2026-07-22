import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { PRIORITY_META, TASK_STATE_META } from "@/constants";
import {
  createProjectTask,
  deleteProject,
  approveEvent,
  dismissEvent,
  rejectEvent,
  getEmployees,
  getProject,
  getProjectEvents,
  getProjectInsights,
  getProjectTasks,
  patchProjectTask,
  type Employee,
  type EventItem,
  type ProjectDetail,
  type ProjectInsights,
  type ProjectTask,
  type TaskInput,
} from "@/lib/api";

// Project Dashboard (wireframe 6.png): team task table + My Tasks + the
// four insight panels. Updates / Blockers & Clarifications / Requests are
// events tagged to this project. Conflicts come from the project store.
// Requests get Approve/Reject (step 24 -- approving a request tied to a
// specific task also marks that task done); Updates/Blockers stay
// dismiss-only -- blocker/conflict resolve semantics are a separate,
// still-future step.

function Chip({ label, bg, fg }: { label: string; bg: string; fg: string }) {
  return (
    <span className="inline-block rounded-md px-3 py-1 text-xs font-bold whitespace-nowrap" style={{ background: bg, color: fg }}>
      {label}
    </span>
  );
}

function StateChip({ task }: { task: ProjectTask }) {
  const meta = TASK_STATE_META[task.schedule_state];
  const label =
    task.schedule_state === "overdue" ? `Overdue: ${task.days_overdue} Day${task.days_overdue > 1 ? "s" : ""}` : meta.label;
  return <Chip label={label} bg={meta.bg} fg={meta.fg} />;
}

function fmtDue(due: string | null): string {
  if (!due) return "—";
  return new Date(due).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

function TaskRow({
  task,
  isManager,
  onApprove,
  onEdit,
  depth = 0,
}: {
  task: ProjectTask;
  isManager: boolean;
  onApprove: (t: ProjectTask) => void;
  onEdit: (t: ProjectTask) => void;
  depth?: number;
}) {
  const pr = PRIORITY_META[task.priority];
  return (
    <>
      <tr className="border-b border-cardline/50">
        <td className="py-2 pr-3 text-xs text-inksoft align-top">{task.id.slice(0, 6)}</td>
        <td className="py-2 pr-3 align-top">
          <span className={`font-semibold text-ink text-sm ${depth ? "block pl-" + depth * 4 : ""}`} style={depth ? { paddingLeft: depth * 16 } : undefined}>
            {depth > 0 && <span className="text-inksoft">- </span>}
            {task.title}
          </span>
          {task.description && <span className="block text-xs text-inksoft" style={depth ? { paddingLeft: depth * 16 } : undefined}>{task.description}</span>}
        </td>
        <td className="py-2 pr-3 align-top">{pr && <Chip label={pr.label} bg={pr.bg} fg={pr.fg} />}</td>
        <td className="py-2 pr-3 align-top text-sm text-ink">
          {task.assignee_name ? (
            <span className="inline-block rounded-md bg-card border border-cardline px-2.5 py-1 text-xs font-semibold">
              {task.assignee_name}
            </span>
          ) : (
            <span className="text-inksoft text-xs">—</span>
          )}
        </td>
        <td className="py-2 pr-3 align-top text-sm text-ink whitespace-nowrap">{fmtDue(task.due)}</td>
        <td className="py-2 pr-3 align-top">
          <StateChip task={task} />
        </td>
        <td className="py-2 align-top">
          {isManager && (
            <div className="flex items-center gap-2">
              {task.status === "pending_approval" && (
                <button
                  onClick={() => onApprove(task)}
                  className="rounded-md bg-card border border-cardline text-nav text-xs font-semibold px-2.5 py-1 hover:bg-nav hover:text-white transition-colors"
                >
                  Approve
                </button>
              )}
              <button onClick={() => onEdit(task)} aria-label={`Edit ${task.title}`} className="text-inksoft hover:text-ink" title="Edit">
                ✎
              </button>
            </div>
          )}
        </td>
      </tr>
      {task.subtasks.map((s) => (
        <TaskRow key={s.id} task={s} isManager={isManager} onApprove={onApprove} onEdit={onEdit} depth={depth + 1} />
      ))}
    </>
  );
}

function TaskDialog({
  title,
  initial,
  employees,
  onSave,
  onClose,
}: {
  title: string;
  initial: Partial<ProjectTask>;
  employees: Employee[];
  onSave: (input: TaskInput) => Promise<void>;
  onClose: () => void;
}) {
  const [form, setForm] = useState<TaskInput>({
    title: initial.title ?? "",
    description: initial.description ?? "",
    priority: initial.priority ?? "medium",
    assignee_employee_id: initial.assignee_employee_id ?? "",
    due: initial.due ? initial.due.slice(0, 10) : "",
    status: initial.status,
  });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!form.title?.trim()) {
      setError("Give the task a title");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave({
        ...form,
        title: form.title.trim(),
        description: form.description || undefined,
        assignee_employee_id: form.assignee_employee_id || null,
        due: form.due ? `${form.due}T00:00:00` : null,
      });
      onClose();
    } catch {
      setError("Save failed -- try again");
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl bg-white shadow-2xl p-6" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <h3 className="font-bold text-ink">{title}</h3>
        <label className="block mt-3 text-sm font-semibold text-inksoft">
          Title
          <input
            autoFocus
            value={form.title ?? ""}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            className="mt-1 w-full rounded-md border border-cardline px-3 py-2 text-sm text-ink focus:outline-none focus:border-nav"
          />
        </label>
        <label className="block mt-3 text-sm font-semibold text-inksoft">
          Description
          <textarea
            value={form.description ?? ""}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            rows={2}
            className="mt-1 w-full rounded-md border border-cardline px-3 py-2 text-sm text-ink focus:outline-none focus:border-nav"
          />
        </label>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <label className="text-sm font-semibold text-inksoft">
            Priority
            <select
              value={form.priority}
              onChange={(e) => setForm({ ...form, priority: e.target.value })}
              className="mt-1 w-full rounded-md border border-cardline px-2 py-2 text-sm text-ink focus:outline-none focus:border-nav"
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </label>
          <label className="text-sm font-semibold text-inksoft">
            Due
            <input
              type="date"
              value={form.due ?? ""}
              onChange={(e) => setForm({ ...form, due: e.target.value })}
              className="mt-1 w-full rounded-md border border-cardline px-2 py-1.5 text-sm text-ink focus:outline-none focus:border-nav"
            />
          </label>
        </div>
        <label className="block mt-3 text-sm font-semibold text-inksoft">
          Assignee
          <select
            value={form.assignee_employee_id ?? ""}
            onChange={(e) => setForm({ ...form, assignee_employee_id: e.target.value })}
            className="mt-1 w-full rounded-md border border-cardline px-2 py-2 text-sm text-ink focus:outline-none focus:border-nav"
          >
            <option value="">Unassigned</option>
            {employees.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name} ({e.email})
              </option>
            ))}
          </select>
        </label>
        {initial.id && (
          <label className="block mt-3 text-sm font-semibold text-inksoft">
            Status
            <select
              value={form.status ?? "todo"}
              onChange={(e) => setForm({ ...form, status: e.target.value })}
              className="mt-1 w-full rounded-md border border-cardline px-2 py-2 text-sm text-ink focus:outline-none focus:border-nav"
            >
              <option value="todo">To do</option>
              <option value="in_progress">In progress</option>
              <option value="blocked">Blocked</option>
              <option value="done">Done</option>
              <option value="pending_approval">Pending approval</option>
            </select>
          </label>
        )}
        {error && <p className="mt-2 text-sm text-[#DD5454]">{error}</p>}
        <div className="mt-4 flex justify-end gap-3">
          <button onClick={onClose} className="rounded-md px-4 py-2 text-sm font-semibold text-inksoft hover:bg-surface">
            Cancel
          </button>
          <button onClick={save} disabled={saving} className="rounded-md bg-nav text-white px-4 py-2 text-sm font-semibold hover:bg-navdeep disabled:opacity-60">
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function InsightPanel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl bg-nav text-white p-5 min-h-56 flex flex-col">
      <h3 className="text-center font-bold">{title}</h3>
      <div className="mt-4 flex-1 text-sm space-y-3">{children}</div>
    </section>
  );
}

function EventList({
  events,
  empty,
  onDismiss,
  onApprove,
  onReject,
}: {
  events: EventItem[];
  empty: string;
  onDismiss?: (id: string) => void;
  onApprove?: (id: string) => void;
  onReject?: (id: string) => void;
}) {
  if (events.length === 0) return <p className="text-white/80">{empty}</p>;
  return (
    <>
      {events.map((e) => (
        <div key={e.id} className="group flex items-start gap-2">
          <p className="flex-1">{e.title}</p>
          {onApprove && onReject && (
            <div className="flex gap-2 opacity-0 group-hover:opacity-100">
              <button
                onClick={() => onApprove(e.id)}
                className="text-xs font-bold text-white/90 hover:text-white underline"
              >
                Approve
              </button>
              <button
                onClick={() => onReject(e.id)}
                className="text-xs font-bold text-white/90 hover:text-white underline"
              >
                Reject
              </button>
            </div>
          )}
          {onDismiss && (
            <button
              onClick={() => onDismiss(e.id)}
              aria-label={`Dismiss: ${e.title}`}
              className="opacity-0 group-hover:opacity-100 text-white/80 hover:text-white"
            >
              &times;
            </button>
          )}
        </div>
      ))}
    </>
  );
}

export default function ProjectDashboard() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [tasks, setTasks] = useState<ProjectTask[]>([]);
  const [myTasks, setMyTasks] = useState<ProjectTask[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [insights, setInsights] = useState<ProjectInsights | null>(null);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [editing, setEditing] = useState<ProjectTask | null>(null);
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback(() => {
    getProject(id).then(setProject).catch(() => navigate("/projects"));
    getProjectTasks(id)
      .then((r) => {
        setTasks(r.tasks);
        setMyTasks(r.my_tasks);
      })
      .catch(() => {});
    getProjectEvents(id).then((r) => setEvents(r.events)).catch(() => setEvents([]));
    getProjectInsights(id).then(setInsights).catch(() => setInsights(null));
    getEmployees().then(setEmployees).catch(() => setEmployees([]));
  }, [id, navigate]);
  useEffect(load, [load]);

  if (!project) return null;
  const isManager = project.is_manager;

  const updates = events.filter((e) => !["blocker", "clarification", "request"].includes(e.type));
  const blockers = events.filter((e) => ["blocker", "clarification"].includes(e.type));
  const requests = events.filter((e) => e.type === "request");

  async function approve(task: ProjectTask) {
    await patchProjectTask(id, task.id, { status: "todo" });
    load();
  }

  async function handleDismiss(eventId: string) {
    await dismissEvent(eventId);
    load();
  }

  async function handleApproveRequest(eventId: string) {
    await approveEvent(eventId);
    load();
  }

  async function handleRejectRequest(eventId: string) {
    await rejectEvent(eventId);
    load();
  }

  async function handleDelete() {
    await deleteProject(id);
    navigate("/projects");
  }

  const taskTable = (rows: ProjectTask[], showAssignee: boolean) => (
    <div className="overflow-x-auto">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="text-sm font-bold text-ink border-b border-cardline">
            <th className="py-2 pr-3 font-bold">TaskID</th>
            <th className="py-2 pr-3">Task</th>
            <th className="py-2 pr-3">Priority</th>
            {showAssignee && <th className="py-2 pr-3">Assigned</th>}
            <th className="py-2 pr-3">Due</th>
            <th className="py-2 pr-3">Status</th>
            <th className="py-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) =>
            showAssignee ? (
              <TaskRow key={t.id} task={t} isManager={isManager} onApprove={approve} onEdit={setEditing} />
            ) : (
              // My Tasks: same row shape minus the Assigned column -- render
              // via a stripped table for simplicity
              <TaskRow key={t.id} task={{ ...t, assignee_name: null }} isManager={isManager} onApprove={approve} onEdit={setEditing} />
            ),
          )}
        </tbody>
      </table>
    </div>
  );

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <p className="text-center font-bold text-ink text-xl">My Projects</p>

      <div className="mt-6 flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-extrabold text-ink">Project Name</h1>
          <p className="mt-1 text-lg text-ink">{project.name}</p>
        </div>
        <div className="flex gap-3">
          {isManager && (
            <button
              onClick={() => setConfirmDelete(true)}
              className="rounded-md border-2 border-ink text-ink font-bold px-4 py-2 hover:bg-ink hover:text-white transition-colors"
            >
              Delete Project
            </button>
          )}
          <button
            onClick={() => navigate(`/projects/${id}`)}
            className="rounded-md bg-nav text-white font-semibold px-5 py-2 hover:bg-navdeep"
          >
            View Project
          </button>
        </div>
      </div>

      <div className="mt-6 flex items-center gap-3">
        <h2 className="text-xl font-extrabold text-ink">Tasks</h2>
        {isManager && (
          <button
            onClick={() => setCreating(true)}
            aria-label="Add task"
            className="h-7 w-7 rounded-full bg-nav text-white text-lg leading-none hover:bg-navdeep"
          >
            +
          </button>
        )}
      </div>
      {tasks.length === 0 ? (
        <p className="mt-2 text-sm text-inksoft">
          No tasks yet{isManager ? " -- add the first with the + button" : ""}.
        </p>
      ) : (
        <div className="mt-3">{taskTable(tasks, true)}</div>
      )}

      <h2 className="mt-10 text-xl font-extrabold text-ink">My Tasks</h2>
      {myTasks.length === 0 ? (
        <p className="mt-2 text-sm text-inksoft">Nothing assigned to you in this project.</p>
      ) : (
        <div className="mt-3">{taskTable(myTasks, false)}</div>
      )}

      <div className="mt-10 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        <InsightPanel title="Updates">
          <EventList events={updates} empty="No updates for this project yet." onDismiss={handleDismiss} />
        </InsightPanel>
        <InsightPanel title="Blockers & Clarifications">
          <EventList events={blockers} empty="No blockers or clarification requests right now." onDismiss={handleDismiss} />
        </InsightPanel>
        <InsightPanel title="Requests">
          <EventList
            events={requests}
            empty="No open requests."
            onApprove={handleApproveRequest}
            onReject={handleRejectRequest}
          />
        </InsightPanel>
        <InsightPanel title="Conflicts">
          {!insights || insights.conflicts.length === 0 ? (
            <p className="text-white/80">No conflicts detected.</p>
          ) : (
            insights.conflicts.map((c) => (
              <p key={c.id}>
                {c.claim_a_ref} vs {c.claim_b_ref} ({c.severity})
              </p>
            ))
          )}
        </InsightPanel>
      </div>

      {creating && (
        <TaskDialog
          title="New task"
          initial={{}}
          employees={employees}
          onClose={() => setCreating(false)}
          onSave={async (input) => {
            await createProjectTask(id, input);
            load();
          }}
        />
      )}
      {editing && (
        <TaskDialog
          title="Edit task"
          initial={editing}
          employees={employees}
          onClose={() => setEditing(null)}
          onSave={async (input) => {
            await patchProjectTask(id, editing.id, input);
            load();
          }}
        />
      )}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setConfirmDelete(false)}>
          <div className="w-full max-w-sm rounded-xl bg-white shadow-2xl p-6" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <h3 className="font-bold text-ink">Delete {project.name}?</h3>
            <p className="mt-2 text-sm text-inksoft">
              This removes the project, its tasks, files, and history for everyone. It can't be undone.
            </p>
            <div className="mt-4 flex justify-end gap-3">
              <button onClick={() => setConfirmDelete(false)} className="rounded-md px-4 py-2 text-sm font-semibold text-inksoft hover:bg-surface">
                Cancel
              </button>
              <button onClick={handleDelete} className="rounded-md bg-[#DD5454] text-white px-4 py-2 text-sm font-semibold hover:brightness-110">
                Delete project
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
