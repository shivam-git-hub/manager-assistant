import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import ProjectCard from "@/components/ProjectCard";
import CreateProjectModal from "@/components/CreateProjectModal";
import CreatePortfolioModal from "@/components/CreatePortfolioModal";
import { getPortfolios, getProjects, type Portfolio, type ProjectSummary } from "@/lib/api";

// Health bands, worst-first (a project with no data yet sorts last, not
// first -- an unscored project isn't "healthier" than a red one).
const HEALTH_RANK: Record<string, number> = { red: 0, yellow: 1, green: 2 };

// Projects/Portfolios grid (wireframes 3.png / 4.png): toggle, sort
// control, card grid, floating "+" to create. Sort-by-health now live --
// GET /api/projects carries a real health band per project (dream job's
// HealthLog), so this no longer needs the disabled placeholder.
export default function Projects() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<"projects" | "portfolios">("projects");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [creating, setCreating] = useState(false);
  const [sort, setSort] = useState<"name" | "health">("name");

  const load = useCallback(() => {
    getProjects()
      .then((all) => setProjects(all.filter((p) => p.kind === "team")))
      .catch(() => setProjects([]));
    getPortfolios()
      .then(setPortfolios)
      .catch(() => setPortfolios([]));
  }, []);
  useEffect(load, [load]);

  const sortedProjects = [...projects].sort((a, b) =>
    sort === "name"
      ? a.name.localeCompare(b.name)
      : (HEALTH_RANK[a.health ?? ""] ?? 3) - (HEALTH_RANK[b.health ?? ""] ?? 3),
  );
  const sortedPortfolios = [...portfolios].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div className="inline-flex rounded-lg overflow-hidden border border-cardline">
          <button
            onClick={() => setTab("portfolios")}
            className={`px-5 py-2 text-sm font-bold ${tab === "portfolios" ? "bg-nav text-white" : "bg-card text-inksoft"}`}
          >
            Portfolios
          </button>
          <button
            onClick={() => setTab("projects")}
            className={`px-5 py-2 text-sm font-bold ${tab === "projects" ? "bg-nav text-white" : "bg-card text-inksoft"}`}
          >
            Projects
          </button>
        </div>

        <label className="text-sm font-semibold text-ink">
          Sort by:{" "}
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as "name" | "health")}
            className="ml-1 rounded-md border border-cardline bg-white px-2 py-1 focus:outline-none focus:border-nav"
          >
            <option value="name">name</option>
            <option value="health">health (worst first)</option>
          </select>
        </label>
      </div>

      {tab === "projects" ? (
        sortedProjects.length === 0 ? (
          <div className="mt-16 text-center text-inksoft">
            <p className="text-lg font-semibold text-ink">No projects yet</p>
            <p className="mt-1 text-sm">
              Create your first project and Pulse starts tracking it end to end.
            </p>
          </div>
        ) : (
          <div className="mt-6 grid gap-5 [grid-template-columns:repeat(auto-fill,minmax(16rem,1fr))]">
            {sortedProjects.map((p) => (
              <ProjectCard key={p.id} project={p} onClick={() => navigate(`/projects/${p.id}/dashboard`)} />
            ))}
          </div>
        )
      ) : sortedPortfolios.length === 0 ? (
        <div className="mt-16 text-center text-inksoft">
          <p className="text-lg font-semibold text-ink">No portfolios yet</p>
          <p className="mt-1 text-sm">Group related projects into a portfolio to track them together.</p>
        </div>
      ) : (
        <div className="mt-6 grid gap-5 [grid-template-columns:repeat(auto-fill,minmax(16rem,1fr))]">
          {sortedPortfolios.map((p) => (
            <PortfolioCard key={p.id} portfolio={p} onClick={() => navigate(`/portfolios/${p.id}`)} />
          ))}
        </div>
      )}

      <button
        onClick={() => setCreating(true)}
        aria-label={tab === "projects" ? "New project" : "New portfolio"}
        className="fixed bottom-8 left-8 h-14 w-14 rounded-full bg-nav text-white text-3xl leading-none shadow-lg hover:bg-navdeep focus-visible:outline focus-visible:outline-2 focus-visible:outline-navdeep flex items-center justify-center"
      >
        +
      </button>

      {creating && tab === "projects" && (
        <CreateProjectModal
          kind="team"
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            load();
          }}
        />
      )}
      {creating && tab === "portfolios" && (
        <CreatePortfolioModal
          onClose={() => setCreating(false)}
          onCreated={(p) => {
            setCreating(false);
            load();
            navigate(`/portfolios/${p.id}`);
          }}
        />
      )}
    </main>
  );
}

// Portfolio cards are a distinct card shape from ProjectCard (name +
// member-project list, no per-portfolio flower row -- there's no single
// "portfolio health," each member project carries its own).
function PortfolioCard({ portfolio, onClick }: { portfolio: Portfolio; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-64 shrink-0 rounded-xl border border-cardline bg-card px-5 py-4 text-center shadow-sm hover:shadow-md hover:border-nav/50 transition-shadow text-left"
    >
      <h3 className="font-bold text-ink text-lg text-center truncate" title={portfolio.name}>
        {portfolio.name}
      </h3>
      <p className="mt-2 text-xs text-inksoft min-h-8 text-center">
        {portfolio.projects.length === 0 ? (
          "No projects yet"
        ) : (
          <span className="line-clamp-2">{portfolio.projects.map((p) => p.name).join(", ")}</span>
        )}
      </p>
      <div className="mt-3 text-[11px] tracking-wide text-inksoft text-center">
        {portfolio.projects.length} project{portfolio.projects.length === 1 ? "" : "s"}
      </div>
    </button>
  );
}
