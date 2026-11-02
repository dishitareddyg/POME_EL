/**
 * TypeScript mirrors of the POSDCORB ablation study API.
 *
 * Source of truth: backend/app/api/ablation.py and
 *                  backend/app/ablation/runner.py
 *
 * Endpoints:
 *   POST /api/ablation/study
 *   GET  /api/ablation/configurations
 */

// ---------------------------------------------------------------------------
// Metric keys
// ---------------------------------------------------------------------------

export type AblationMetricKey =
  | "latency_ms"
  | "throughput_tasks_per_sec"
  | "success_rate"
  | "cpu_utilization"
  | "coordination_overhead"
  | "agent_utilization"
  | "activation_count";

export interface AblationMetricSpec {
  key: AblationMetricKey;
  label: string;
  unit: string;
  higher_is_better: boolean | null;
  description: string;
}

export interface AblationMetricValue {
  /** Aggregated mean across repetitions. */
  value: number;
  mean: number;
  std: number;
  min: number;
  max: number;
  n: number;
}

export type ImprovementDirection = "better" | "worse" | "equal" | "n/a" | "higher" | "lower";

export interface AblationImprovement {
  metric: AblationMetricKey;
  reference_value: number;
  value: number;
  absolute_delta: number;
  pct_change: number | null;
  /**
   * Directional improvement: positive always means "better than the
   * reference". Null for context-dependent metrics (activation count).
   */
  improvement_pct: number | null;
  higher_is_better: boolean | null;
  direction: ImprovementDirection;
}

// ---------------------------------------------------------------------------
// Raw run records
// ---------------------------------------------------------------------------

export interface AblationRawRun {
  config_id: string;
  repetition: number;
  seed: number;
  metrics: Record<AblationMetricKey, number>;
  completed_tasks: number;
  failed_tasks: number;
  pending_tasks: number;
  total_wall_ms: number;
  total_decisions: number;
  termination_reason: string;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Configurations
// ---------------------------------------------------------------------------

export interface AblationConfiguration {
  id: string;
  label: string;
  short_label: string;
  description: string;
  manager_type: "conventional" | "posdcorb" | string;
  enabled_principles: string[];
  principle_labels: string[];
  principle_codes: string;
  adaptive_mechanisms: boolean;
  added_principle: string | null;
  added_label: string;
  is_baseline: boolean;
  is_full: boolean;

  metrics: Record<AblationMetricKey, AblationMetricValue>;
  improvements_vs_baseline: Record<string, AblationImprovement>;
  marginal_vs_previous: Record<string, AblationImprovement>;
  marginal_composite_improvement: number | null;
  composite_improvement: number | null;
  rank: number | null;
  raw_runs: AblationRawRun[];
  errors: string[];
  /** Mean event counts proving each mechanism ran (diagnostic). */
  mechanism_activity: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Ranking / principle contributions
// ---------------------------------------------------------------------------

export interface AblationRankingRow {
  rank: number;
  config_id: string;
  label: string;
  short_label: string;
  composite_improvement: number | null;
  latency_improvement: number | null;
  throughput_improvement: number | null;
  success_improvement: number | null;
  cpu_improvement: number | null;
  overhead_improvement: number | null;
  agent_utilization_improvement: number | null;
}

export interface PrincipleEffect {
  absolute_delta: number;
  improvement_pct: number | null;
  direction: ImprovementDirection;
}

export interface AblationPrincipleContribution {
  principle: string;
  label: string;
  config_id: string;
  config_label: string;
  compared_to: string;
  composite_improvement: number | null;
  effects: Record<AblationMetricKey, PrincipleEffect>;
}

// ---------------------------------------------------------------------------
// Conditions (fairness evidence)
// ---------------------------------------------------------------------------

export interface AblationAgentPoolConditions {
  total: number;
  initial: number;
  dynamic: number;
  signature: Array<Record<string, unknown>>;
}

export interface AblationSimulationConditions {
  base_seed: number;
  repetitions: number;
  seeds: number[];
  max_decisions: number;
  max_wall_ms: number;
  max_agents: number | null;
  failure_probability: number | null;
}

export interface AblationConditions {
  workload: Record<string, unknown>;
  agent_pool: AblationAgentPoolConditions;
  simulation: AblationSimulationConditions;
  fingerprint: string;
  identical_inputs: boolean;
  note: string;
}

// ---------------------------------------------------------------------------
// Request / response
// ---------------------------------------------------------------------------

export interface AblationWorkloadInput {
  num_tasks: number;
  complexity: "low" | "medium" | "high";
  dependency_percentage: number;
  seed?: number;
}

export interface AblationStudyRequest {
  agents?: unknown[];
  use_saved_agents?: boolean;
  workload: AblationWorkloadInput;
  repetitions: number;
  base_seed: number;
  max_decisions: number;
  max_wall_ms: number;
  max_agents?: number | null;
  failure_probability: number;
  configurations?: Array<Record<string, unknown>> | null;
}

export interface AblationStudyResponse {
  study_id: string;
  conditions: AblationConditions;
  configurations: AblationConfiguration[];
  ranking: AblationRankingRow[];
  principle_contributions: AblationPrincipleContribution[];
  metric_specs: AblationMetricSpec[];
  total_runs: number;
  elapsed_s: number;
  errors: string[];
  agent_source: "request" | "database" | "default" | string;
  baseline_config_id: string;
}

export interface AblationConfigurationsResponse {
  configurations: Array<Omit<
    AblationConfiguration,
    | "metrics"
    | "improvements_vs_baseline"
    | "marginal_vs_previous"
    | "marginal_composite_improvement"
    | "composite_improvement"
    | "rank"
    | "raw_runs"
    | "errors"
    | "mechanism_activity"
  >>;
  metric_specs: AblationMetricSpec[];
  baseline_config_id: string;
  default_workload: AblationWorkloadInput;
  notes: Record<string, string>;
}
