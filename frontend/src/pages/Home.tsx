import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import StatusFlower from "@/components/StatusFlower";
import ProjectCard from "@/components/ProjectCard";
import CreateProjectModal from "@/components/CreateProjectModal";
import {
  ALL_CLEAR,
  PANEL_PREVIEW_COUNT,
  SEVERITY_META,
  UPDATES_MIN_SEVERITY,
} from "@/constants";
import {
  createTodo,
  deleteTodo,
  dismissEvent,
  getEvents,
  getProjects,
  getTodos,
  patchTodo,
  type EventsResponse,
  type Manager,
  type ProjectSummary,
  type TodoItem,
} from "@/lib/api";

// ── Shared panel chrome (Updates / TODOs, wireframe 2.png) ──

function Panel({
  title,
  status,
  children,
  id,
}: {
  title: string;
  status: { label: string; color: string };
  children: React.ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className="flex flex-col scroll-mt-20">
      <div className="rounded-t-lg bg-nav text-white text-center font-bold py-2 text-[15px]">
        {title}
      </div>
      <div className="flex-1 rounded-b-lg border border-t-0 border-cardline bg-white px-5 py-4 flex gap-4">
        <div className="flex-1 min-w-0">{children}</div>
        <div className="flex flex-col items-center justify-center gap-1 w-20 shrink-0">
          <StatusFlower color={status.color} size={34} title={`status: ${status.label}`} />
          <span className="text-[11px] italic text-inksoft whitespace-nowrap">
            status: {status.label}
          </span>
        </div>
      </div>
    </section>
  );
}

// ── Updates ──────────────────────────────────────────────

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
    <Panel title="Updates" status={status}>
      {events.length === 0 ? (
        <p className="text-sm text-inksoft py-2">
          No updates yet. Once your accounts are connected, Pulse surfaces what needs your
          attention here.
        </p>
      ) : (
        <>
          <ul className="space-y-1.5">
            {shown.map((ev) => (
              <li key={ev.id} className="group flex items-start gap-2 text-sm text-ink">
                <span
                  className="mt-1.5 h-2 w-2 rounded-full shrink-0"
                  style={{ background: SEVERITY_META[ev.severity]?.color }}
                  aria-label={SEVERITY_META[ev.severity]?.label}
                />
                <span className="font-semibold flex-1 min-w-0">{ev.title}</span>
                <button
                  onClick={() => handleDismiss(ev.id)}
                  aria-label={`Dismiss: ${ev.title}`}
                  className="opacity-0 group-hover:opacity-100 text-inksoft hover:text-ink px-1"
                >
                  &times;
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-2 flex items-center gap-3 text-xs text-inksoft">
            {hidden > 0 && <span>+{hidden} more..</span>}
            {events.length > PANEL_PREVIEW_COUNT && (
              <button onClick={() => setExpanded(!expanded)} className="underline hover:text-ink">
                {expanded ? "Show less" : "View All"}
              </button>
            )}
          </div>
        </>
      )}
    </Panel>
  );
}

// ── TODOs ────────────────────────────────────────────────

function fmtDue(due: string | null): string {
  if (!due) return "nill";
  const d = new Date(due);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "2-digit" });
}

function TodosPanel() {
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [newText, setNewText] = useState("");
  const [newDue, setNewDue] = useState("");

  const load = useCallback(() => {
    getTodos("open").then(setTodos).catch(() => setTodos([]));
  }, []);
  useEffect(load, [load]);

  async function handleAdd() {
    if (!newText.trim()) return;
    await createTodo(newText.trim(), newDue ? `${newDue}T00:00:00` : undefined);
    setNewText("");
    setNewDue("");
    load();
  }

  async function handleDone(todo: TodoItem) {
    await patchTodo(todo.id, { status: "done" });
    load();
  }

  async function handleDelete(todo: TodoItem) {
    await deleteTodo(todo.id);
    load();
  }

  // TODOs are user-curated, so their aggregate stays a steady "attention"
  // yellow while anything is open (wireframe 2), green when clear.
  const status = todos.length > 0 ? SEVERITY_META[2] : ALL_CLEAR;

  return (
    <Panel title="TODOs" status={status} id="todos">
      {todos.length === 0 ? (
        <p className="text-sm text-inksoft py-2">Nothing on your list. Add your first todo below.</p>
      ) : (
        <ul className="space-y-1.5">
          {todos.map((todo) => (
            <li key={todo.id} className="group flex items-start gap-2 text-sm text-ink">
              <input
                type="checkbox"
                onChange={() => handleDone(todo)}
                aria-label={`Mark done: ${todo.text}`}
                className="mt-0.5"
              />
              <span className="font-semibold flex-1 min-w-0">
                {todo.text}
                <span className="text-inksoft font-normal"> | {fmtDue(todo.due)}</span>
              </span>
              <button
                onClick={() => handleDelete(todo)}
                aria-label={`Delete: ${todo.text}`}
                className="opacity-0 group-hover:opacity-100 text-inksoft hover:text-ink px-1"
              >
                &times;
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex gap-2">
        <input
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="Add a todo…"
          className="flex-1 min-w-0 rounded-md border border-cardline px-2.5 py-1.5 text-sm focus:outline-none focus:border-nav"
        />
        <input
          type="date"
          value={newDue}
          onChange={(e) => setNewDue(e.target.value)}
          aria-label="Due date"
          className="rounded-md border border-cardline px-2 py-1.5 text-sm text-inksoft focus:outline-none focus:border-nav"
        />
        <button
          onClick={handleAdd}
          className="rounded-md bg-nav text-white text-sm font-semibold px-3.5 hover:bg-navdeep"
        >
          Add
        </button>
      </div>
    </Panel>
  );
}

// ── Card rails ───────────────────────────────────────────

function CardRail({
  title,
  projects,
  emptyText,
  onNew,
  newLabel,
}: {
  title: string;
  projects: ProjectSummary[];
  emptyText: string;
  onNew: () => void;
  newLabel: string;
}) {
  const navigate = useNavigate();
  const navigateToProject = (id: string) => navigate(`/projects/${id}/dashboard`);
  return (
    <section className="mt-8">
      <h2 className="text-2xl font-extrabold text-ink">{title}</h2>
      <div className="mt-3 flex gap-5 overflow-x-auto pb-2">
        {projects.map((p) => (
          <ProjectCard key={p.id} project={p} onClick={() => navigateToProject(p.id)} />
        ))}
        <button
          onClick={onNew}
          className="w-64 shrink-0 rounded-xl border-2 border-dashed border-cardline bg-white/60 px-5 py-4 text-inksoft hover:border-nav hover:text-nav transition-colors flex flex-col items-center justify-center gap-1 min-h-40"
        >
          <span className="text-3xl leading-none">+</span>
          <span className="text-sm font-semibold">{newLabel}</span>
          {projects.length === 0 && <span className="text-xs text-center">{emptyText}</span>}
        </button>
      </div>
    </section>
  );
}

// ── Page ─────────────────────────────────────────────────

export default function Home({ manager }: { manager: Manager }) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [creating, setCreating] = useState<"team" | "personal" | null>(null);

  const loadProjects = useCallback(() => {
    getProjects().then(setProjects).catch(() => setProjects([]));
  }, []);
  useEffect(loadProjects, [loadProjects]);

  const tasks = projects.filter((p) => p.kind === "personal");
  const teamProjects = projects.filter((p) => p.kind === "team");

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <p className="sr-only">Signed in as {manager.name}</p>

      <div className="grid gap-6 lg:grid-cols-2 items-stretch">
        <UpdatesPanel />
        <TodosPanel />
      </div>

      <CardRail
        title="Tasks"
        projects={tasks}
        emptyText="Personal work you want Pulse to track"
        onNew={() => setCreating("personal")}
        newLabel="New Task"
      />
      <CardRail
        title="Projects"
        projects={teamProjects}
        emptyText="Team projects you manage or belong to"
        onNew={() => setCreating("team")}
        newLabel="New Project"
      />

      {creating && (
        <CreateProjectModal
          kind={creating}
          onClose={() => setCreating(null)}
          onCreated={() => {
            setCreating(null);
            loadProjects();
          }}
        />
      )}
    </main>
  );
}
