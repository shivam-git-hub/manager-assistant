import { Link, NavLink } from "react-router-dom";
import amexLogo from "@/assets/amex.png";
import menusIcon from "@/assets/menus.png";
import { NAV_TABS } from "@/constants";

function HomeIcon() {
  // Basic shape drawn inline (allowed per the asset rules -- no home icon
  // in /assets).
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5.5v-6h-5v6H4a1 1 0 0 1-1-1v-9.5Z"
        fill="white"
      />
    </svg>
  );
}

export default function TopNav({
  onMenuClick,
  onTodoClick,
}: {
  onMenuClick: () => void;
  onTodoClick: () => void;
}) {
  return (
    <header className="sticky top-0 z-30 bg-nav text-white shadow-md">
      <div className="flex items-center gap-5 px-4 h-14">
        <button
          onClick={onMenuClick}
          aria-label="Open menu"
          className="p-1.5 rounded hover:bg-navdeep focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
        >
          {/* black source icon from /assets, inverted to white for the bar */}
          <img src={menusIcon} alt="" className="h-6 w-6 invert" />
        </button>

        <Link
          to="/"
          aria-label="Home"
          className="p-1.5 rounded hover:bg-navdeep focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
        >
          <HomeIcon />
        </Link>

        <nav className="hidden md:flex items-center gap-6 text-[15px] font-semibold">
          {NAV_TABS.map((tab) =>
            tab.path ? (
              <NavLink
                key={tab.label}
                to={tab.path}
                className={({ isActive }) =>
                  `hover:text-wordmark transition-colors ${isActive ? "underline underline-offset-8 decoration-2" : ""}`
                }
              >
                {tab.label}
              </NavLink>
            ) : (
              // Pages that don't exist yet render as plain text, not links.
              <span key={tab.label} className="text-white/75 cursor-default select-none">
                {tab.label}
              </span>
            ),
          )}
        </nav>

        <div className="flex-1 flex justify-center">
          <Link to="/" className="font-extrabold tracking-tight text-2xl select-none">
            <span className="text-wordmark">PULSE</span>
            <span
              className="ml-0.5"
              style={{ WebkitTextStroke: "1.2px #BFE6FB", color: "transparent" }}
            >
              .ai
            </span>
          </Link>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={onTodoClick}
            className="rounded-full bg-white text-nav font-bold text-sm px-4 py-1.5 hover:bg-wordmark transition-colors"
          >
            Todo
          </button>
          <img src={amexLogo} alt="American Express" className="h-9 w-9 rounded" />
        </div>
      </div>
    </header>
  );
}
