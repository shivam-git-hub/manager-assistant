import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import ProjectCard from "@/components/ProjectCard";
import CreateProjectModal from "@/components/CreateProjectModal";
import { getProjects, type ProjectSummary } from "@/lib/api";

// Projects grid (wireframe 3.png): Portfolios/Projects toggle, sort
// control, card grid, floating "+" to create. Portfolios and
// sort-by-health wait on their backend (portfolios page 4.png not built;
// health lands with the dream job) -- both render disabled rather than
// faked.
export default function Projects() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [creating, setCreating] = useState(false);
  const [sort, setSort] = useState<"name">("name");

  const load = useCallback(() => {
    getProjects()
      .then((all) => setProjects(all.filter((p) => p.kind === "team")))
      .catch(() => setProjects([]));
  }, []);
  useEffect(load, [load]);

  const sorted = [...projects].sort((a, b) =>
    sort === "name" ? a.name.localeCompare(b.name) : 0,
  );

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div className="inline-flex rounded-lg overflow-hidden border border-cardline">
          <button
            disabled
            title="Portfolios -- coming soon"
            className="px-5 py-2 text-sm font-bold bg-card text-inksoft cursor-not-allowed"
          >
            Portfolios
          </button>
          <button className="px-5 py-2 text-sm font-bold bg-nav text-white">Projects</button>
        </div>

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
          <p className="text-lg font-semibold text-ink">No projects yet</p>
          <p className="mt-1 text-sm">
            Create your first project and Pulse starts tracking it end to end.
          </p>
        </div>
      ) : (
        <div className="mt-6 grid gap-5 [grid-template-columns:repeat(auto-fill,minmax(16rem,1fr))]">
          {sorted.map((p) => (
            <ProjectCard key={p.id} project={p} onClick={() => navigate(`/projects/${p.id}`)} />
          ))}
        </div>
      )}

      <button
        onClick={() => setCreating(true)}
        aria-label="New project"
        className="fixed bottom-8 left-8 h-14 w-14 rounded-full bg-nav text-white text-3xl leading-none shadow-lg hover:bg-navdeep focus-visible:outline focus-visible:outline-2 focus-visible:outline-navdeep"
      >
        +
      </button>

      {creating && (
        <CreateProjectModal
          kind="team"
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
