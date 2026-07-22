import amexLogo from "@/assets/amex.png";
import outlookIcon from "@/assets/outlook_icon.png";
import { goToOutlookLogin } from "@/lib/api";

export default function Login() {
  return (
    <div className="min-h-screen flex flex-col bg-[#fefcf8]">
      <header className="px-8 pt-8">
        <img src={amexLogo} alt="American Express" className="h-16 w-16 rounded-md" />
      </header>

      <main className="flex-1 flex items-center px-8 md:px-20">
        <div className="w-full flex flex-col md:flex-row items-center justify-between gap-12">
          <h1 className="text-6xl md:text-7xl font-extrabold tracking-tight">
            <span className="text-[#3b76e0]">PULSE</span>
            <span
              className="ml-1 text-[#3b76e0]"
              style={{ WebkitTextStroke: "1.5px #3b76e0", color: "transparent" }}
            >
              .ai
            </span>
          </h1>

          <div className="flex flex-col gap-4 w-full max-w-xs">
            <div className="border-2 border-black rounded-sm px-6 py-3 text-center font-bold tracking-wide select-none">
              LOGIN
            </div>
            <button
              onClick={goToOutlookLogin}
              className="flex items-center gap-3 rounded-md bg-[#59a7f0] hover:bg-[#4a9aea] transition-colors px-5 py-3 font-semibold text-slate-900"
            >
              <img src={outlookIcon} alt="" className="h-7 w-7 rounded" />
              Continue with Outlook
            </button>
          </div>
        </div>
      </main>

      <footer className="relative h-40 bg-gradient-to-r from-[#16296b] to-[#6f9ce8] overflow-hidden">
        <div className="absolute inset-0 flex items-end justify-end p-6">
          <span className="text-white/90 text-sm">@all rights reserved</span>
        </div>
      </footer>
    </div>
  );
}
