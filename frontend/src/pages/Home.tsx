import { useCallback, useEffect, useState } from "react";
import CreateProjectModal from "@/components/CreateProjectModal";
import {
  ALL_CLEAR,
  SEVERITY_META,
} from "@/constants";
import {
  createTodo,
  deleteTodo,
  getProjects,
  getTodos,
  patchTodo,
  type ProjectSummary,
  type TodoItem,
  type Manager,
} from "@/lib/api";

const HEALTH_LABEL: Record<string, { text: string; bg: string; textCol: string }> = {
  green: { text: "Healthy", bg: "bg-emerald-500", textCol: "text-emerald-700" },
  yellow: { text: "Attention", bg: "bg-amber-500", textCol: "text-amber-700" },
  red: { text: "Critical", bg: "bg-rose-500", textCol: "text-rose-700" },
};

// ── Shared panel chrome (TODOs) ──

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
    <section id={id} className="flex flex-col rounded-xl border border-zinc-200 bg-white overflow-hidden shadow-sm">
      <div className="border-b border-zinc-100 bg-zinc-50/50 px-5 py-3.5 flex items-center justify-between">
        <h3 className="font-bold text-zinc-950 text-sm">{title}</h3>
        <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-zinc-100 border border-zinc-200">
          <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: status.color }} />
          <span className="text-[10px] font-bold tracking-wide uppercase text-zinc-500">
            {status.label}
          </span>
        </div>
      </div>
      <div className="p-5 flex-1">
        {children}
      </div>
    </section>
  );
}

// ── TODOs ──

function fmtDue(due: string | null): string {
  if (!due) return "nil";
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

  const status = todos.length > 0 ? SEVERITY_META[2] : ALL_CLEAR;

  return (
    <Panel title="TODOs" status={status} id="todos">
      {todos.length === 0 ? (
        <p className="text-xs text-zinc-500 py-4">Nothing on your list. Add your first todo below.</p>
      ) : (
        <ul className="space-y-3 max-h-[300px] overflow-y-auto pr-1">
          {todos.map((todo) => (
            <li key={todo.id} className="group flex items-start gap-2.5 text-xs text-zinc-700 leading-normal">
              <input
                type="checkbox"
                onChange={() => handleDone(todo)}
                aria-label={`Mark done: ${todo.text}`}
                className="mt-0.5 rounded border-zinc-300 text-zinc-950 focus:ring-zinc-950"
              />
              <span className="font-medium flex-1 min-w-0 text-zinc-800">
                {todo.text}
                <span className="text-zinc-400 font-normal"> | {fmtDue(todo.due)}</span>
              </span>
              <button
                onClick={() => handleDelete(todo)}
                aria-label={`Delete: ${todo.text}`}
                className="opacity-0 group-hover:opacity-100 text-zinc-400 hover:text-zinc-600 transition-opacity px-1"
              >
                &times;
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex flex-col gap-2">
        <input
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="Add a todo…"
          className="w-full rounded-lg border border-zinc-200 px-3 py-1.5 text-xs focus:outline-none focus:border-zinc-950"
        />
        <div className="flex gap-2">
          <input
            type="date"
            value={newDue}
            onChange={(e) => setNewDue(e.target.value)}
            aria-label="Due date"
            className="flex-1 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-500 focus:outline-none focus:border-zinc-950"
          />
          <button
            onClick={handleAdd}
            className="rounded-lg bg-zinc-950 text-white text-xs font-semibold px-4 hover:bg-zinc-900 transition-colors"
          >
            Add
          </button>
        </div>
      </div>
    </Panel>
  );
}

// ── Projects List ──

function ProjectsSection({ projects, onNew }: { projects: ProjectSummary[]; onNew: () => void }) {
  return (
    <section className="flex flex-col">
      <div className="flex items-center justify-between border-b border-zinc-200 pb-3.5 mb-6">
        <h2 className="text-lg font-bold text-zinc-900">Projects</h2>
        <button
          onClick={onNew}
          className="rounded-lg bg-zinc-950 hover:bg-zinc-900 text-white text-xs font-semibold px-4 py-2 transition-colors shadow-sm"
        >
          New Project
        </button>
      </div>

      {projects.length === 0 ? (
        <div className="rounded-xl border border-dashed border-zinc-200 bg-white p-10 text-center">
          <p className="text-sm font-semibold text-zinc-900">No active projects</p>
          <p className="mt-1 text-xs text-zinc-500">Create a new team project to get started.</p>
        </div>
      ) : (
        <div className="bg-white border border-zinc-200 rounded-xl overflow-hidden shadow-sm divide-y divide-zinc-100">
          {projects.map((p) => {
            const hInfo = p.health ? HEALTH_LABEL[p.health] : { text: "No Data", bg: "bg-zinc-400", textCol: "text-zinc-500" };
            return (
              <div
                key={p.id}
                className="flex items-center justify-between p-5"
              >
                <div className="flex-1 min-w-0 pr-6">
                  <div className="flex items-center gap-3">
                    <h3 className="font-bold text-zinc-900 text-sm truncate">
                      {p.name}
                    </h3>
                    <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-zinc-100 border border-zinc-200 shrink-0">
                      <span className={`h-1.5 w-1.5 rounded-full ${hInfo.bg}`} />
                      <span className={`text-[10px] font-semibold tracking-wide uppercase ${hInfo.textCol}`}>
                        {hInfo.text}
                      </span>
                    </div>
                  </div>
                  <p className="mt-1 text-xs text-zinc-500 truncate max-w-xl">
                    {p.description || "No project description provided."}
                  </p>
                </div>

                <div className="flex items-center gap-4 text-xs font-medium text-zinc-400 shrink-0">
                  <span className={p.blockers_count > 0 ? "text-rose-600 font-semibold" : ""}>
                    {p.blockers_count} blocker{p.blockers_count === 1 ? "" : "s"}
                  </span>
                  <span className="text-zinc-300">•</span>
                  <span className={p.actions_count > 0 ? "text-amber-600 font-semibold" : ""}>
                    {p.actions_count} action{p.actions_count === 1 ? "" : "s"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// ── Page ──

export default function Home({ manager }: { manager: Manager }) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [creating, setCreating] = useState(false);

  const loadProjects = useCallback(() => {
    getProjects()
      .then((all) => setProjects(all.filter((p) => p.kind === "team")))
      .catch(() => setProjects([]));
  }, []);
  useEffect(loadProjects, [loadProjects]);

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <p className="sr-only">Signed in as {manager.name}</p>

      <div className="border-b border-zinc-200 pb-5 mb-8">
        <h1 className="text-3xl font-black tracking-tight text-zinc-900">Dashboard</h1>
        <p className="mt-1.5 text-sm text-zinc-500">
          Welcome back, {manager.name}. Here is your workspace status overview.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 items-start">
        <div className="lg:col-span-2">
          <ProjectsSection projects={projects} onNew={() => setCreating(true)} />
        </div>
        <div className="lg:col-span-1">
          <TodosPanel />
        </div>
      </div>

      {creating && (
        <CreateProjectModal
          kind="team"
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            loadProjects();
          }}
        />
      )}
    </main>
  );
}
