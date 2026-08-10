import { useEffect, useState } from "react";
import { createProject, getEmployees, type Employee, type ProjectDetail } from "@/lib/api";

// Minimal create dialog so projects/tasks can actually be created and the
// grid/home rails have real data to show. The full Create Project page
// replaces/extends this later.
export default function CreateProjectModal({
  kind,
  onClose,
  onCreated,
}: {
  kind: "team" | "personal";
  onClose: () => void;
  onCreated: (p: ProjectDetail) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const noun = kind === "team" ? "project" : "task";
  const MIN_DESCRIPTION_LENGTH = 20;

  useEffect(() => {
    if (kind === "team") getEmployees().then(setEmployees).catch(() => setEmployees([]));
  }, [kind]);

  function toggleMember(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleCreate() {
    if (!name.trim()) {
      setError(`Give the ${noun} a name`);
      return;
    }
    if (description.trim().length < MIN_DESCRIPTION_LENGTH) {
      setError(`Description must be at least ${MIN_DESCRIPTION_LENGTH} characters`);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await createProject({
        name: name.trim(),
        description: description.trim(),
        kind,
        member_employee_ids:
          kind === "team" ? [...selected].map((id) => ({ employee_id: id })) : undefined,
      });
      onCreated(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong -- try again");
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-xl bg-white shadow-2xl p-6"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`New ${noun}`}
      >
        <h2 className="text-lg font-bold text-ink capitalize">New {noun}</h2>

        <label className="block mt-4 text-sm font-semibold text-inksoft">
          Name
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-md border border-cardline px-3 py-2 text-ink focus:outline-none focus:border-nav"
          />
        </label>

        <label className="block mt-3 text-sm font-semibold text-inksoft">
          Description
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="mt-1 w-full rounded-md border border-cardline px-3 py-2 text-ink focus:outline-none focus:border-nav"
          />
          <span className="mt-1 block text-xs text-inksoft">
            {description.trim().length}/{MIN_DESCRIPTION_LENGTH} characters minimum
          </span>
        </label>

        {kind === "team" && (
          <div className="mt-3">
            <div className="text-sm font-semibold text-inksoft">Team members</div>
            {employees.length === 0 ? (
              <p className="mt-1 text-xs text-inksoft">
                No employees in the directory yet -- ask your admin to seed it, or create the
                project without members and add them later.
              </p>
            ) : (
              <ul className="mt-1 max-h-36 overflow-y-auto rounded-md border border-cardline divide-y divide-cardline/60">
                {employees.map((emp) => (
                  <li key={emp.id}>
                    <label className="flex items-center gap-2 px-3 py-1.5 text-sm text-ink hover:bg-card cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selected.has(emp.id)}
                        onChange={() => toggleMember(emp.id)}
                      />
                      <span className="font-medium">{emp.name}</span>
                      <span className="text-inksoft text-xs truncate">{emp.email}</span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {error && <p className="mt-3 text-sm text-[#DD5454]">{error}</p>}

        <div className="mt-5 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-md px-4 py-2 text-sm font-semibold text-inksoft hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={handleCreate}
            disabled={saving}
            className="rounded-md bg-nav px-4 py-2 text-sm font-semibold text-white hover:bg-navdeep disabled:opacity-60 capitalize"
          >
            {saving ? "Creating…" : `Create ${noun}`}
          </button>
        </div>
      </div>
    </div>
  );
}
