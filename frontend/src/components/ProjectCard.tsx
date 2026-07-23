import StatusFlower from "@/components/StatusFlower";
import { ALL_CLEAR, HEALTH_COLORS, NO_DATA_COLOR, SEVERITY_META } from "@/constants";
import type { ProjectSummary } from "@/lib/api";

// Card per wireframes 2/3: title, recent-activity bullets, then the
// health | Blockers | Actions flower row. health comes from the dream
// job's HealthLog; blockers/actions come from open heartbeat-produced
// Events tagged to this project (app.api.projects_registry._project_card_stats)
// -- grey/no-data is now only shown for genuinely pipeline-untouched
// projects (no dream tick has run yet), not as a permanent placeholder.
export default function ProjectCard({
  project,
  onClick,
}: {
  project: ProjectSummary;
  onClick?: () => void;
}) {
  const healthColor = project.health ? HEALTH_COLORS[project.health] ?? NO_DATA_COLOR : NO_DATA_COLOR;
  const blockersColor = project.blockers_count > 0 ? SEVERITY_META[3].color : ALL_CLEAR.color;
  const actionsColor = project.actions_count > 0 ? SEVERITY_META[2].color : ALL_CLEAR.color;

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
        <StatusFlower color={healthColor} title={`health: ${project.health ?? "no data yet"}`} />
        <StatusFlower color={blockersColor} title={`blockers: ${project.blockers_count}`} />
        <StatusFlower color={actionsColor} title={`actions needed: ${project.actions_count}`} />
      </div>
      <div className="mt-1 text-[11px] tracking-wide text-inksoft text-center">
        health&nbsp; | &nbsp;Blockers&nbsp; | &nbsp;Actions
      </div>
    </Wrapper>
  );
}
