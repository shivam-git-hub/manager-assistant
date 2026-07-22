import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  claimAgent,
  getAvailableAgents,
  getMyAgent,
  releaseAgent,
  type MyAgent,
  type PoolAgent,
} from "@/lib/api";

// Agents page (Shivam's addition -- not in the wireframes, styled to match
// Connectors). Layout: "My agent" on top (the one you hold), then
// "Available agents" (unclaimed pool bots) always listed. The admin's
// access code is asked for AT claim time -- seeing the list needs no
// code. One agent per user. Redesigned 2026-07-23: claiming is pure
// bookkeeping (see app/controlplane/agents.py) -- there's no "install to
// Slack" action here anymore, the admin installs each pool bot directly
// on Slack's own site (see SLACK.md) independent of any claim. This page
// is also unrelated to Connectors' Slack row (that's message tracking).

function AgentGlyph() {
  // Basic shape drawn inline: a friendly bot head (no agent icon in /assets).
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" aria-hidden="true">
      <rect x="8" y="14" width="28" height="20" rx="6" fill="#2E77DD" />
      <circle cx="17" cy="24" r="3" fill="white" />
      <circle cx="27" cy="24" r="3" fill="white" />
      <rect x="20" y="6" width="4" height="6" rx="2" fill="#2E77DD" />
      <circle cx="22" cy="5" r="2.5" fill="#67C05B" />
    </svg>
  );
}

export default function Agents() {
  const [mine, setMine] = useState<MyAgent | null | undefined>(undefined);
  const [available, setAvailable] = useState<PoolAgent[]>([]);
  const [claiming, setClaiming] = useState<PoolAgent | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmingRemove, setConfirmingRemove] = useState(false);

  const load = useCallback(() => {
    getMyAgent().then(setMine).catch(() => setMine(null));
    getAvailableAgents()
      .then((r) => setAvailable(r.agents))
      .catch(() => setAvailable([]));
  }, []);
  useEffect(load, [load]);

  async function handleClaim() {
    if (!claiming || !code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await claimAgent(claiming.id, code.trim());
      setClaiming(null);
      setCode("");
      load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setError("That access code isn't right. Codes are handed out by your admin.");
      } else if (e instanceof ApiError && e.status === 409) {
        setError("That agent was taken a moment ago -- pick another.");
        setClaiming(null);
        load();
      } else {
        setError("Claim failed -- try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove() {
    setBusy(true);
    setError(null);
    try {
      await releaseAgent();
      setConfirmingRemove(false);
      load();
    } catch {
      setError("Couldn't remove your agent -- try again.");
    } finally {
      setBusy(false);
    }
  }

  if (mine === undefined) return null;

  return (
    <main className="mx-auto max-w-4xl px-6 py-8">
      <h1 className="text-2xl font-extrabold text-ink text-center">Agents</h1>

      <section className="mt-8">
        <h2 className="font-semibold text-ink">My agent</h2>
        {mine ? (
          <div className="mt-3 rounded-lg border border-cardline bg-white px-6 py-5">
            <div className="flex items-center gap-4 flex-wrap">
              <AgentGlyph />
              <div className="flex-1 min-w-40">
                <div className="text-lg font-semibold text-ink">{mine.agent_name}</div>
                <div className="text-xs text-inksoft">
                  {mine.installed
                    ? "Installed and ready to receive messages"
                    : "Waiting on your admin to install it to your Slack workspace"}
                </div>
              </div>
              <button
                onClick={() => setConfirmingRemove(true)}
                disabled={busy}
                className="rounded-md border border-cardline px-4 py-1.5 text-sm font-semibold text-inksoft hover:border-[#DD5454] hover:text-[#DD5454] disabled:opacity-50"
              >
                Remove
              </button>
            </div>
            <p className="mt-3 text-sm text-inksoft">
              This agent chats with you and your teammates and can send messages on your behalf.
              It's separate from your own Slack connection in Connectors, which just tracks your
              messages.
            </p>
          </div>
        ) : (
          <p className="mt-2 text-sm text-inksoft">
            You don't hold an agent yet. Claim one below with the access code from your admin.
          </p>
        )}
      </section>

      <section className="mt-8">
        <h2 className="font-semibold text-ink">Available agents</h2>
        {available.length === 0 ? (
          <p className="mt-2 text-sm text-inksoft">
            No unclaimed agents right now -- ask your admin to add more to the pool.
          </p>
        ) : (
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            {available.map((agent) => (
              <div
                key={agent.id}
                className="rounded-lg border border-cardline bg-card px-5 py-4 flex items-center gap-3"
              >
                <AgentGlyph />
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-ink truncate">{agent.name}</div>
                  <div className="text-xs text-inksoft">Unclaimed</div>
                </div>
                <button
                  onClick={() => {
                    setClaiming(agent);
                    setError(null);
                  }}
                  disabled={!!mine || busy}
                  title={mine ? "You already hold an agent -- one per user" : undefined}
                  className="rounded-md bg-nav text-white px-4 py-1.5 text-sm font-semibold hover:bg-navdeep disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Claim
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {claiming && (
        <div
          className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
          onClick={() => setClaiming(null)}
        >
          <div
            className="w-full max-w-sm rounded-xl bg-white shadow-2xl p-6"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={`Claim ${claiming.name}`}
          >
            <h3 className="font-bold text-ink">Claim {claiming.name}</h3>
            <p className="mt-1 text-sm text-inksoft">
              Enter the access code from your admin to claim this agent.
            </p>
            <input
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleClaim()}
              placeholder="Access code"
              type="password"
              className="mt-3 w-full rounded-md border border-cardline px-3 py-2 text-sm focus:outline-none focus:border-nav"
            />
            {error && <p className="mt-2 text-sm text-[#DD5454]">{error}</p>}
            <div className="mt-4 flex justify-end gap-3">
              <button
                onClick={() => setClaiming(null)}
                className="rounded-md px-4 py-2 text-sm font-semibold text-inksoft hover:bg-surface"
              >
                Cancel
              </button>
              <button
                onClick={handleClaim}
                disabled={busy || !code.trim()}
                className="rounded-md bg-nav text-white px-4 py-2 text-sm font-semibold hover:bg-navdeep disabled:opacity-60"
              >
                {busy ? "Claiming…" : "Claim agent"}
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmingRemove && mine && (
        <div
          className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
          onClick={() => setConfirmingRemove(false)}
        >
          <div
            className="w-full max-w-sm rounded-xl bg-white shadow-2xl p-6"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={`Remove ${mine.agent_name}`}
          >
            <h3 className="font-bold text-ink">Remove {mine.agent_name}?</h3>
            <p className="mt-1 text-sm text-inksoft">
              This frees {mine.agent_name} back into the available pool for anyone to claim. You
              can claim a different agent (or this one again) afterward.
            </p>
            <div className="mt-4 flex justify-end gap-3">
              <button
                onClick={() => setConfirmingRemove(false)}
                className="rounded-md px-4 py-2 text-sm font-semibold text-inksoft hover:bg-surface"
              >
                Cancel
              </button>
              <button
                onClick={handleRemove}
                disabled={busy}
                className="rounded-md bg-[#DD5454] text-white px-4 py-2 text-sm font-semibold hover:bg-[#c74848] disabled:opacity-60"
              >
                {busy ? "Removing…" : "Remove agent"}
              </button>
            </div>
          </div>
        </div>
      )}

      {error && !claiming && <p className="mt-4 text-sm text-[#DD5454] text-center">{error}</p>}
    </main>
  );
}
