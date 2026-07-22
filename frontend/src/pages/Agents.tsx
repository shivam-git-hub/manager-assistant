import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  claimAgent,
  getConnections,
  goToSlackInstall,
  redeemAgentCode,
  type Connections,
  type PoolAgent,
} from "@/lib/api";

// Agents page (Shivam's addition -- not in the wireframes, styled to match
// Connectors): unlock the pool with the admin's access code, claim one
// available agent, then install it to Slack. A user holds at most one
// agent; the claim is kept even if Slack is later disconnected.

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
  const [conn, setConn] = useState<Connections | null>(null);
  const [code, setCode] = useState("");
  const [available, setAvailable] = useState<PoolAgent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(() => {
    getConnections().then(setConn).catch(() => setConn(null));
  }, []);
  useEffect(load, [load]);

  async function handleUnlock() {
    if (!code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await redeemAgentCode(code.trim());
      setAvailable(res.agents);
      if (res.agents.length === 0)
        setError("The pool has no unclaimed agents right now -- ask your admin to add more.");
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 403
          ? "That access code isn't right. Codes are handed out by your admin."
          : "Couldn't check the code -- try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleClaim(agent: PoolAgent) {
    setBusy(true);
    setError(null);
    try {
      await claimAgent(agent.id);
      setAvailable(null);
      load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError("That agent was taken a moment ago -- pick another.");
        // refresh the list with the same already-validated code
        try {
          const res = await redeemAgentCode(code.trim());
          setAvailable(res.agents);
        } catch {
          setAvailable(null);
        }
      } else {
        setError("Claim failed -- try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (!conn) return null;

  const mine = conn.slack;

  return (
    <main className="mx-auto max-w-4xl px-6 py-8">
      <h1 className="text-2xl font-extrabold text-ink text-center">Agents</h1>

      {mine ? (
        <section className="mt-8 rounded-lg border border-cardline bg-white px-6 py-6">
          <div className="flex items-center gap-4 flex-wrap">
            <AgentGlyph />
            <div className="flex-1 min-w-40">
              <div className="text-lg font-semibold text-ink">{mine.agent_name}</div>
              <div className="text-xs text-inksoft">
                Your personal agent{mine.installed ? " -- installed to Slack" : " -- not installed to Slack yet"}
              </div>
            </div>
            {mine.installed ? (
              <button
                onClick={() => navigate("/connectors")}
                className="rounded-md bg-nav/15 text-nav px-4 py-2 text-sm font-semibold hover:bg-nav hover:text-white transition-colors"
              >
                Manage in Connectors
              </button>
            ) : (
              <button
                onClick={goToSlackInstall}
                className="rounded-md bg-nav text-white px-4 py-2 text-sm font-semibold hover:bg-navdeep"
              >
                Install to Slack
              </button>
            )}
          </div>
          <p className="mt-4 text-sm text-inksoft">
            This agent chats with you and your teammates, takes follow-ups, and drafts messages on
            your behalf. The claim is yours -- disconnecting Slack later keeps it.
          </p>
        </section>
      ) : (
        <>
          <section className="mt-8 rounded-lg border border-cardline bg-white px-6 py-6">
            <h2 className="font-semibold text-ink">Claim your agent</h2>
            <p className="mt-1 text-sm text-inksoft">
              Enter the access code from your admin to see the available agents.
            </p>
            <div className="mt-3 flex gap-2 max-w-md">
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleUnlock()}
                placeholder="Access code"
                type="password"
                className="flex-1 min-w-0 rounded-md border border-cardline px-3 py-2 text-sm focus:outline-none focus:border-nav"
              />
              <button
                onClick={handleUnlock}
                disabled={busy}
                className="rounded-md bg-nav text-white px-4 py-2 text-sm font-semibold hover:bg-navdeep disabled:opacity-60"
              >
                Unlock
              </button>
            </div>
          </section>

          {available && available.length > 0 && (
            <section className="mt-6">
              <h2 className="font-semibold text-ink">Available agents</h2>
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
                      onClick={() => handleClaim(agent)}
                      disabled={busy}
                      className="rounded-md bg-nav text-white px-4 py-1.5 text-sm font-semibold hover:bg-navdeep disabled:opacity-60"
                    >
                      Claim
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}
        </>
      )}

      {error && <p className="mt-4 text-sm text-[#DD5454] text-center">{error}</p>}
    </main>
  );
}
