import StatusFlower from "@/components/StatusFlower";
import { HEALTH_COLORS, NO_DATA_COLOR } from "@/constants";
import type { ProjectSummary } from "@/lib/api";

// Card per wireframes 2/3: title, recent-activity bullets, then the
// health | Blockers | Actions flower row. Activity/health/blockers all
// come from pipeline jobs that don't exist yet, so cards render their
// honest no-data state (grey flowers, "No activity yet") rather than
// dummy values.
export default function ProjectCard({
  project,
  onClick,
}: {
  project: ProjectSummary & { health?: string | null };
  onClick?: () => void;
}) {
  const healthColor = project.health ? HEALTH_COLORS[project.health] ?? NO_DATA_COLOR : NO_DATA_COLOR;

  const Wrapper = onClick ? "button" : "div";
  return (
    <Wrapper
      onClick={onClick}
      className={`w-64 shrink-0 rounded-xl border border-cardline bg-card px-5 py-4 text-center shadow-sm ${
        onClick ? "hover:shadow-md hover:border-nav/50 transition-shadow text-left" : ""
      }`}
    >
      <h3 className="font-bold text-ink text-lg text-center truncate" title={project.name}>
        {project.name}
      </h3>

      <p className="mt-2 text-xs text-inksoft min-h-8 text-center">
        {project.description ? (
          <span className="line-clamp-2">{project.description}</span>
        ) : (
          "No activity yet"
        )}
      </p>

      <div className="mt-3 flex items-end justify-center gap-5">
        <StatusFlower color={healthColor} title="health" />
        <StatusFlower color={NO_DATA_COLOR} title="blockers" />
        <StatusFlower color={NO_DATA_COLOR} title="actions" />
      </div>
      <div className="mt-1 text-[11px] tracking-wide text-inksoft text-center">
        health&nbsp; | &nbsp;Blockers&nbsp; | &nbsp;Actions
      </div>
    </Wrapper>
  );
}
