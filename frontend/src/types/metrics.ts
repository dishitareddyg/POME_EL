/**
 * TypeScript mirrors of the backend Module 8 metric models.
 *
 * Source of truth: backend/app/metrics/models.py
 * Keep in sync when backend models change.
 *
 * These types are returned by:
 *   POST /api/metrics/compare
 *   POST /api/metrics/compute
 */

// ---------------------------------------------------------------------------
// Sub-metric objects inside DerivedMetrics
// ---------------------------------------------------------------------------

export interface PerformanceMetrics {
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  pending_tasks: number;
  total_execution_latency_ms: number;
  avg_task_latency_ms: number;
  median_task_latency_ms: number;
  min_task_latency_ms: number;
  max_task_latency_ms: number;
  throughput_tasks_per_sec: number;
  task_success_rate: number;   // [0, 1]
  task_completion_rate: number; // [0, 1]
}

export interface SingleTaskMetrics {
  task_id: string;
  status: "completed" | "failed" | "pending" | "unknown";
  assigned_agent_id: string | null;
  queue_time_ms: number;
  execution_time_ms: number;
  total_latency_ms: number;
  retry_count: number;
  reassignment_count: number;
}

/** Keyed by task_id */
export type TaskMetrics = Record<string, SingleTaskMetrics>;

export interface SingleAgentMetrics {
  agent_id: string;
  agent_name: string;
  origin: "initial" | "dynamic";
  tasks_completed: number;
  tasks_failed: number;
  busy_time_ms: number;
  available_time_ms: number;
  agent_utilization: number;  // [0, 1]
  is_activated: boolean;
}

export interface AgentMetrics {
  initial_agent_count: number;
  dynamic_agent_count: number;
  total_active_agents: number;
  avg_agent_utilization: number;  // [0, 1]
  max_simultaneous_agents: number;
  activation_count: number;
  failed_agent_count: number;
  per_agent: Record<string, SingleAgentMetrics>;
}

export interface ManagementMetrics {
  total_decisions: number;
  management_time_ms: number;
  avg_decision_latency_ms: number;
  planning_events: number;
  staffing_decisions: number;
  coordination_events: number;
  replanning_events: number;
  reassignment_count: number;
  dependency_wait_events: number;
  coordination_overhead_ratio: number;  // [0, 1]
}

export interface DynamicStaffingMetrics {
  initial_agent_count: number;
  max_active_agents: number;
  avg_active_agents: number;
  activation_count: number;
  activation_rate: number;
  estimated_activation_cost_ms: number;
  tasks_per_active_agent: number;
}

export interface ReliabilityMetrics {
  agent_failure_count: number;
  task_failure_count: number;
  retry_count: number;
  reassignment_count: number;
  recovery_count: number;
  recovery_success_rate: number;  // [0, 1]
  avg_recovery_time_ms: number;
  tasks_lost_due_to_failures: number;
}

export interface CostMetrics {
  budget_decisions_allocated: number;
  budget_decisions_consumed: number;
  budget_decisions_utilization: number;
  budget_wall_ms_allocated: number;
  budget_wall_ms_consumed: number;
  budget_wall_ms_utilization: number;
  activation_cost_ms: number;
  coordination_cost_ms: number;
  llm_calls_used: number;
  llm_calls_allocated: number;
  tokens_used: number;
  tokens_allocated: number;
}

export interface ResourceMonitorMetrics {
  available: boolean;
  avg_cpu_percent: number;
  peak_cpu_percent: number;
  avg_memory_mb: number;
  peak_memory_mb: number;
  sample_count: number;
  unavailable_reason: string;
}

// ---------------------------------------------------------------------------
// Full per-run metric bundle (DerivedMetrics.as_dict())
// ---------------------------------------------------------------------------

export interface DerivedMetrics {
  manager_type: string;
  experiment_id: string;
  seed: number;
  workload_id: string;
  performance: PerformanceMetrics;
  tasks: TaskMetrics;
  agents: AgentMetrics;
  management: ManagementMetrics;
  staffing: DynamicStaffingMetrics;
  reliability: ReliabilityMetrics;
  cost: CostMetrics;
  resource_monitor: ResourceMonitorMetrics;
}

// ---------------------------------------------------------------------------
// Comparison row (one metric, both managers)
// ---------------------------------------------------------------------------

export type BetterManager = "conventional" | "posdcorb" | "tie" | "context_dependent";

export interface ComparisonRow {
  metric: string;
  conventional_value: number;
  posdcorb_value: number;
  absolute_difference: number;
  pct_difference: number;      // signed: positive = POSDCORB larger
  higher_is_better: boolean | null;
  better_manager: BetterManager;
  interpretation: string;
}

// ---------------------------------------------------------------------------
// POST /api/metrics/compare request / response
// ---------------------------------------------------------------------------

/**
 * Matches the backend CompareRequest model.
 * ``experiments`` keys must be "conventional" and "posdcorb".
 * Values are ExperimentResult summary_dict() objects.
 */
export interface CompareRequest {
  experiments: Record<string, Record<string, unknown>>;
  trace_decisions?: Record<string, unknown[]>;
}

export interface CompareResponse {
  workload_id: string;
  seed: number;
  rows: ComparisonRow[];
  conventional_metrics: DerivedMetrics | null;
  posdcorb_metrics: DerivedMetrics | null;
}
