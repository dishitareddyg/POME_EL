import type { ManagerType } from "../types/experiment";

export const MANAGER_LABELS: Record<ManagerType, string> = {
  conventional: "Conventional LLM Supervisor",
  posdcorb: "POSDCORB Adaptive Manager",
};

export const POSDCORB_FUNCTIONS = [
  "Planning",
  "Organizing",
  "Staffing",
  "Directing",
  "Coordinating",
  "Reporting",
  "Budgeting",
] as const;

export const MANAGER_COLORS: Record<ManagerType, string> = {
  conventional: "#d97706", // amber-600
  posdcorb: "#2563eb", // blue-600
};

export const MANAGER_SHORT: Record<ManagerType, string> = {
  conventional: "Conventional",
  posdcorb: "POSDCORB",
};
