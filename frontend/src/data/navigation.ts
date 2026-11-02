import {
  BarChart3,
  FilePlus2,
  FlaskConical,
  History,
  Layers,
  LayoutDashboard,
  LineChart,
  ListChecks,
  PlayCircle,
  Users,
  type LucideIcon,
} from "lucide-react";
import type { RouteId } from "../hooks/useHashRoute";

export interface NavItem {
  id: RouteId;
  label: string;
  href: string;
  icon: LucideIcon;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "dashboard", label: "Dashboard", href: "#/", icon: LayoutDashboard },
  { id: "new", label: "New Experiment", href: "#/new", icon: FilePlus2 },
  { id: "agents", label: "Agent Pool", href: "#/agents", icon: Users },
  { id: "workload", label: "Workload", href: "#/workload", icon: ListChecks },
  { id: "simulation", label: "Simulation", href: "#/simulation", icon: PlayCircle },
  { id: "results", label: "Results", href: "#/results", icon: BarChart3 },
  { id: "comparison", label: "Comparison", href: "#/comparison", icon: FlaskConical },
  { id: "analysis", label: "Experiment Analysis", href: "#/analysis", icon: LineChart },
  { id: "ablation", label: "Ablation Study", href: "#/ablation", icon: Layers },
  { id: "history", label: "Experiment History", href: "#/history", icon: History },
];
