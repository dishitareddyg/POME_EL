/**
 * Deterministic workload generator.
 *
 * Implements a seeded LCG (linear congruential generator) so the exact same
 * TaskGraph is produced whenever the same WorkloadGeneratorConfig (including
 * seed) is supplied.  No external library is required.
 *
 * Algorithm outline
 * -----------------
 * 1. Seed the LCG.
 * 2. For each task i (0..n-1):
 *    a. Pick task_type from the 7 types uniformly.
 *    b. Apply priority according to the configured distribution.
 *    c. Derive required_capability from TASK_TYPE_CAPABILITY[task_type].
 *    d. Sample estimated_workload_ms ± 20% jitter.
 *    e. With probability dependency_percentage/100, choose 1-3 random
 *       predecessor tasks from the already-generated tasks (i < current).
 *       This guarantees the DAG is acyclic by construction.
 * 3. Build the forward edges adjacency list.
 * 4. Return a TaskGraph.
 *
 * The graph is passed identically to both managers — no manager-specific
 * state is set here.
 */

import type {
  PriorityDistribution,
  Task,
  TaskComplexity,
  TaskGraph,
  TaskPriority,
  TaskType,
  WorkloadGeneratorConfig,
} from "../types/workload";
import {
  BASE_WORKLOAD_MS,
  TASK_TYPE_CAPABILITY,
} from "../types/workload";

// ---------------------------------------------------------------------------
// Seeded LCG — Numerical Recipes constants (fast, good distribution)
// ---------------------------------------------------------------------------

class LCG {
  private state: number;

  constructor(seed: number) {
    // Ensure the seed is a positive 32-bit integer
    this.state = (seed >>> 0) || 1;
  }

  /** Returns a float in [0, 1). */
  next(): number {
    // Knuth's LCG constants
    this.state = Math.imul(1664525, this.state) + 1013904223;
    this.state = this.state >>> 0; // keep as unsigned 32-bit
    return this.state / 0x100000000;
  }

  /** Returns an integer in [0, max). */
  nextInt(max: number): number {
    return Math.floor(this.next() * max);
  }

  /** Returns a float in [min, max]. */
  nextFloat(min: number, max: number): number {
    return min + this.next() * (max - min);
  }

  /** Pick one item uniformly from an array. */
  pick<T>(arr: T[]): T {
    return arr[this.nextInt(arr.length)];
  }

  /** Pick 1..count unique indices from [0, poolSize). */
  pickUnique(poolSize: number, count: number): number[] {
    const result: number[] = [];
    const available = Array.from({ length: poolSize }, (_, i) => i);
    const take = Math.min(count, poolSize);
    for (let i = 0; i < take; i++) {
      const idx = this.nextInt(available.length);
      result.push(available[idx]);
      available.splice(idx, 1);
    }
    return result;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TASK_TYPES: TaskType[] = [
  "Data Filtering",
  "Sorting",
  "Statistical Computation",
  "Aggregation",
  "Anomaly Detection",
  "Validation",
  "Summarization",
];

function samplePriority(rng: LCG, dist: PriorityDistribution): TaskPriority {
  const roll = rng.next() * 100;
  if (roll < dist.critical) return "critical";
  if (roll < dist.critical + dist.high) return "high";
  if (roll < dist.critical + dist.high + dist.medium) return "medium";
  return "low";
}

function jitteredWorkload(rng: LCG, base: number): number {
  // ± 20% jitter
  return Math.round(base * rng.nextFloat(0.8, 1.2));
}

function fingerprint(cfg: WorkloadGeneratorConfig): string {
  const p = cfg.priority_distribution;
  const priorities = `p${p.critical}_${p.high}_${p.medium}_${p.low}`;
  return `g-${cfg.seed}-n${cfg.num_tasks}-d${cfg.dependency_percentage}-${cfg.complexity}-${priorities}`;
}

// ---------------------------------------------------------------------------
// Main generator
// ---------------------------------------------------------------------------

export function generateWorkload(config: WorkloadGeneratorConfig): TaskGraph {
  const rng = new LCG(config.seed);
  const tasks: Task[] = [];

  const {
    num_tasks,
    complexity,
    dependency_percentage,
    priority_distribution,
  } = config;

  for (let i = 0; i < num_tasks; i++) {
    const id = `task-${String(i + 1).padStart(4, "0")}`;
    const task_type = rng.pick(TASK_TYPES) as TaskType;
    const priority = samplePriority(rng, priority_distribution);
    const base = BASE_WORKLOAD_MS[complexity][task_type];
    const estimated_workload_ms = jitteredWorkload(rng, base);

    // Dependency sampling (only possible for tasks after the first)
    let dependency_ids: string[] = [];
    if (i > 0 && rng.next() * 100 < dependency_percentage) {
      // How many predecessors: 1, 2, or 3 (weighted toward fewer)
      const maxDeps = Math.min(i, 3);
      const numDeps = rng.nextInt(maxDeps) + 1;
      const predIndices = rng.pickUnique(i, numDeps);
      dependency_ids = predIndices.map((pi) => tasks[pi].id);
    }

    tasks.push({
      id,
      index: i,
      task_type,
      complexity,
      required_capability: TASK_TYPE_CAPABILITY[task_type],
      priority,
      estimated_workload_ms,
      dependency_ids,
      // Runtime state — simulation engine will overwrite these
      status: dependency_ids.length === 0 ? "ready" : "blocked",
      assigned_agent_id: null,
      start_time: null,
      completion_time: null,
    });
  }

  // Build forward adjacency list (predecessor → successors)
  const edges: Record<string, string[]> = {};
  for (const task of tasks) {
    if (!edges[task.id]) edges[task.id] = [];
    for (const depId of task.dependency_ids) {
      if (!edges[depId]) edges[depId] = [];
      edges[depId].push(task.id);
    }
  }

  return {
    graph_id: fingerprint(config),
    seed: config.seed,
    tasks,
    edges,
    generated_at: new Date().toISOString(),
    config,
  };
}

// ---------------------------------------------------------------------------
// Default config (matches the stub that will live in the backend)
// ---------------------------------------------------------------------------

export const DEFAULT_CONFIG: WorkloadGeneratorConfig = {
  num_tasks: 30,
  complexity: "medium",
  dependency_percentage: 40,
  priority_distribution: { critical: 10, high: 25, medium: 45, low: 20 },
  seed: 42,
};

// ---------------------------------------------------------------------------
// Derived statistics helpers
// ---------------------------------------------------------------------------

export interface WorkloadStats {
  total: number;
  with_deps: number;
  no_deps: number;
  avg_deps: number;
  max_deps: number;
  max_depth: number;          // longest path in the DAG (critical path length)
  by_type: Record<string, number>;
  by_priority: Record<TaskPriority, number>;
  by_complexity: Record<TaskComplexity, number>;
  total_estimated_ms: number;
  avg_estimated_ms: number;
}

export function computeStats(graph: TaskGraph): WorkloadStats {
  const { tasks, edges } = graph;

  const by_type: Record<string, number> = {};
  const by_priority: Record<TaskPriority, number> = { critical: 0, high: 0, medium: 0, low: 0 };
  const by_complexity: Record<TaskComplexity, number> = { low: 0, medium: 0, high: 0 };
  let with_deps = 0;
  let total_deps = 0;
  let max_deps = 0;
  let total_estimated_ms = 0;

  for (const t of tasks) {
    by_type[t.task_type] = (by_type[t.task_type] ?? 0) + 1;
    by_priority[t.priority]++;
    by_complexity[t.complexity]++;
    total_estimated_ms += t.estimated_workload_ms;
    if (t.dependency_ids.length > 0) {
      with_deps++;
      total_deps += t.dependency_ids.length;
      if (t.dependency_ids.length > max_deps) max_deps = t.dependency_ids.length;
    }
  }

  // Critical path depth via BFS/memoisation from root nodes
  const depth: Record<string, number> = {};
  const inDegree: Record<string, number> = {};
  for (const t of tasks) inDegree[t.id] = t.dependency_ids.length;

  const queue = tasks.filter((t) => t.dependency_ids.length === 0).map((t) => t.id);
  for (const id of queue) depth[id] = 1;

  // Topological BFS
  const processed = new Set<string>();
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (processed.has(current)) continue;
    processed.add(current);
    for (const succ of (edges[current] ?? [])) {
      depth[succ] = Math.max(depth[succ] ?? 0, (depth[current] ?? 0) + 1);
      inDegree[succ]--;
      if (inDegree[succ] === 0) queue.push(succ);
    }
  }

  const max_depth = Object.values(depth).reduce((a, b) => Math.max(a, b), 0);

  return {
    total: tasks.length,
    with_deps,
    no_deps: tasks.length - with_deps,
    avg_deps: tasks.length ? total_deps / tasks.length : 0,
    max_deps,
    max_depth,
    by_type,
    by_priority,
    by_complexity,
    total_estimated_ms,
    avg_estimated_ms: tasks.length ? total_estimated_ms / tasks.length : 0,
  };
}
