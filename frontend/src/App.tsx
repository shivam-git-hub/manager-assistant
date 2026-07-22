import { useEffect, useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import Login from "@/pages/Login";
import Home from "@/pages/Home";
import Projects from "@/pages/Projects";
import Tasks from "@/pages/Tasks";
import ProjectOverview from "@/pages/ProjectOverview";
import ProjectDashboard from "@/pages/ProjectDashboard";
import PortfolioDetail from "@/pages/PortfolioDetail";
import Connectors from "@/pages/Connectors";
import Agents from "@/pages/Agents";
import TopNav from "@/components/TopNav";
import Sidebar from "@/components/Sidebar";
import QuickAddTodoModal from "@/components/QuickAddTodoModal";
import { getMe, type Manager } from "@/lib/api";

export default function App() {
  const [manager, setManager] = useState<Manager | null>(null);
  const [loading, setLoading] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [quickAddTodoOpen, setQuickAddTodoOpen] = useState(false);

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
      <TopNav onMenuClick={() => setSidebarOpen(true)} onTodoClick={() => setQuickAddTodoOpen(true)} />
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      {quickAddTodoOpen && <QuickAddTodoModal onClose={() => setQuickAddTodoOpen(false)} />}
      <Routes>
        <Route path="/" element={<Home manager={manager} />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/projects/:id" element={<ProjectOverview />} />
        <Route path="/projects/:id/dashboard" element={<ProjectDashboard />} />
        <Route path="/portfolios/:id" element={<PortfolioDetail />} />
        <Route path="/connectors" element={<Connectors />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
