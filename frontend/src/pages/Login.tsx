import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import amexLogo from "@/assets/amex.png";
import outlookIcon from "@/assets/outlook_icon.png";
import { ApiError, devLogin, goToOutlookLogin } from "@/lib/api";

const ERROR_MESSAGES: Record<string, string> = {
  not_recognized_employee:
    "This account isn't set up for Pulse.ai yet. Contact your admin to be added.",
};

export default function Login() {
  const [searchParams] = useSearchParams();
  const errorCode = searchParams.get("error");
  const errorMessage = errorCode ? ERROR_MESSAGES[errorCode] ?? "Sign-in failed. Please try again." : null;

  const [devEmail, setDevEmail] = useState("");
  const [devName, setDevName] = useState("");
  const [devError, setDevError] = useState<string | null>(null);
  const [devBusy, setDevBusy] = useState(false);

  async function handleDevLogin(e: React.FormEvent) {
    e.preventDefault();
    setDevError(null);
    setDevBusy(true);
    try {
      await devLogin(devEmail.trim(), devName.trim());
      // Full page navigation, not client-side navigate(): App.tsx only
      // fetches getMe() once on mount to decide whether to render Login vs
      // the authenticated app -- a SPA-internal navigate() would land on
      // /connectors while App.tsx still thinks there's no manager.
      window.location.href = "/connectors";
    } catch (err) {
      setDevError(
        err instanceof ApiError && err.status === 404
          ? "Dev login is disabled on this server (DEV_AUTH_ENABLED=false)."
          : "Dev login failed. Check the email and try again.",
      );
    } finally {
      setDevBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col bg-zinc-50 text-zinc-950">
      <header className="px-8 pt-8 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <img src={amexLogo} alt="American Express" className="h-10 w-10 rounded object-contain border border-zinc-200" />
          <span className="text-xs font-semibold tracking-wider text-zinc-500 uppercase">American Express Partner</span>
        </div>
      </header>

      <main className="flex-1 flex items-center justify-center px-8">
        <div className="w-full max-w-md bg-white border border-zinc-200 rounded-xl shadow-sm p-8 flex flex-col gap-8">
          <div className="text-center">
            <h1 className="text-4xl font-extrabold tracking-tight">
              <span className="text-zinc-900 font-black">PULSE</span>
              <span className="text-zinc-400 font-light">.ai</span>
            </h1>
            <p className="mt-2 text-sm text-zinc-500">
              The Intelligent Management Assistant Portal
            </p>
          </div>

          {errorMessage && (
            <div className="rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3 text-center">
              {errorMessage}
            </div>
          )}

          <div className="flex flex-col gap-4">
            <button
              onClick={goToOutlookLogin}
              className="flex items-center justify-center gap-3 rounded-lg bg-zinc-950 hover:bg-zinc-900 text-white transition-colors px-6 py-3.5 font-medium shadow-sm focus:outline-none focus:ring-2 focus:ring-zinc-950 focus:ring-offset-2"
            >
              <img src={outlookIcon} alt="" className="h-5 w-5 rounded-sm shrink-0" />
              <span>Continue with Outlook</span>
            </button>
          </div>

          <div className="flex items-center gap-3 text-[11px] text-zinc-400">
            <div className="h-px flex-1 bg-zinc-200" />
            <span>dev / demo -- no corporate network required</span>
            <div className="h-px flex-1 bg-zinc-200" />
          </div>

          <form onSubmit={handleDevLogin} className="flex flex-col gap-2">
            <input
              type="email"
              required
              placeholder="you@company.com"
              value={devEmail}
              onChange={(e) => setDevEmail(e.target.value)}
              className="rounded-lg border border-zinc-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-950"
            />
            <input
              type="text"
              placeholder="Display name (optional)"
              value={devName}
              onChange={(e) => setDevName(e.target.value)}
              className="rounded-lg border border-zinc-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-950"
            />
            {devError && <div className="text-xs text-red-600">{devError}</div>}
            <button
              type="submit"
              disabled={devBusy}
              className="rounded-lg bg-zinc-200 hover:bg-zinc-300 text-zinc-900 transition-colors px-6 py-2.5 font-medium text-sm disabled:opacity-50"
            >
              {devBusy ? "Signing in..." : "Dev Login"}
            </button>
          </form>

          <div className="text-center text-[11px] text-zinc-400 leading-relaxed">
            By signing in, you authorize Pulse.ai to access your work context and synchronize with your tenant's secure workspace.
          </div>
        </div>
      </main>

      <footer className="border-t border-zinc-200 bg-white py-6 px-8 flex items-center justify-between text-xs text-zinc-500">
        <span>© 2026 Pulse.ai Organization. All rights reserved.</span>
        <span className="font-mono text-[10px]">v2.1.0-architecture-opt</span>
      </footer>
    </div>
  );
}
