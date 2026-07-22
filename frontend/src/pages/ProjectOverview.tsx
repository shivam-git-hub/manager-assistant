import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  addManualMessage,
  getEmployees,
  getProjectDoc,
  getProject,
  getVaultFiles,
  patchProjectMembers,
  putProjectDoc,
  uploadVaultFile,
  vaultDownloadUrl,
  type Employee,
  type ProjectDetail,
  type VaultFile,
} from "@/lib/api";

// Project Overview (wireframe 7.png): Overview / Milestones / KPIs from
// project.md, Timeline (dream-job output -- honest empty state for now),
// Quick Links: Team, Project Vault, Add MoM. "Project Dashboard" button →
// the tasks/panels page (6.png).

// project.md section split: "## Heading" boundaries; the preamble (title +
// description) is dropped -- name/description already render from the
// registry row.
function mdSections(md: string): Record<string, string> {
  const sections: Record<string, string> = {};
  const parts = md.split(/^## /m).slice(1);
  for (const part of parts) {
    const nl = part.indexOf("\n");
    const heading = (nl === -1 ? part : part.slice(0, nl)).trim();
    sections[heading] = nl === -1 ? "" : part.slice(nl + 1).trim();
  }
  return sections;
}

function SectionText({ text, empty }: { text: string; empty: string }) {
  if (!text) return <p className="text-sm text-inksoft italic">{empty}</p>;
  return <div className="text-sm text-ink whitespace-pre-wrap">{text}</div>;
}

function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-xl bg-white shadow-2xl p-6 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="flex items-center justify-between">
          <h3 className="font-bold text-ink">{title}</h3>
          <button onClick={onClose} aria-label="Close" className="text-inksoft hover:text-ink text-xl leading-none">
            &times;
          </button>
        </div>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}

export default function ProjectOverview() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [projectMd, setProjectMd] = useState("");
  const [editing, setEditing] = useState(false);
  const [draftMd, setDraftMd] = useState("");
  const [dialog, setDialog] = useState<"team" | "vault" | "mom" | null>(null);
  const [vaultFiles, setVaultFiles] = useState<VaultFile[]>([]);
  const [momText, setMomText] = useState("");
  const [momSubject, setMomSubject] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [addingId, setAddingId] = useState("");
  const [memberBusy, setMemberBusy] = useState(false);

  const load = useCallback(() => {
    getProject(id).then(setProject).catch(() => navigate("/projects"));
    getProjectDoc(id).then((d) => setProjectMd(d.project_md)).catch(() => setProjectMd(""));
  }, [id, navigate]);
  useEffect(load, [load]);

  useEffect(() => {
    if (dialog === "vault") {
      getVaultFiles(id).then((r) => setVaultFiles(r.files)).catch(() => setVaultFiles([]));
    }
    if (dialog === "team" && project?.is_manager && project.kind === "team") {
      getEmployees().then(setEmployees).catch(() => setEmployees([]));
    }
  }, [dialog, id, project?.is_manager, project?.kind]);

  if (!project) return null;

  const sections = mdSections(projectMd);
  const noun = project.kind === "personal" ? "task" : "project";

  async function saveDoc() {
    const updated = await putProjectDoc(id, { project_md: draftMd });
    setProjectMd(updated.project_md);
    setEditing(false);
  }

  async function handleUpload(file: File) {
    await uploadVaultFile(id, file);
    const r = await getVaultFiles(id);
    setVaultFiles(r.files);
  }

  async function submitMom() {
    if (!momText.trim()) return;
    await addManualMessage(momText.trim(), momSubject.trim() || undefined, id);
    setMomText("");
    setMomSubject("");
    setDialog(null);
    setNotice("Added. It flows into the knowledge pipeline with this project as its tag.");
  }

  async function addMember() {
    if (!addingId) return;
    setMemberBusy(true);
    try {
      const updated = await patchProjectMembers(id, { add_member_employee_ids: [{ employee_id: addingId }] });
      setProject(updated);
      setAddingId("");
    } finally {
      setMemberBusy(false);
    }
  }

  async function removeMember(employeeId: string) {
    setMemberBusy(true);
    try {
      const updated = await patchProjectMembers(id, { remove_member_employee_ids: [employeeId] });
      setProject(updated);
    } finally {
      setMemberBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <p className="text-center font-bold text-ink text-xl">My Projects</p>

      <div className="mt-6 flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-extrabold text-ink">Project Name</h1>
          <p className="mt-1 text-lg text-ink">{project.name}</p>
          {project.description && <p className="text-sm text-inksoft">{project.description}</p>}
        </div>
        <button
          onClick={() => navigate(`/projects/${id}/dashboard`)}
          className="rounded-md bg-nav text-white font-semibold px-5 py-2.5 hover:bg-navdeep"
        >
          Project Dashboard
        </button>
      </div>

      {notice && (
        <div className="mt-4 rounded-md border border-cardline bg-card px-4 py-2 text-sm text-ink flex justify-between">
          <span>{notice}</span>
          <button onClick={() => setNotice(null)} aria-label="Dismiss" className="text-inksoft hover:text-ink">
            &times;
          </button>
        </div>
      )}

      <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_20rem]">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-xl font-extrabold text-ink">Overview</h2>
            {project.is_manager && !editing && (
              <button
                onClick={() => {
                  setDraftMd(projectMd);
                  setEditing(true);
                }}
                className="text-sm text-nav font-semibold hover:underline"
              >
                Edit
              </button>
            )}
          </div>

          {editing ? (
            <div className="mt-3">
              <textarea
                value={draftMd}
                onChange={(e) => setDraftMd(e.target.value)}
                rows={16}
                className="w-full rounded-md border border-cardline px-3 py-2 text-sm font-mono focus:outline-none focus:border-nav"
              />
              <div className="mt-2 flex gap-3">
                <button onClick={saveDoc} className="rounded-md bg-nav text-white text-sm font-semibold px-4 py-2 hover:bg-navdeep">
                  Save
                </button>
                <button onClick={() => setEditing(false)} className="rounded-md text-sm font-semibold px-4 py-2 text-inksoft hover:bg-surface">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="mt-3">
                <SectionText
                  text={sections["Overview"] ?? ""}
                  empty={`No overview yet${project.is_manager ? " -- Edit to write one" : ""}.`}
                />
              </div>

              <div className="mt-8 grid gap-8 sm:grid-cols-2">
                <div>
                  <h2 className="text-xl font-extrabold text-ink">Milestones</h2>
                  <div className="mt-2">
                    <SectionText text={sections["Milestones"] ?? ""} empty="No milestones set yet." />
                  </div>
                </div>
                <div>
                  <h2 className="text-xl font-extrabold text-ink">KPIs</h2>
                  <div className="mt-2">
                    <SectionText text={sections["KPIs"] ?? ""} empty="No KPIs set yet." />
                  </div>
                </div>
              </div>
            </>
          )}

          <h2 className="mt-10 text-xl font-extrabold text-ink">Quick Links</h2>
          <div className="mt-3 flex gap-4 flex-wrap">
            <button
              onClick={() => setDialog("team")}
              className="rounded-md bg-nav text-white font-semibold px-8 py-2.5 hover:bg-navdeep"
            >
              Team
            </button>
            <button
              onClick={() => setDialog("vault")}
              className="rounded-md bg-nav text-white font-semibold px-8 py-2.5 hover:bg-navdeep"
            >
              Project Vault
            </button>
            <button
              onClick={() => setDialog("mom")}
              className="rounded-md bg-nav/15 text-nav font-semibold px-8 py-2.5 hover:bg-nav hover:text-white transition-colors"
            >
              Add MoM / note
            </button>
          </div>
        </div>

        <aside>
          <div className="rounded-xl border border-cardline bg-card min-h-72 flex items-center justify-center p-6 text-center">
            <p className="text-sm text-inksoft">
              The timeline draws itself from key {noun} events as Pulse collects them -- nothing
              recorded yet.
            </p>
          </div>
          <p className="mt-2 text-center font-bold text-ink">Timeline</p>
        </aside>
      </div>

      {dialog === "team" && (
        <Dialog title="Team" onClose={() => setDialog(null)}>
          {project.members.length === 0 ? (
            <p className="text-sm text-inksoft">
              {project.kind === "personal" ? "Personal task -- just you." : "No members added yet."}
            </p>
          ) : (
            <ul className="divide-y divide-cardline/60">
              {project.members.map((m) => (
                <li key={m.employee_id} className="py-2 flex justify-between items-center gap-3 text-sm">
                  <div className="min-w-0">
                    <span className="font-semibold text-ink">{m.name}</span>{" "}
                    <span className="text-inksoft truncate">{m.role || m.email}</span>
                  </div>
                  {project.is_manager && (
                    <button
                      onClick={() => removeMember(m.employee_id)}
                      disabled={memberBusy}
                      className="text-inksoft hover:text-[#DD5454] text-xs font-semibold disabled:opacity-50"
                    >
                      Remove
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {project.is_manager && project.kind === "team" && (
            <div className="mt-4 pt-4 border-t border-cardline/60">
              {employees.filter((e) => !project.members.some((m) => m.employee_id === e.id)).length === 0 ? (
                <p className="text-xs text-inksoft">
                  Everyone in the directory is already on this project.
                </p>
              ) : (
                <div className="flex gap-2">
                  <select
                    value={addingId}
                    onChange={(e) => setAddingId(e.target.value)}
                    className="flex-1 min-w-0 rounded-md border border-cardline px-3 py-2 text-sm text-ink focus:outline-none focus:border-nav"
                  >
                    <option value="">Add a teammate…</option>
                    {employees
                      .filter((e) => !project.members.some((m) => m.employee_id === e.id))
                      .map((e) => (
                        <option key={e.id} value={e.id}>
                          {e.name} ({e.email})
                        </option>
                      ))}
                  </select>
                  <button
                    onClick={addMember}
                    disabled={!addingId || memberBusy}
                    className="rounded-md bg-nav text-white text-sm font-semibold px-4 py-2 hover:bg-navdeep disabled:opacity-60"
                  >
                    Add
                  </button>
                </div>
              )}
            </div>
          )}
        </Dialog>
      )}

      {dialog === "vault" && (
        <Dialog title="Project Vault" onClose={() => setDialog(null)}>
          {vaultFiles.length === 0 ? (
            <p className="text-sm text-inksoft">Nothing in the vault yet. Upload the first file below.</p>
          ) : (
            <ul className="divide-y divide-cardline/60">
              {vaultFiles.map((f) => (
                <li key={f.name} className="py-2 flex justify-between gap-3 text-sm">
                  <a href={vaultDownloadUrl(id, f.name)} className="text-nav font-semibold hover:underline truncate">
                    {f.name}
                  </a>
                  <span className="text-inksoft shrink-0">{(f.size / 1024).toFixed(1)} KB</span>
                </li>
              ))}
            </ul>
          )}
          <label className="mt-4 inline-block rounded-md bg-nav text-white text-sm font-semibold px-4 py-2 hover:bg-navdeep cursor-pointer">
            Upload file
            <input
              type="file"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleUpload(f);
                e.target.value = "";
              }}
            />
          </label>
        </Dialog>
      )}

      {dialog === "mom" && (
        <Dialog title="Add MoM / note" onClose={() => setDialog(null)}>
          <p className="text-sm text-inksoft">
            Meeting minutes or notes added here enter the knowledge pipeline tagged to this {noun}.
          </p>
          <input
            value={momSubject}
            onChange={(e) => setMomSubject(e.target.value)}
            placeholder="Subject (optional)"
            className="mt-3 w-full rounded-md border border-cardline px-3 py-2 text-sm focus:outline-none focus:border-nav"
          />
          <textarea
            value={momText}
            onChange={(e) => setMomText(e.target.value)}
            rows={6}
            placeholder="Paste the minutes or note…"
            className="mt-2 w-full rounded-md border border-cardline px-3 py-2 text-sm focus:outline-none focus:border-nav"
          />
          <div className="mt-3 flex justify-end">
            <button
              onClick={submitMom}
              disabled={!momText.trim()}
              className="rounded-md bg-nav text-white text-sm font-semibold px-4 py-2 hover:bg-navdeep disabled:opacity-60"
            >
              Add
            </button>
          </div>
        </Dialog>
      )}
    </main>
  );
}
