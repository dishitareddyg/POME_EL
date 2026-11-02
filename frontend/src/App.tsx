import { useHashRoute } from "./hooks/useHashRoute";
import MainLayout from "./layouts/MainLayout";
import AgentPoolPage from "./pages/AgentPoolPage";
import AblationPage from "./pages/AblationPage";
import AnalysisPage from "./pages/AnalysisPage";
import ComparisonPage from "./pages/ComparisonPage";
import DashboardPage from "./pages/DashboardPage";
import HistoryPage from "./pages/HistoryPage";
import NewExperimentPage from "./pages/NewExperimentPage";
import ResultsPage from "./pages/ResultsPage";
import SimulationPage from "./pages/SimulationPage";
import WorkloadPage from "./pages/WorkloadPage";

export default function App() {
  const { route, param } = useHashRoute();

  return (
    <MainLayout route={route}>
      {route === "dashboard" && <DashboardPage />}
      {route === "new" && <NewExperimentPage />}
      {route === "agents" && <AgentPoolPage />}
      {route === "workload" && <WorkloadPage />}
      {route === "simulation" && <SimulationPage />}
      {route === "results" && <ResultsPage key={param ?? "latest"} initialId={param ? Number(param) : undefined} />}
      {route === "history" && <HistoryPage />}
      {route === "comparison" && <ComparisonPage />}
      {route === "analysis" && <AnalysisPage />}
      {route === "ablation" && <AblationPage />}
    </MainLayout>
  );
}
