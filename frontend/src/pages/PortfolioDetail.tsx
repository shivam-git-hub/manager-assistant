import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import ProjectCard from "@/components/ProjectCard";
import {
  deletePortfolio,
  getPortfolio,
  getProjects,
  patchPortfolio,
  type Portfolio,
  type ProjectSummary,
} from "@/lib/api";

// Portfolio detail: editable name, delete, member-
// project grid with per-card remove + an add picker for projects not yet
// in this portfolio.
export default function PortfolioDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [allProjects, setAllProjects] = useState<ProjectSummary[]>([]);
  const [name, setName] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getPortfolio(id)
      .then((p) => {
        setPortfolio(p);
        setName(p.name);
      })
      .catch(() => navigate("/projects"));
    getProjects().then(setAllProjects).catch(() => setAllProjects([]));
  }, [id, navigate]);
  useEffect(load, [load]);

  if (!portfolio) return null;

  async function saveName() {
    const trimmed = name.trim();
    if (!trimmed || trimmed === portfolio!.name) {
      setName(portfolio!.name);
      return;
    }
    const updated = await patchPortfolio(portfolio!.id, { name: trimmed });
    setPortfolio(updated);
  }

  async function removeProject(projectId: string) {
    setBusy(true);
    try {
      const updated = await patchPortfolio(portfolio!.id, { remove_project_ids: [projectId] });
      setPortfolio(updated);
    } finally {
      setBusy(false);
    }
  }

  async function addProject(projectId: string) {
    setBusy(true);
    try {
      const updated = await patchPortfolio(portfolio!.id, { add_project_ids: [projectId] });
      setPortfolio(updated);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    await deletePortfolio(portfolio!.id);
    navigate("/projects");
  }

  const addable = allProjects.filter((p) => !portfolio.projects.some((pp) => pp.id === p.id));

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <p className="text-center font-bold text-ink text-xl">My Portfolios</p>

      <div className="mt-6 flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-extrabold text-ink">Portfolio Name</h1>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={saveName}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            className="mt-1 text-lg text-ink bg-transparent border-b border-transparent hover:border-cardline focus:border-nav focus:outline-none"
          />
        </div>
        <button
          onClick={() => setConfirmDelete(true)}
          className="rounded-md border-2 border-ink text-ink font-bold px-4 py-2 hover:bg-ink hover:text-white transition-colors"
        >
          Delete Portfolio
        </button>
      </div>

      {portfolio.projects.length === 0 ? (
        <div className="mt-16 text-center text-inksoft">
          <p className="text-lg font-semibold text-ink">No projects in this portfolio yet</p>
          <p className="mt-1 text-sm">Add one with the + button.</p>
        </div>
      ) : (
        <div className="mt-8 grid gap-5 [grid-template-columns:repeat(auto-fill,minmax(16rem,1fr))]">
          {portfolio.projects.map((p) => (
            <div key={p.id} className="relative">
              <button
                onClick={() => removeProject(p.id)}
                disabled={busy}
                aria-label={`Remove ${p.name} from portfolio`}
                title="Remove from portfolio"
                className="absolute top-2 right-2 z-10 text-inksoft hover:text-[#DD5454] disabled:opacity-50"
              >
                🗑
              </button>
              <ProjectCard project={p} onClick={() => navigate(`/projects/${p.id}/dashboard`)} />
            </div>
          ))}
        </div>
      )}

      <button
        onClick={() => setAdding(true)}
        aria-label="Add project to portfolio"
        className="fixed bottom-8 left-8 h-14 w-14 rounded-full bg-nav text-white text-3xl leading-none shadow-lg hover:bg-navdeep focus-visible:outline focus-visible:outline-2 focus-visible:outline-navdeep flex items-center justify-center"
      >
        +
      </button>

      {adding && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setAdding(false)}>
          <div
            className="w-full max-w-md rounded-xl bg-white shadow-2xl p-6"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="Add project to portfolio"
          >
            <h2 className="text-lg font-bold text-ink">Add a project</h2>
            {addable.length === 0 ? (
              <p className="mt-3 text-sm text-inksoft">
                Every project you can see is already in this portfolio.
              </p>
            ) : (
              <ul className="mt-3 max-h-72 overflow-y-auto rounded-md border border-cardline divide-y divide-cardline/60">
                {addable.map((p) => (
                  <li key={p.id}>
                    <button
                      onClick={() => addProject(p.id)}
                      disabled={busy}
                      className="w-full text-left px-3 py-2 text-sm text-ink hover:bg-card disabled:opacity-50"
                    >
                      <span className="font-semibold">{p.name}</span>
                      {p.description && <span className="block text-xs text-inksoft truncate">{p.description}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-5 flex justify-end">
              <button onClick={() => setAdding(false)} className="rounded-md px-4 py-2 text-sm font-semibold text-inksoft hover:bg-surface">
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmDelete && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setConfirmDelete(false)}>
          <div className="w-full max-w-sm rounded-xl bg-white shadow-2xl p-6" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <h3 className="font-bold text-ink">Delete {portfolio.name}?</h3>
            <p className="mt-2 text-sm text-inksoft">
              This removes the portfolio grouping. The projects themselves aren't affected.
            </p>
            <div className="mt-4 flex justify-end gap-3">
              <button onClick={() => setConfirmDelete(false)} className="rounded-md px-4 py-2 text-sm font-semibold text-inksoft hover:bg-surface">
                Cancel
              </button>
              <button onClick={handleDelete} className="rounded-md bg-[#DD5454] text-white px-4 py-2 text-sm font-semibold hover:brightness-110">
                Delete portfolio
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
