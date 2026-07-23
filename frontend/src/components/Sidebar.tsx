import { useNavigate } from "react-router-dom";
import { SIDEBAR_ITEMS } from "@/constants";
import { logout } from "@/lib/api";

export default function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();

  async function handleLogout() {
    try {
      await logout();
    } finally {
      window.location.href = window.location.port === "3000" ? "/manager-assistant/" : "/";
    }
  }

  return (
    <>
      {/* backdrop */}
      <div
        onClick={onClose}
        className={`fixed inset-0 z-40 bg-black/30 transition-opacity ${
          open ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
        aria-hidden="true"
      />
      <aside
        className={`fixed top-0 left-0 z-50 h-full w-72 bg-sidebarbg flex flex-col
          transition-transform duration-200 motion-reduce:transition-none ${
            open ? "translate-x-0 shadow-2xl" : "-translate-x-full"
          }`}
        aria-label="Menu"
      >
        <div className="h-14 bg-nav flex items-center px-4">
          <button
            onClick={onClose}
            aria-label="Close menu"
            className="text-white text-2xl leading-none p-1.5 rounded hover:bg-navdeep"
          >
            &times;
          </button>
        </div>

        <nav className="flex-1 flex flex-col gap-4 px-6 pt-8">
          {SIDEBAR_ITEMS.map((item) =>
            item.path ? (
              <button
                key={item.label}
                onClick={() => {
                  onClose();
                  navigate(item.path!);
                }}
                className="rounded-full bg-sidebarbtn text-white font-bold py-3 px-6 text-left hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
              >
                {item.label}
              </button>
            ) : (
              <button
                key={item.label}
                disabled
                title="Coming soon"
                className="rounded-full bg-sidebarbtn/60 text-white/80 font-bold py-3 px-6 text-left cursor-not-allowed"
              >
                {item.label}
              </button>
            ),
          )}
        </nav>

        <div className="px-6 pb-8">
          <button
            onClick={handleLogout}
            className="w-full rounded-full bg-sidebarbtn text-white font-bold py-3 tracking-wide hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
          >
            LOGOUT
          </button>
        </div>
      </aside>
    </>
  );
}
