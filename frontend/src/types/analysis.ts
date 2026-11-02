/**
 * TypeScript mirrors of Module 10 backend analysis models.
 *
 * Source of truth: backend/app/api/analysis.py
 */

// ---------------------------------------------------------------------------
// Request
// ---------------------------------------------------------------------------

export interface BatchRequest {
  task_counts: number[];
  complexities: string[];
  dep_percentages: number[];
  failure_probs: number[];
  max_agents_list: number[];
  repetitions: number;
  base_seed: number;
  max_decisions: number;
  max_wall_ms: number;
}

// ---------------------------------------------------------------------------
// Response sub-types
// ---------------------------------------------------------------------------

export interface StatsSummary {
  mean: number;
  median: number;
  std: number;
  min: number;
  max: number;
  n: number;
}

export interface EffectSize {
  config_id: string;
  metric: string;
  task_count: number;
  complexity: string;
  dep_pct: number;
  failure_prob: number;
  max_agents: number;
  mean_conventional: number;
  mean_posdcorb: number;
  cohens_d: number;
  magnitude: "negligible" | "small" | "medium" | "large" | "not_estimable";
  interpretation: string;
  test_performed: string;   // '' | 'welch_t'
  p_value: number | null;
}

export interface RunRecord {
  run_id: string;
  config_id: string;
  rep: number;
  seed: number;
  task_count: number;
  complexity: string;
  dep_pct: number;
  failure_prob: number;
  max_agents: number;
  manager: string;
  completed_tasks: number;
  failed_tasks: number;
  success_rate: number;
  total_wall_ms: number;
  throughput_tasks_per_sec: number;
  avg_task_latency_ms: number;
  total_decisions: number;
  coordination_overhead_ratio: number;
  total_active_agents: number;
  activation_count: number;
  avg_agent_utilization: number;
  recovery_count: number;
  recovery_success_rate: number;
  termination_reason: string;
  error: string;
}

export interface ConfigStats {
  config_id: string;
  manager: string;
  task_count: number;
  complexity: string;
  dep_pct: number;
  failure_prob: number;
  max_agents: number;
  n_reps: number;
  success_rate: StatsSummary;
  throughput: StatsSummary;
  avg_latency_ms: StatsSummary;
  total_wall_ms: StatsSummary;
  coordination_overhead: StatsSummary;
  active_agents: StatsSummary;
  recovery_rate: StatsSummary;
  avg_agent_utilization: StatsSummary;
}

export interface ChartPoint {
  x_value: number;
  x_label: string;
  conventional_mean: number;
  conventional_std: number;
  conventional_min: number;
  conventional_max: number;
  posdcorb_mean: number;
  posdcorb_std: number;
  posdcorb_min: number;
  posdcorb_max: number;
  n_reps: number;
}

export interface BatchChartData {
  task_count_vs_latency: ChartPoint[];
  task_count_vs_throughput: ChartPoint[];
  task_count_vs_success_rate: ChartPoint[];
  task_count_vs_coordination_overhead: ChartPoint[];
  task_count_vs_active_agents: ChartPoint[];
  failure_prob_vs_recovery_rate: ChartPoint[];
  task_count_vs_cpu_note: string;
}

// ---------------------------------------------------------------------------
// Top-level response
// ---------------------------------------------------------------------------

export interface BatchResponse {
  batch_id: string;
  total_configs: number;
  total_runs: number;
  elapsed_s: number;
  raw_runs: RunRecord[];
  stats: ConfigStats[];
  effect_sizes: EffectSize[];
  chart_data: BatchChartData;
  errors: string[];
}
