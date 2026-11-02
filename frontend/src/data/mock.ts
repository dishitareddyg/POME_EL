// Illustrative mock data. Values are NOT experimental results.
import type {
  AgentProfile,
  ExperimentRecord,
  ExperimentStatus,
  ManagerResult,
  WorkloadProfile,
} from "../types/experiment";

const res = (
  avg_latency_ms: number,
  p95_latency_ms: number,
  throughput_tps: number,
  success_rate: number,
  cpu_pct: number,
  memory_mb: number,
): ManagerResult => ({ avg_latency_ms, p95_latency_ms, throughput_tps, success_rate, cpu_pct, memory_mb });

const exp = (
  id: number,
  name: string,
  status: ExperimentStatus,
  num_workers: number,
  num_tasks: number,
  workload: string,
  created_at: string,
  conv?: ManagerResult,
  pos?: ManagerResult,
): ExperimentRecord => ({
  id,
  name,
  status,
  num_workers,
  num_tasks,
  random_seed: 40 + id,
  workload,
  created_at,
  results: conv && pos ? { conventional: conv, posdcorb: pos } : null,
});

export const MOCK_EXPERIMENTS: ExperimentRecord[] = [
  exp(13, "Burst load, 16 workers", "running", 16, 800, "Burst", "2026-10-28T09:10:00"),
  exp(12, "Replication of #11 (seed 7)", "pending", 12, 600, "Mixed", "2026-10-27T16:42:00"),
  exp(11, "Mixed workload baseline", "completed", 12, 600, "Mixed", "2026-10-26T11:05:00",
    res(2140, 4310, 3.8, 90.4, 62, 512), res(1380, 2650, 5.1, 95.2, 58, 498)),
  exp(10, "Scaling: 24 workers", "completed", 24, 1200, "Mixed", "2026-10-25T14:20:00",
    res(2460, 5120, 4.4, 88.7, 71, 560), res(1510, 2890, 6.3, 94.1, 66, 541)),
  exp(9, "Scaling: 16 workers", "completed", 16, 1200, "Mixed", "2026-10-24T10:30:00",
    res(2290, 4780, 4.1, 89.9, 67, 540), res(1450, 2760, 5.7, 94.8, 62, 524)),
  exp(8, "Scaling: 8 workers", "completed", 8, 1200, "Mixed", "2026-10-23T15:12:00",
    res(2010, 3980, 3.2, 91.2, 54, 486), res(1620, 3120, 4.1, 93.6, 52, 478)),
  exp(7, "Failure injection 5%", "completed", 12, 600, "Mixed", "2026-10-21T09:45:00",
    res(2380, 5340, 3.6, 84.1, 64, 528), res(1590, 3010, 4.9, 91.7, 60, 512)),
  exp(6, "Failure injection 10%", "failed", 12, 600, "Mixed", "2026-10-20T13:05:00"),
  exp(5, "Deadline-sensitive tasks", "completed", 12, 500, "Deadline", "2026-10-18T11:40:00",
    res(1980, 4210, 3.9, 86.3, 61, 505), res(1290, 2480, 5.4, 93.9, 57, 492)),
  exp(4, "Long-horizon planning tasks", "completed", 12, 300, "Planning", "2026-10-16T16:00:00",
    res(2870, 6120, 2.7, 87.5, 59, 521), res(1840, 3560, 3.9, 92.4, 56, 509)),
  exp(3, "Retrieval-heavy tasks", "completed", 12, 600, "Retrieval", "2026-10-14T10:10:00",
    res(1760, 3490, 4.6, 92.8, 66, 534), res(1540, 2970, 5.0, 94.6, 63, 520)),
  exp(2, "Pilot run (seed sweep)", "completed", 8, 400, "Mixed", "2026-10-12T09:00:00",
    res(2190, 4450, 3.7, 89.6, 60, 497), res(1470, 2820, 5.0, 94.0, 57, 486)),
  exp(1, "Smoke test", "completed", 4, 100, "Steady", "2026-10-10T17:30:00",
    res(1840, 3620, 2.9, 91.0, 48, 462), res(1340, 2540, 3.8, 95.0, 46, 455)),
];

export const MOCK_AGENTS: AgentProfile[] = [
  { id: "w-01", name: "Retriever-A", role: "Retrieval", skills: ["search", "rank"], avg_service_ms: 620, capacity: 4, reliability: 98.1, memory_mb: 180 },
  { id: "w-02", name: "Retriever-B", role: "Retrieval", skills: ["search", "dedupe"], avg_service_ms: 710, capacity: 4, reliability: 97.4, memory_mb: 176 },
  { id: "w-03", name: "Coder-A", role: "Code generation", skills: ["python", "tests"], avg_service_ms: 1480, capacity: 2, reliability: 93.6, memory_mb: 310 },
  { id: "w-04", name: "Coder-B", role: "Code generation", skills: ["python", "refactor"], avg_service_ms: 1390, capacity: 2, reliability: 94.2, memory_mb: 305 },
  { id: "w-05", name: "Analyst-A", role: "Data analysis", skills: ["stats", "tables"], avg_service_ms: 980, capacity: 3, reliability: 96.0, memory_mb: 240 },
  { id: "w-06", name: "Summarizer-A", role: "Summarization", skills: ["summary", "extract"], avg_service_ms: 540, capacity: 5, reliability: 98.8, memory_mb: 150 },
  { id: "w-07", name: "Reviewer-A", role: "Verification", skills: ["check", "critique"], avg_service_ms: 860, capacity: 3, reliability: 95.3, memory_mb: 205 },
  { id: "w-08", name: "Planner-A", role: "Task decomposition", skills: ["plan", "dependencies"], avg_service_ms: 1210, capacity: 2, reliability: 94.9, memory_mb: 260 },
];

export const MOCK_WORKLOADS: WorkloadProfile[] = [
  {
    id: "mixed", name: "Mixed", description: "Balanced mix of all task types with Poisson arrivals.",
    num_tasks: 600, arrival: "Poisson", arrival_rate_tps: 6, deadline_ms: 8000,
    mix: [
      { type: "Retrieval", share: 25 }, { type: "Code", share: 20 }, { type: "Analysis", share: 20 },
      { type: "Summarization", share: 20 }, { type: "Planning", share: 15 },
    ],
    arrival_series: [58, 63, 61, 66, 59, 62, 64, 60, 57, 65, 61, 63],
  },
  {
    id: "burst", name: "Burst", description: "Idle periods interrupted by short high-rate bursts.",
    num_tasks: 800, arrival: "Burst", arrival_rate_tps: 12, deadline_ms: 8000,
    mix: [
      { type: "Retrieval", share: 30 }, { type: "Code", share: 15 }, { type: "Analysis", share: 20 },
      { type: "Summarization", share: 25 }, { type: "Planning", share: 10 },
    ],
    arrival_series: [12, 10, 148, 152, 14, 11, 9, 141, 155, 13, 10, 12],
  },
  {
    id: "deadline", name: "Deadline", description: "Tight per-task deadlines to stress scheduling decisions.",
    num_tasks: 500, arrival: "Poisson", arrival_rate_tps: 5, deadline_ms: 3500,
    mix: [
      { type: "Retrieval", share: 30 }, { type: "Code", share: 10 }, { type: "Analysis", share: 25 },
      { type: "Summarization", share: 30 }, { type: "Planning", share: 5 },
    ],
    arrival_series: [48, 52, 50, 49, 53, 47, 51, 50, 52, 48, 51, 49],
  },
  {
    id: "planning", name: "Planning", description: "Long-horizon tasks with dependency chains.",
    num_tasks: 300, arrival: "Steady", arrival_rate_tps: 3, deadline_ms: 15000,
    mix: [
      { type: "Retrieval", share: 10 }, { type: "Code", share: 25 }, { type: "Analysis", share: 15 },
      { type: "Summarization", share: 10 }, { type: "Planning", share: 40 },
    ],
    arrival_series: [30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  },
];
