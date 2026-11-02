/**
 * Shared TypeScript types for experiment configuration.
 * Mirror of backend/app/models/experiment.py — keep both files in sync.
 */

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

export type ManagerType = "conventional" | "posdcorb";
export type ExperimentStatus = "pending" | "running" | "completed" | "failed";
export type TaskComplexity = "low" | "medium" | "high";

/** Priority tier → share (0-100). Must sum to 100. */
export type PriorityDistribution = {
  critical: number;
  high: number;
  medium: number;
  low: number;
};

// ---------------------------------------------------------------------------
// Sub-configs (each maps 1-to-1 with a Pydantic model in the backend)
// ---------------------------------------------------------------------------

export interface WorkloadConfig {
  /** Total tasks to generate. Range: 1-10 000. */
  num_tasks: number;
  /** Complexity tier for generated tasks. */
  task_complexity: TaskComplexity;
  /** Percentage of tasks that have at least one dependency. Range: 0-100. */
  dependency_percentage: number;
  /** Distribution of task priorities. Values must sum to 100. */
  priority_distribution: PriorityDistribution;
}

export interface AgentConfig {
  /** Number of agents alive at simulation start. Range: 1-256. */
  initial_agent_count: number;
  /** Maximum agents after dynamic scaling. Must be >= initial_agent_count. */
  max_agent_count: number;
  /** 0 = homogeneous clones; 1 = maximally diverse skill sets. Range: 0-1. */
  agent_heterogeneity: number;
  /** Per-step probability an agent fails. Range: 0-1. */
  agent_failure_probability: number;
}

export interface ResourceBudget {
  /** Wall-clock seconds before simulation is aborted. */
  max_execution_time_s: number;
  /** Total manager decision calls allowed. */
  max_manager_decisions: number;
  /** Total LLM inference calls allowed. */
  max_llm_calls: number;
  /** Cumulative token budget across all LLM calls. */
  token_budget: number;
}

export interface ManagerSelection {
  /** Which manager types participate. At least one required. */
  manager_types: ManagerType[];
}

// ---------------------------------------------------------------------------
// Top-level config
// ---------------------------------------------------------------------------

export interface ExperimentConfig {
  /** Human-readable experiment label. */
  name: string;
  /** RNG seed for reproducibility. */
  random_seed: number;
  /** Independent runs per manager (for statistical averaging). */
  repetitions: number;
  workload: WorkloadConfig;
  agents: AgentConfig;
  resources: ResourceBudget;
  managers: ManagerSelection;
}

// ---------------------------------------------------------------------------
// API request / response shapes
// ---------------------------------------------------------------------------

export interface CreateExperimentRequest {
  config: ExperimentConfig;
}

export interface CreateExperimentResponse {
  experiment_id: string;
  name: string;
  status: ExperimentStatus;
  created_at: number;
  message: string;
}

// ---------------------------------------------------------------------------
// Dashboard / mock-data types (kept for backward compat with existing pages)
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: "ok";
  version: string;
  timestamp: number;
  python_version: string;
  database: "ok" | "error";
  cpu_count: number;
  memory_total_mb: number;
}

export interface ManagerResult {
  avg_latency_ms: number;
  p95_latency_ms: number;
  throughput_tps: number;
  success_rate: number;
  cpu_pct: number;
  memory_mb: number;
}

/** Legacy dashboard fixture shape used by the overview/history/results pages.
 * Kept separate from the backend ExperimentRecord API model above.
 */
export interface DashboardExperimentRecord {
  id: number;
  name: string;
  status: ExperimentStatus;
  num_workers: number;
  num_tasks: number;
  random_seed: number;
  workload: string;
  created_at: string;
  results: Record<ManagerType, ManagerResult> | null;
}

export interface ExperimentRecord {
  id: string;
  name: string;
  status: ExperimentStatus;
  config: ExperimentConfig;
  created_at: string;
  results: Record<ManagerType, ManagerResult> | null;
}

export interface AgentProfile {
  id: string;
  name: string;
  role: string;
  skills: string[];
  avg_service_ms: number;
  capacity: number;
  reliability: number;
  memory_mb: number;
}

export interface TaskMixEntry {
  type: string;
  share: number;
}

export interface WorkloadProfile {
  id: string;
  name: string;
  description: string;
  num_tasks: number;
  arrival: string;
  arrival_rate_tps: number;
  deadline_ms: number;
  mix: TaskMixEntry[];
  arrival_series: number[];
}
