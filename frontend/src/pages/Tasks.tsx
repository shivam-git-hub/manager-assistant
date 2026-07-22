import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import ProjectCard from "@/components/ProjectCard";
import CreateProjectModal from "@/components/CreateProjectModal";
import { getProjects, type ProjectSummary } from "@/lib/api";

// Tasks grid -- mirrors Projects.tsx but scoped to personal projects
// (kind="personal", Home's "Tasks" rail concept -- see CLAUDE.md: "Tasks =
// personal projects"). Added alongside a Tasks nav tab so tasks have a
// dedicated grid the same way team projects do, instead of only living in
// Home's card rail.
export default function Tasks() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<ProjectSummary[]>([]);
  const [creating, setCreating] = useState(false);
  const [sort, setSort] = useState<"name">("name");

  const load = useCallback(() => {
    getProjects()
      .then((all) => setTasks(all.filter((p) => p.kind === "personal")))
      .catch(() => setTasks([]));
  }, []);
  useEffect(load, [load]);

  const sorted = [...tasks].sort((a, b) =>
    sort === "name" ? a.name.localeCompare(b.name) : 0,
  );

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <div className="flex items-center justify-between flex-wrap gap-4">
        <h1 className="text-2xl font-extrabold text-ink">Tasks</h1>

        <label className="text-sm font-semibold text-ink">
          Sort by:{" "}
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as "name")}
            className="ml-1 rounded-md border border-cardline bg-white px-2 py-1 focus:outline-none focus:border-nav"
          >
            <option value="name">name</option>
            <option value="health" disabled>
              health (available once health scoring is live)
            </option>
          </select>
        </label>
      </div>

      {sorted.length === 0 ? (
        <div className="mt-16 text-center text-inksoft">
          <p className="text-lg font-semibold text-ink">No tasks yet</p>
          <p className="mt-1 text-sm">Create your first task and Pulse starts tracking it end to end.</p>
        </div>
      ) : (
        <div className="mt-6 grid gap-5 [grid-template-columns:repeat(auto-fill,minmax(16rem,1fr))]">
          {sorted.map((t) => (
            <ProjectCard key={t.id} project={t} onClick={() => navigate(`/projects/${t.id}/dashboard`)} />
          ))}
        </div>
      )}

      <button
        onClick={() => setCreating(true)}
        aria-label="New task"
        className="fixed bottom-8 left-8 h-14 w-14 rounded-full bg-nav text-white text-3xl leading-none shadow-lg hover:bg-navdeep focus-visible:outline focus-visible:outline-2 focus-visible:outline-navdeep flex items-center justify-center"
      >
        +
      </button>

      {creating && (
        <CreateProjectModal
          kind="personal"
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            load();
          }}
        />
      )}
    </main>
  );
}
