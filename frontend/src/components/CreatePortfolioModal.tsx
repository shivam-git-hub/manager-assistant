import { useState } from "react";
import { createPortfolio, type Portfolio } from "@/lib/api";

export default function CreatePortfolioModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (p: Portfolio) => void;
}) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    if (!name.trim()) {
      setError("Give the portfolio a name");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await createPortfolio(name.trim());
      onCreated(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong -- try again");
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-xl bg-white shadow-2xl p-6"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="New portfolio"
      >
        <h2 className="text-lg font-bold text-ink">New portfolio</h2>

        <label className="block mt-4 text-sm font-semibold text-inksoft">
          Name
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            className="mt-1 w-full rounded-md border border-cardline px-3 py-2 text-ink focus:outline-none focus:border-nav"
          />
        </label>

        {error && <p className="mt-3 text-sm text-[#DD5454]">{error}</p>}

        <div className="mt-5 flex justify-end gap-3">
          <button onClick={onClose} className="rounded-md px-4 py-2 text-sm font-semibold text-inksoft hover:bg-surface">
            Cancel
          </button>
          <button
            onClick={handleCreate}
            disabled={saving}
            className="rounded-md bg-nav px-4 py-2 text-sm font-semibold text-white hover:bg-navdeep disabled:opacity-60"
          >
            {saving ? "Creating…" : "Create portfolio"}
          </button>
        </div>
      </div>
    </div>
  );
}
