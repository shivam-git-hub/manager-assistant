import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import CreateProjectModal from "@/components/CreateProjectModal";
import { getProjects, type ProjectSummary } from "@/lib/api";

const HEALTH_LABEL: Record<string, { text: string; bg: string; textCol: string }> = {
  green: { text: "Healthy", bg: "bg-emerald-500", textCol: "text-emerald-700" },
  yellow: { text: "Attention", bg: "bg-amber-500", textCol: "text-amber-700" },
  red: { text: "Critical", bg: "bg-rose-500", textCol: "text-rose-700" },
};

export default function Projects() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    getProjects()
      .then((all) => setProjects(all.filter((p) => p.kind === "team")))
      .catch(() => setProjects([]));
  }, []);
  useEffect(load, [load]);

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <div className="flex items-center justify-between border-b border-zinc-200 pb-5 mb-8">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-zinc-900">Projects</h1>
          <p className="mt-1.5 text-sm text-zinc-500">
            Monitor and coordinate active workspace workstreams.
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="rounded-lg bg-zinc-950 hover:bg-zinc-900 text-white font-medium text-xs px-4 py-2.5 transition-colors shadow-sm"
        >
          New Project
        </button>
      </div>

      {projects.length === 0 ? (
        <div className="rounded-xl border border-dashed border-zinc-200 bg-white p-12 text-center">
          <p className="text-sm font-semibold text-zinc-900">No active projects</p>
          <p className="mt-1 text-xs text-zinc-500">
            Create a new team project to get started tracking context end-to-end.
          </p>
        </div>
      ) : (
        <div className="bg-white border border-zinc-200 rounded-xl overflow-hidden shadow-sm divide-y divide-zinc-100">
          {projects.map((p) => {
            const hInfo = p.health ? HEALTH_LABEL[p.health] : { text: "No Data", bg: "bg-zinc-400", textCol: "text-zinc-500" };
            return (
              <div
                key={p.id}
                onClick={() => navigate(`/projects/${p.id}/dashboard`)}
                className="flex items-center justify-between p-6 hover:bg-zinc-50 transition-colors cursor-pointer group"
              >
                <div className="flex-1 min-w-0 pr-6">
                  <div className="flex items-center gap-3">
                    <h3 className="font-bold text-zinc-900 text-base group-hover:text-zinc-950">
                      {p.name}
                    </h3>
                    <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-zinc-100 border border-zinc-200">
                      <span className={`h-1.5 w-1.5 rounded-full ${hInfo.bg}`} />
                      <span className={`text-[10px] font-semibold tracking-wide uppercase ${hInfo.textCol}`}>
                        {hInfo.text}
                      </span>
                    </div>
                  </div>
                  <p className="mt-1.5 text-xs text-zinc-500 max-w-2xl leading-relaxed">
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
                  <svg
                    className="w-4 h-4 text-zinc-400 group-hover:text-zinc-600 transition-colors ml-2"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth="2.5"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                </div>
              </div>
            );
          })}
        </div>
      )}

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
