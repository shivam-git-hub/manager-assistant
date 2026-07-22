import { useState } from "react";
import { createTodo } from "@/lib/api";

// Opened from the TopNav "Todo" button -- lets a user add a todo from
// anywhere in the app without being dropped onto Home (which used to be
// the only place to add one).
export default function QuickAddTodoModal({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState("");
  const [due, setDue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState(false);

  async function handleAdd() {
    if (!text.trim()) {
      setError("Give the todo some text");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await createTodo(text.trim(), due ? `${due}T00:00:00` : undefined);
      setText("");
      setDue("");
      setAdded(true);
      setSaving(false);
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
        aria-label="Add a todo"
      >
        <h2 className="text-lg font-bold text-ink">Add a todo</h2>

        <label className="block mt-4 text-sm font-semibold text-inksoft">
          What needs doing?
          <input
            autoFocus
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setAdded(false);
            }}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            className="mt-1 w-full rounded-md border border-cardline px-3 py-2 text-ink focus:outline-none focus:border-nav"
          />
        </label>

        <label className="block mt-3 text-sm font-semibold text-inksoft">
          Due (optional)
          <input
            type="date"
            value={due}
            onChange={(e) => setDue(e.target.value)}
            className="mt-1 w-full rounded-md border border-cardline px-3 py-2 text-ink focus:outline-none focus:border-nav"
          />
        </label>

        {error && <p className="mt-3 text-sm text-[#DD5454]">{error}</p>}
        {added && <p className="mt-3 text-sm text-[#4CAF50] font-semibold">Added. Add another or close.</p>}

        <div className="mt-5 flex justify-end gap-3">
          <button onClick={onClose} className="rounded-md px-4 py-2 text-sm font-semibold text-inksoft hover:bg-surface">
            Done
          </button>
          <button
            onClick={handleAdd}
            disabled={saving}
            className="rounded-md bg-nav px-4 py-2 text-sm font-semibold text-white hover:bg-navdeep disabled:opacity-60"
          >
            {saving ? "Adding…" : "Add todo"}
          </button>
        </div>
      </div>
    </div>
  );
}
