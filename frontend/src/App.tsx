import { useEffect, useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import Login from "@/pages/Login";
import Home from "@/pages/Home";
import Projects from "@/pages/Projects";
import ComingSoon from "@/pages/ComingSoon";
import TopNav from "@/components/TopNav";
import Sidebar from "@/components/Sidebar";
import { getMe, type Manager } from "@/lib/api";

export default function App() {
  const [manager, setManager] = useState<Manager | null>(null);
  const [loading, setLoading] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    getMe()
      .then(setManager)
      .catch(() => setManager(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return null;

  if (!manager) {
    return (
      <Routes>
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }

  return (
    <div className="min-h-screen bg-surface text-ink font-sans">
      <TopNav onMenuClick={() => setSidebarOpen(true)} />
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <Routes>
        <Route path="/" element={<Home manager={manager} />} />
        <Route path="/projects" element={<Projects />} />
        <Route
          path="/connectors"
          element={
            <ComingSoon
              title="Connectors"
              note="Connect Outlook and Slack here. This page is next on the build list."
            />
          }
        />
        <Route
          path="/agents"
          element={
            <ComingSoon
              title="Agents"
              note="Claim your personal agent here. This page is next on the build list."
            />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
