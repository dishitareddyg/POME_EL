import type { DashboardExperimentRecord, ManagerResult, ManagerType } from "../types/experiment";

export type CompletedExperiment = DashboardExperimentRecord & { results: Record<ManagerType, ManagerResult> };

export const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

export const completedOnly = (exps: DashboardExperimentRecord[]): CompletedExperiment[] =>
  exps.filter((e): e is CompletedExperiment => e.status === "completed" && e.results !== null);

export const managerMean = (
  exps: CompletedExperiment[],
  manager: ManagerType,
  key: keyof ManagerResult,
) => mean(exps.map((e) => e.results[manager][key]));

export const latestExperiment = (exps: DashboardExperimentRecord[]) =>
  [...exps].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];

/** Percent change of `b` relative to `a`. */
export const pctDelta = (a: number, b: number) => (a === 0 ? 0 : ((b - a) / a) * 100);
