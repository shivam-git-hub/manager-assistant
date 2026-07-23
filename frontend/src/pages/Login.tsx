import amexLogo from "@/assets/amex.png";
import outlookIcon from "@/assets/outlook_icon.png";
import { goToOutlookLogin } from "@/lib/api";

export default function Login() {
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

          <div className="flex flex-col gap-4">
            <button
              onClick={goToOutlookLogin}
              className="flex items-center justify-center gap-3 rounded-lg bg-zinc-950 hover:bg-zinc-900 text-white transition-colors px-6 py-3.5 font-medium shadow-sm focus:outline-none focus:ring-2 focus:ring-zinc-950 focus:ring-offset-2"
            >
              <img src={outlookIcon} alt="" className="h-5 w-5 rounded-sm shrink-0" />
              <span>Continue with Outlook</span>
            </button>
          </div>

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
