// Central knobs for the Pulse.ai frontend. Visual/structural tokens
// (brand blues, card tints) live in tailwind.config.js; everything the
// app decides AT RUNTIME -- severity semantics, panel sizing, nav
// structure -- lives here so it can be tuned in one place.

export const APP_NAME = "Pulse.ai";

// ── Updates panel ────────────────────────────────────────
// Events at/below this severity stay out of the Updates panel (the
// backend also honors explicit promotes regardless of severity).
export const UPDATES_MIN_SEVERITY = 1;
// How many rows a home panel shows before "+N more..".
export const PANEL_PREVIEW_COUNT = 5;

// Severity 0-3 → label + color. Semantic colors (ok/attention/critical)
// are deliberately separate from the brand blue.
export const SEVERITY_META: Record<number, { label: string; color: string }> = {
  0: { label: "info", color: "#A0AEC0" },
  1: { label: "low", color: "#4CAF50" },
  2: { label: "attention", color: "#E9B949" },
  3: { label: "critical", color: "#DD5454" },
};

// Aggregate panel state when there is nothing to show.
export const ALL_CLEAR = { label: "all clear", color: "#4CAF50" };
// Flower color when a metric has no data yet (health before the dream
// job exists, blockers/actions before heartbeat exists).
export const NO_DATA_COLOR = "#C3CDD9";

export const HEALTH_COLORS: Record<string, string> = {
  green: "#4CAF50",
  yellow: "#E9B949",
  red: "#DD5454",
};

// ── Navigation ───────────────────────────────────────────
// Tabs without a path render as plain text until their pages exist.
export const NAV_TABS: { label: string; path?: string }[] = [
  { label: "Team" },
  { label: "Notes" },
  { label: "Plan My Day" },
  { label: "Workflows" },
  { label: "Projects", path: "/projects" }, // added on Shivam's ask -- missing from the wireframe
];

export const SIDEBAR_ITEMS: { label: string; path?: string }[] = [
  { label: "Settings" },
  { label: "Preferences" },
  { label: "Connectors", path: "/connectors" },
  { label: "Agents", path: "/agents" },
  { label: "Report Issue" },
];
