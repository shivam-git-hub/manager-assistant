# SPEC: Simplified & Ultra-Professional Minimalist UI Refactoring

This specification outlines the redesign of the Manager Assistant ("Harry") frontend (`app/static/index.html`) into an ultra-professional, minimalist SaaS portal (similar to Vercel and Linear). 

---

## 1. Core Visual Design & Theme
- **Color Palette:** High-contrast greyscale base.
  - Backgrounds: Clean white `bg-white` and cool light-zinc `bg-zinc-50`.
  - Sidebar: Premium dark zinc `bg-zinc-950` with `text-zinc-400`.
  - Borders: Thin, sharp borders `border-zinc-200` (light) and `border-zinc-800` (dark).
- **Accents:** Modern minimal pastel accents (emerald for Green/Success, amber for Yellow/Warning, rose for Red/Danger, indigo for active selection).
- **Typography:** Sleek sans-serif (`Inter`) with elegant monospace (`font-mono`) for tools/code/system logs.
- **Components:** Completely remove all decorative, cartoonish flower SVGs, heavy grid tiles, and cluttered panels.

---

## 2. Layout Structure
The interface is split into a left sidebar, top header, and main content area.

### A. Left Sidebar Navigation (`bg-zinc-950`)
- Slim, elegant left panel containing **exactly two navigation links**:
  1. **Agent View** (ChatGPT-style conversation view) - Default active tab.
  2. **Dashboard** (Minimal projects & ToDos flat view)
- Sidebar links are flat, minimal list items with clean hover and active indigo border-left effects.
- Branding at the top: **Harry** / **Pulse.ai** with a subtle subtitle "Manager Assistant".

### B. Top Navigation Bar (Header)
- Clean, thin header with bottom border.
- **No clock or time controls:** Completely remove the simulated time widget, clock date/time inputs, set/advance/reset buttons. Keep the background silent sync to `/api/time` ticking in code to avoid breaking API dependencies, but show **zero** time elements in the UI.
- Contains:
  - Branding/Logo.
  - **Acting-as Profile Selector:** Dropdown switcher allowing the user to select the active team member profile they are impersonating.
  - **Sleek Hamburger Button:** Toggles an absolutely positioned floating dropdown.

### C. Sleek Hamburger Drop-down Menu
- Absolute floating dropdown menu from the top-right hamburger icon.
- Contains exactly two actions:
  1. **Connectors Hub** (triggers the mockup integrations modal).
  2. **Logout** (red-accented action).

---

## 3. Main Views

### View 1: Agent View (ChatGPT Conversational Chat)
- Active by default (`activeMainTab = 'agent'`).
- Clean conversational chat interface with left/right aligned speech bubbles:
  - **User (Right):** Sleek, dark zinc speech bubbles (`bg-zinc-900 text-white`) aligned to the right.
  - **Harry (Left):** Soft gray speech bubbles (`bg-zinc-100 text-zinc-800`) aligned to the left.
  - **Tool Traces:** Rendered inside a clean HTML `<details>` disclosure dropdown using monospace `font-mono` text. Displays exactly which tools Harry executed under the hood for that response.
- **Empty State (Welcome Screen):** Minimal, centered title "How can I help you today?" with a small peaceful description when there are no messages.
- **Floating bottom input bar:** Minimalist text area with an absolute-positioned send icon button and a subtle hint: "Press Enter to send".

### View 2: Flat Projects & ToDos Dashboard
- Flexible two-column layout with no tiles or cards:
- **Column 1: Projects**
  - Header: "Projects" (styled with flat `text-xs font-bold uppercase tracking-wider text-zinc-500`).
  - Lists **exactly two** active team projects (e.g. Phoenix and Atlas).
  - Format: `Project Name: Milestones`.
  - Next to the project name, show a simple color-coded health badge (Green, Yellow, Red pill shape).
  - Milestones text is fetched dynamically from `/api/tasks?project_id=...` or `/api/projects/{id}/tasks` and rendered as a comma-separated milestones string (e.g., `"Draft mocks (Completed), Schema Contract (Pending)"`).
  - Completely hides individual task grids, team lists, or complex subtask panels from the main dashboard viewport.
- **Column 2: ToDos**
  - Header: "ToDos"
  - Interactive personal todo/checklist manager for the manager (Shivam) himself.
  - Allows:
    - Adding a todo dynamically via an input field on press of `Enter`.
    - Checking/unchecking items (completed items get struck-through: `line-through text-zinc-400`).
    - Deleting items using a clean delete button.
  - State is watched and persisted automatically to browser `localStorage` ("harry_todos") so it survives refreshes.
  - Pre-populated on first-run with:
    1. Review Bob's payment schema document
    2. Prep for Phoenix Go/No-Go alignment meeting
    3. Check Safari checkout bug logs

---

## 4. Connectors Hub (Integrations Modal)
To keep the main presentation dashboard completely pristine and professional, all integration simulators and setup forms are consolidated inside a beautiful modal.
- Triggered by clicking **"Connectors Hub"** from the hamburger dropdown.
- Contains a Tabbed Interface to switch between the mock sandboxes:
  - **Slack Sandbox:** Auburn-themed high-fidelity Slack channel and DM simulator (to send mock webhook events).
  - **Outlook Sandbox:** Corporate blue Microsoft Outlook email reader and composer (to ingest mock email events).
  - **Team Roster:** Roster profile creation form and employee deletion directory.
  - **Direct Chat API Hook:** Standard direct API log stream or diagnostic direct chat interface.

---

## 5. Verification Gateways
- **Build & Linter:** Auto-runs and must yield 100% successful syntax checks.
- **Pytest:** Runs and passes all backend tests (excluding pre-existing `test_04_followup_lifecycle` mismatch).
