/**
 * Workload types.
 *
 * Mirror of backend/app/models/workload.py — keep both files in sync.
 *
 * The WorkloadGeneratorConfig drives deterministic task generation.
 * The resulting TaskGraph is passed identically to BOTH managers during
 * comparative experiments — no manager-specific mutation is done here.
 */

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

export type TaskType =
  | "Data Filtering"
  | "Sorting"
  | "Statistical Computation"
  | "Aggregation"
  | "Anomaly Detection"
  | "Validation"
  | "Summarization";

export type TaskComplexity = "low" | "medium" | "high";

export type TaskPriority = "critical" | "high" | "medium" | "low";

export type TaskStatus =
  | "pending"
  | "ready"        // all dependencies met, waiting for an agent
  | "running"
  | "completed"
  | "failed"
  | "blocked";     // waiting on one or more dependencies

// ---------------------------------------------------------------------------
// Required capability per task type
// Maps deterministically: same task type → same capability requirement.
// ---------------------------------------------------------------------------

export const TASK_TYPE_CAPABILITY: Record<TaskType, string> = {
  "Data Filtering":          "text-processing",
  "Sorting":                 "batch-execution",
  "Statistical Computation": "statistics",
  "Aggregation":             "data-visualization",
  "Anomaly Detection":       "anomaly-detection",
  "Validation":              "quality-check",
  "Summarization":           "text-summarization",
};

// Estimated base workload (ms) per complexity tier
export const BASE_WORKLOAD_MS: Record<TaskComplexity, Record<TaskType, number>> = {
  low: {
    "Data Filtering":          200,
    "Sorting":                 150,
    "Statistical Computation": 400,
    "Aggregation":             250,
    "Anomaly Detection":       350,
    "Validation":              180,
    "Summarization":           300,
  },
  medium: {
    "Data Filtering":          600,
    "Sorting":                 500,
    "Statistical Computation": 1200,
    "Aggregation":             800,
    "Anomaly Detection":       1100,
    "Validation":              550,
    "Summarization":           900,
  },
  high: {
    "Data Filtering":          1800,
    "Sorting":                 1400,
    "Statistical Computation": 3500,
    "Aggregation":             2200,
    "Anomaly Detection":       3000,
    "Validation":              1600,
    "Summarization":           2600,
  },
};

// ---------------------------------------------------------------------------
// Core Task model — every field maps to a backend column / Pydantic field
// ---------------------------------------------------------------------------

export interface Task {
  /** Stable identifier, e.g. "task-001". Generated deterministically. */
  id: string;

  /** Sequential index within the workload (0-based). */
  index: number;

  /** Computational task type. */
  task_type: TaskType;

  /** Complexity tier. */
  complexity: TaskComplexity;

  /** Capability a capable agent must possess to handle this task. */
  required_capability: string;

  /** Priority tier. */
  priority: TaskPriority;

  /**
   * Estimated duration in ms.
   * Derived from BASE_WORKLOAD_MS[complexity][task_type] ± jitter.
   */
  estimated_workload_ms: number;

  /**
   * IDs of tasks that must complete before this task can start.
   * Empty array = no dependencies (task is immediately ready).
   */
  dependency_ids: string[];

  // -- Runtime state (set to initial values; simulation engine overwrites) ---

  status: TaskStatus;

  /** ID of the agent assigned to this task; null = unassigned. */
  assigned_agent_id: string | null;

  /** ISO-8601 start time; null until the agent picks it up. */
  start_time: string | null;

  /** ISO-8601 completion time; null until finished. */
  completion_time: string | null;
}

// ---------------------------------------------------------------------------
// TaskGraph — the whole workload as a DAG
// ---------------------------------------------------------------------------

export interface TaskGraph {
  /** Unique opaque identifier derived from the generator config + seed. */
  graph_id: string;

  /** The RNG seed used. Replay with same seed → identical graph. */
  seed: number;

  /** Ordered list of tasks. Index order = topological order (guaranteed). */
  tasks: Task[];

  /** Pre-computed adjacency list: task id → list of successor task ids. */
  edges: Record<string, string[]>;

  /** Generation timestamp. */
  generated_at: string;

  /** Config snapshot (for auditability). */
  config: WorkloadGeneratorConfig;
}

// ---------------------------------------------------------------------------
// Generator configuration
// ---------------------------------------------------------------------------

export interface PriorityDistribution {
  critical: number;  // 0-100, must sum to 100 with others
  high:     number;
  medium:   number;
  low:      number;
}

export interface WorkloadGeneratorConfig {
  /** Total tasks to generate. Range: 1–500. */
  num_tasks: number;

  /** Complexity tier applied to all generated tasks. */
  complexity: TaskComplexity;

  /**
   * Percentage of tasks (other than the first) that will have at least one
   * dependency on a previously generated task. Range: 0–100.
   */
  dependency_percentage: number;

  /** Priority distribution. Values must sum to 100. */
  priority_distribution: PriorityDistribution;

  /** RNG seed for reproducibility. Same seed → same graph. */
  seed: number;
}
