/**
 * TypeScript mirrors of the backend simulate.py Pydantic models.
 *
 * SimulateRequest  → POST /api/simulate
 * SimulateResponse ← POST /api/simulate
 *
 * SessionCreateRequest  → POST /api/simulate/session
 * SessionCreateResponse ← POST /api/simulate/session
 * SessionState          ← GET  /api/simulate/status/{id}
 *
 * Keep in sync with backend/app/api/simulate.py.
 */

import type { ManagementReport } from "./posdcorb";

// ---------------------------------------------------------------------------
// Shared sub-shapes
// ---------------------------------------------------------------------------

/** Mirrors WorkloadGeneratorConfig (backend/app/models/workload.py). */
export interface WorkloadGeneratorConfig {
  num_tasks: number;
  complexity: "low" | "medium" | "high";
  dependency_percentage: number;
  seed: number;
  /** Optional cap on agents passed through to POSDCORBConfig. */
  max_agents?: number | null;
}

/** Mirrors Agent (backend/app/models/agent.py) — minimal required subset. */
export interface AgentPayload {
  id: string;
  name: string;
  role: string;
  capabilities: string[];
  processing_speed: number;
  reliability: number;
  max_concurrent_tasks: number;
  origin?: string;
}

// ---------------------------------------------------------------------------
// Per-manager result (nested inside SimulateResponse.results)
// ---------------------------------------------------------------------------

export interface ManagerRunResult {
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  success_rate: number;
  total_wall_ms: number;
  total_decisions: number;
  avg_decision_latency_ms: number;
  decision_count_by_type: Record<string, number>;
  termination_reason: string | null;
  manager_statistics: Record<string, unknown>;
  resource_usage: Record<string, unknown>;
  /** Full ManagementReport dict — only present for "posdcorb" manager. */
  management_report: ManagementReport | null;
  /** Last N decision records from the execution trace. */
  decisions: DecisionRecord[];
}

/** Mirrors DecisionRecord.as_dict() from backend/app/managers/base.py. */
export interface DecisionRecord {
  manager_decision: string;
  task_id: string | null;
  selected_agent_id: string | null;
  selected_agent_name: string | null;
  decision_latency_ms: number;
  reason: string;
  result: string | null;
  success: boolean | null;
  metadata: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// ExperimentResult summary (experiments field in SimulateResponse)
// ---------------------------------------------------------------------------

export interface ExperimentResultSummary {
  manager_type: string;
  termination_reason: string;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  success_rate: number;
  total_wall_ms: number;
  total_decisions: number;
  resource_usage: Record<string, unknown>;
  manager_statistics: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// POST /api/simulate
// ---------------------------------------------------------------------------

export interface SimulateRequest {
  graph?: object | null;
  workload_config?: WorkloadGeneratorConfig | null;
  agents?: AgentPayload[] | null;
  manager_types?: string[];
  rng_seed?: number;
  max_decisions?: number;
  max_wall_ms?: number;
  max_agents?: number | null;
  failure_probability?: number | null;
  experiment_id?: string | null;
}

export interface SimulateResponse {
  graph_id: string;
  seed: number;
  manager_types: string[];
  /** Keyed by manager type (e.g. "conventional", "posdcorb"). */
  results: Record<string, ManagerRunResult>;
  errors: Record<string, string>;
  real_elapsed_s: number;
  /** ExperimentResult summaries — one per manager. */
  experiments: Record<string, ExperimentResultSummary>;
  termination_reasons: Record<string, string>;
  resource_usage: Record<string, unknown>;
  max_wall_ms: number | null;
}

// ---------------------------------------------------------------------------
// POST /api/simulate/session
// ---------------------------------------------------------------------------

export interface SessionCreateRequest {
  graph?: object | null;
  workload_config?: WorkloadGeneratorConfig | null;
  agents?: AgentPayload[] | null;
  manager_types?: string[];
  rng_seed?: number;
  max_decisions?: number;
  max_wall_ms?: number;
  max_agents?: number | null;
  failure_probability?: number | null;
  experiment_id?: string | null;
}

export interface SessionCreateResponse {
  session_id: string;
  status: string;
  pending_managers: string[];
  seed: number;
}

// ---------------------------------------------------------------------------
// GET /api/simulate/status/{session_id}
// ---------------------------------------------------------------------------

export interface SessionState {
  simulation_id: string;
  status: "running" | "completed" | "stopped";
  seed: number;
  pending_managers: string[];
  completed_managers: string[];
  elapsed_s: number;
  resource_limits: Record<string, unknown>;
  results: Record<string, ExperimentResultSummary>;
}
