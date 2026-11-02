/**
 * TypeScript mirror of the backend ManagementReport / ResourceBudget /
 * POSDCORBConfig structures produced by the POSDCORB Adaptive Manager.
 *
 * Source of truth: backend/app/managers/posdcorb.py — keep both in sync.
 * The report is embedded in the last ExecutionTrace decision's metadata
 * under the key "management_report" (see ManagementReport.as_dict()).
 */

// ---------------------------------------------------------------------------
// ManagementReport.as_dict() shape
// ---------------------------------------------------------------------------

export interface ManagementReportWorkload {
  total_tasks: number;
  pending_tasks: number;
  running_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
}

export interface ManagementReportAgents {
  initial_agent_count: number;
  active_agent_count: number;
  idle_agent_count: number;
  busy_agent_count: number;
  dynamically_activated_count: number;
  failed_agent_count: number;
}

export interface ManagementReportPerformance {
  tasks_completed_by_agent: Record<string, number>;
  tasks_failed_by_agent: Record<string, number>;
  total_retries: number;
  avg_task_latency_ms: number;
}

export interface ManagementReportManagement {
  plan_created: boolean;
  plan_updates: number;
  staffing_decisions: number;
  reassignment_count: number;
  replan_count: number;
  coordination_events: number;
  parallel_group_count: number;
  directives_issued: number;
  recovery_count: number;
}

export interface ManagementReportBudget {
  consumed_wall_ms: number;
  consumed_decisions: number;
  consumed_activation_ms: number;
  budget_warnings: number;
}

export interface ManagementReport {
  timestamp: string;
  workload: ManagementReportWorkload;
  agents: ManagementReportAgents;
  performance: ManagementReportPerformance;
  management: ManagementReportManagement;
  budget: ManagementReportBudget;
}

// ---------------------------------------------------------------------------
// ResourceBudget.as_dict() shape (initial RESOURCE_CHECK event metadata)
// ---------------------------------------------------------------------------

export interface ResourceBudgetSnapshot {
  max_agents: number;
  max_execution_time_ms: number;
  max_manager_decisions: number;
  max_llm_calls: number;
  max_tokens: number;
  agent_activation_cost_ms: number;
  consumed_wall_ms: number;
  consumed_decisions: number;
  consumed_llm_calls: number;
  consumed_tokens: number;
  consumed_activation_ms: number;
  wall_fraction_used: number;
  decision_fraction_used: number;
  overall_fraction_used: number;
}

// ---------------------------------------------------------------------------
// POSDCORBConfig — every threshold is configurable (no hard-coded policy)
// ---------------------------------------------------------------------------

export interface PosdcorbConfig {
  // Staffing
  agent_activation_cost: number;
  activation_pending_threshold: number;
  activation_utilisation_threshold: number;
  max_agent_count: number | null;
  // Replanning triggers
  replan_on_agent_failure: boolean;
  replan_on_task_failure: boolean;
  replan_slow_task_multiplier: number;
  replan_on_budget_critical: boolean;
  budget_critical_threshold: number;
  // Failure recovery / deadlock
  agent_failure_streak_threshold: number;
  deadlock_detection: boolean;
  // Budgets
  max_manager_decisions: number;
  max_llm_calls: number;
  token_budget: number;
  max_wall_ms: number;
  // Misc
  decision_latency_mean_ms: number;
  decision_latency_std_ms: number;
  min_group_size: number;
  max_retries: number;
}

// ---------------------------------------------------------------------------
// POSDCORB event types emitted into the ExecutionTrace
// ---------------------------------------------------------------------------

export type PosdcorbEventType =
  // Planning
  | "PLAN_CREATED"
  | "PLAN_UPDATED"
  // Organizing
  | "TASK_GROUP_CREATED"
  | "TASK_REORGANIZED"
  // Staffing
  | "AGENT_ACTIVATED"
  | "AGENT_REASSIGNED"
  // Directing
  | "TASK_DIRECTED"
  // Coordinating
  | "TASK_SCHEDULED"
  | "TASK_STARTED"
  | "TASK_COMPLETED"
  | "TASK_REASSIGNED"
  | "DEPENDENCY_WAIT"
  // Reporting
  | "STATUS_UPDATED"
  // Budgeting
  | "RESOURCE_CHECK"
  | "BUDGET_WARNING"
  // Failure
  | "AGENT_FAILED"
  | "TASK_FAILED"
  | "RECOVERY_STARTED"
  | "RECOVERY_COMPLETED";
