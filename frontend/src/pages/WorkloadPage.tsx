/**
 * WorkloadPage — Workload Generator module.
 *
 * Sections
 * --------
 * 1. Generator configuration panel (task count, complexity, dependency %,
 *    priority distribution, seed)
 * 2. Workload statistics cards + charts
 * 3. Task dependency DAG visualisation
 * 4. Task table (sortable, filterable)
 *
 * The generated TaskGraph is the same object that will be handed to BOTH
 * managers during comparative experiments — no manager-specific mutation is
 * applied here.
 *
 * Determinism: changing the seed produces a different workload;
 *              re-clicking "Generate" with the same config is a no-op.
 */

import {
  AlertTriangle,
  Binary,
  ChevronDown,
  ChevronUp,
  Cpu,
  Database,
  Filter,
  Layers,
  ListChecks,
  RefreshCw,
  Shuffle,
  Sigma,
  Zap,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AXIS_TICK, GRID_STROKE, TOOLTIP_STYLE } from "../components/chartStyle";
import ChartCard from "../components/ChartCard";
import DagViz from "../components/DagViz";
import MetricCard from "../components/MetricCard";
import SectionHeader from "../components/SectionHeader";
import {
  computeStats,
  DEFAULT_CONFIG,
  generateWorkload,
} from "../services/workloadGenerator";
import type {
  TaskComplexity,
  TaskGraph,
  TaskPriority,
  TaskStatus,
  TaskType,
  WorkloadGeneratorConfig,
} from "../types/workload";

// ─── Constants ───────────────────────────────────────────────────────────────

const TASK_TYPES: TaskType[] = [
  "Data Filtering",
  "Sorting",
  "Statistical Computation",
  "Aggregation",
  "Anomaly Detection",
  "Validation",
  "Summarization",
];

const TYPE_ICON: Record<TaskType, React.ElementType> = {
  "Data Filtering":          Filter,
  "Sorting":                 Layers,
  "Statistical Computation": Sigma,
  "Aggregation":             Database,
  "Anomaly Detection":       AlertTriangle,
  "Validation":              Binary,
  "Summarization":           ListChecks,
};

const TYPE_COLOR: Record<TaskType, string> = {
  "Data Filtering":          "#6366f1",
  "Sorting":                 "#8b5cf6",
  "Statistical Computation": "#0ea5e9",
  "Aggregation":             "#10b981",
  "Anomaly Detection":       "#ef4444",
  "Validation":              "#f59e0b",
  "Summarization":           "#ec4899",
};

const PRIORITY_COLOR: Record<TaskPriority, string> = {
  critical: "#ef4444",
  high:     "#f97316",
  medium:   "#22c55e",
  low:      "#94a3b8",
};

const STATUS_COLOR: Record<TaskStatus, string> = {
  pending:   "#94a3b8",
  ready:     "#6366f1",
  blocked:   "#f59e0b",
  running:   "#3b82f6",
  completed: "#22c55e",
  failed:    "#ef4444",
};

const STATUS_BG: Record<TaskStatus, string> = {
  pending:   "bg-slate-100 text-slate-600",
  ready:     "bg-indigo-50 text-indigo-700",
  blocked:   "bg-amber-50 text-amber-700",
  running:   "bg-blue-50 text-blue-700",
  completed: "bg-emerald-50 text-emerald-700",
  failed:    "bg-red-50 text-red-700",
};

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatusPill({ status }: { status: TaskStatus }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize ${STATUS_BG[status]}`}>
      {status}
    </span>
  );
}

function PriorityBadge({ priority }: { priority: TaskPriority }) {
  const colors: Record<TaskPriority, string> = {
    critical: "bg-red-50 text-red-700 ring-red-200",
    high:     "bg-orange-50 text-orange-700 ring-orange-200",
    medium:   "bg-emerald-50 text-emerald-700 ring-emerald-200",
    low:      "bg-slate-100 text-slate-600 ring-slate-200",
  };
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset capitalize ${colors[priority]}`}>
      {priority}
    </span>
  );
}

function ComplexityDots({ complexity }: { complexity: TaskComplexity }) {
  const filled = complexity === "low" ? 1 : complexity === "medium" ? 2 : 3;
  return (
    <span className="flex items-center gap-0.5">
      {[1, 2, 3].map((i) => (
        <span
          key={i}
          className={`h-2 w-2 rounded-full ${i <= filled ? "bg-indigo-500" : "bg-slate-200"}`}
        />
      ))}
    </span>
  );
}

// ─── Config Panel ─────────────────────────────────────────────────────────────

const INPUT_CLS =
  "rounded border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-400 transition";

interface ConfigPanelProps {
  config: WorkloadGeneratorConfig;
  onChange: (c: WorkloadGeneratorConfig) => void;
  onGenerate: () => void;
  generating: boolean;
}

function ConfigPanel({ config, onChange, onGenerate, generating }: ConfigPanelProps) {
  const set = <K extends keyof WorkloadGeneratorConfig>(key: K, val: WorkloadGeneratorConfig[K]) =>
    onChange({ ...config, [key]: val });

  const setPriority = (key: keyof typeof config.priority_distribution, raw: string) => {
    const val = Math.max(0, Math.min(100, Number(raw)));
    onChange({ ...config, priority_distribution: { ...config.priority_distribution, [key]: val } });
  };

  const prioritySum = Object.values(config.priority_distribution).reduce((a, b) => a + b, 0);
  const priorityValid = prioritySum === 100;

  const randomSeed = () => set("seed", Math.floor(Math.random() * 100000));

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center gap-2">
        <Cpu className="h-4 w-4 text-indigo-500" />
        <h3 className="text-sm font-semibold text-slate-900">Generator configuration</h3>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {/* Task count */}
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-slate-600">Task count</span>
          <input
            id="cfg-num-tasks"
            type="number"
            min={1}
            max={500}
            value={config.num_tasks}
            onChange={(e) => set("num_tasks", Math.max(1, Math.min(500, Number(e.target.value))))}
            className={INPUT_CLS}
          />
          <span className="text-[10px] text-slate-400">Range: 1–500</span>
        </label>

        {/* Complexity */}
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-slate-600">Complexity</span>
          <select
            id="cfg-complexity"
            value={config.complexity}
            onChange={(e) => set("complexity", e.target.value as TaskComplexity)}
            className={INPUT_CLS}
          >
            <option value="low">Low (fast tasks)</option>
            <option value="medium">Medium (balanced)</option>
            <option value="high">High (expensive tasks)</option>
          </select>
        </label>

        {/* Dependency % */}
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-slate-600">
            Dependency density — {config.dependency_percentage}%
          </span>
          <input
            id="cfg-dep-pct"
            type="range"
            min={0}
            max={100}
            step={5}
            value={config.dependency_percentage}
            onChange={(e) => set("dependency_percentage", Number(e.target.value))}
            className="accent-indigo-600"
          />
          <div className="flex justify-between text-[10px] text-slate-400">
            <span>0% (no deps)</span>
            <span>50%</span>
            <span>100% (all dep)</span>
          </div>
        </label>

        {/* Seed */}
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-slate-600">Random seed</span>
          <div className="flex gap-1.5">
            <input
              id="cfg-seed"
              type="number"
              min={0}
              max={999999}
              value={config.seed}
              onChange={(e) => set("seed", Math.max(0, Number(e.target.value)))}
              className={`${INPUT_CLS} flex-1`}
            />
            <button
              type="button"
              onClick={randomSeed}
              title="Random seed"
              className="rounded border border-slate-300 px-2 py-1 text-slate-500 hover:bg-slate-50 transition"
            >
              <Shuffle className="h-4 w-4" />
            </button>
          </div>
          <span className="text-[10px] text-slate-400">Same seed → identical workload</span>
        </label>

        {/* Priority distribution */}
        <div className="flex flex-col gap-1 sm:col-span-2">
          <span className="text-xs font-medium text-slate-600">
            Priority distribution
            {!priorityValid && (
              <span className="ml-2 text-red-500">
                (sum = {prioritySum}, must equal 100)
              </span>
            )}
          </span>
          <div className="grid grid-cols-4 gap-2">
            {(["critical", "high", "medium", "low"] as (keyof typeof config.priority_distribution)[]).map((p) => (
              <label key={p} className="flex flex-col items-center gap-1">
                <span className="text-[10px] capitalize text-slate-500">{p}</span>
                <input
                  id={`cfg-pri-${p}`}
                  type="number"
                  min={0}
                  max={100}
                  value={config.priority_distribution[p]}
                  onChange={(e) => setPriority(p, e.target.value)}
                  className={`${INPUT_CLS} w-full text-center`}
                  style={{ borderColor: PRIORITY_COLOR[p as TaskPriority] }}
                />
                <span className="text-[10px] text-slate-400">%</span>
              </label>
            ))}
          </div>
          {/* Visual bar */}
          <div className="mt-1 flex h-2 overflow-hidden rounded-full">
            {(["critical", "high", "medium", "low"] as TaskPriority[]).map((p) => (
              <div
                key={p}
                style={{
                  width: `${config.priority_distribution[p]}%`,
                  background: PRIORITY_COLOR[p],
                }}
                title={`${p}: ${config.priority_distribution[p]}%`}
              />
            ))}
          </div>
        </div>
      </div>

      {/* Generate button */}
      <div className="mt-4 flex items-center justify-between">
        <p className="text-xs text-slate-400">
          Graph ID will be: <code className="font-mono text-indigo-600">
            g-{config.seed}-n{config.num_tasks}-d{config.dependency_percentage}-{config.complexity}
          </code>
        </p>
        <button
          id="generate-workload-btn"
          onClick={onGenerate}
          disabled={!priorityValid || generating}
          className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50 transition"
        >
          <RefreshCw className={`h-4 w-4 ${generating ? "animate-spin" : ""}`} />
          Generate workload
        </button>
      </div>
    </div>
  );
}

// ─── Statistics section ───────────────────────────────────────────────────────

function WorkloadStats({ graph }: { graph: TaskGraph }) {
  const stats = useMemo(() => computeStats(graph), [graph]);

  const typeChartData = Object.entries(stats.by_type).map(([type, count]) => ({ type, count }));
  const priorityChartData = (["critical", "high", "medium", "low"] as TaskPriority[]).map((p) => ({
    name: p,
    value: stats.by_priority[p],
  }));

  const fmtMs = (ms: number) =>
    ms >= 60000
      ? `${(ms / 60000).toFixed(1)} min`
      : ms >= 1000
      ? `${(ms / 1000).toFixed(1)} s`
      : `${Math.round(ms)} ms`;

  return (
    <>
      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard label="Total tasks" value={stats.total} icon={ListChecks} accent="#6366f1" />
        <MetricCard
          label="With dependencies"
          value={stats.with_deps}
          hint={`${stats.no_deps} root tasks`}
          icon={Layers}
          accent="#f59e0b"
        />
        <MetricCard
          label="Critical path depth"
          value={stats.max_depth}
          unit="layers"
          hint={`avg ${stats.avg_deps.toFixed(1)} deps/task`}
          icon={Zap}
          accent="#ef4444"
        />
        <MetricCard
          label="Total estimated work"
          value={fmtMs(stats.total_estimated_ms)}
          hint={`avg ${fmtMs(stats.avg_estimated_ms)} / task`}
          icon={Cpu}
          accent="#22c55e"
        />
      </div>

      {/* Charts row */}
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Tasks by type" subtitle="Count per task type" height={220}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={typeChartData}
              margin={{ top: 4, right: 8, left: 0, bottom: 40 }}
            >
              <CartesianGrid stroke={GRID_STROKE} vertical={false} />
              <XAxis
                dataKey="type"
                tick={{ ...AXIS_TICK, angle: -35, textAnchor: "end" }}
                tickLine={false}
                interval={0}
              />
              <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={28} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f1f5f9" }} />
              <Bar dataKey="count" name="Tasks" radius={[3, 3, 0, 0]}>
                {typeChartData.map((entry) => (
                  <Cell key={entry.type} fill={TYPE_COLOR[entry.type as TaskType] ?? "#6366f1"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Priority distribution" subtitle="Share of tasks per priority tier" height={220}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={priorityChartData}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                outerRadius={80}
                innerRadius={44}
                paddingAngle={2}
                label={({ name, percent }) =>
                  percent > 0.04 ? `${name} ${(percent * 100).toFixed(0)}%` : ""
                }
                labelLine={false}
              >
                {priorityChartData.map((entry) => (
                  <Cell
                    key={entry.name}
                    fill={PRIORITY_COLOR[entry.name as TaskPriority]}
                  />
                ))}
              </Pie>
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                formatter={(v: number) => [v, "tasks"]}
              />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>
    </>
  );
}

// ─── Task table ───────────────────────────────────────────────────────────────

type SortKey = "id" | "task_type" | "priority" | "complexity" | "estimated_workload_ms" | "status" | "dependency_ids";

function TaskTable({ graph }: { graph: TaskGraph }) {
  const { tasks } = graph;

  const [sortKey, setSortKey] = useState<SortKey>("id");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [filterType, setFilterType] = useState<string>("all");
  const [filterPriority, setFilterPriority] = useState<string>("all");
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [search, setSearch] = useState("");

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 1 ? -1 : 1));
    else { setSortKey(key); setSortDir(1); }
  };

  const SortIcon = ({ col }: { col: SortKey }) =>
    sortKey === col
      ? sortDir === 1 ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />
      : <span className="h-3 w-3 opacity-0"><ChevronUp className="h-3 w-3" /></span>;

  const filtered = useMemo(() => {
    let result = [...tasks];
    if (filterType !== "all") result = result.filter((t) => t.task_type === filterType);
    if (filterPriority !== "all") result = result.filter((t) => t.priority === filterPriority);
    if (filterStatus !== "all") result = result.filter((t) => t.status === filterStatus);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(
        (t) =>
          t.id.includes(q) ||
          t.task_type.toLowerCase().includes(q) ||
          t.required_capability.includes(q) ||
          t.dependency_ids.some((d) => d.includes(q)),
      );
    }
    result.sort((a, b) => {
      const va = sortKey === "dependency_ids" ? a.dependency_ids.length : (a as never)[sortKey];
      const vb = sortKey === "dependency_ids" ? b.dependency_ids.length : (b as never)[sortKey];
      return (va < vb ? -1 : va > vb ? 1 : 0) * sortDir;
    });
    return result;
  }, [tasks, filterType, filterPriority, filterStatus, search, sortKey, sortDir]);

  const TH = ({ col, label }: { col: SortKey; label: string }) => (
    <th className="whitespace-nowrap px-3 py-2 font-medium">
      <button
        onClick={() => toggleSort(col)}
        className="inline-flex items-center gap-0.5 hover:text-slate-900"
      >
        {label}
        <SortIcon col={col} />
      </button>
    </th>
  );

  return (
    <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2">
        <input
          placeholder="Search tasks…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-700 placeholder:text-slate-400 outline-none focus:border-indigo-400"
        />
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 focus:border-indigo-400 outline-none"
        >
          <option value="all">All types</option>
          {TASK_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select
          value={filterPriority}
          onChange={(e) => setFilterPriority(e.target.value)}
          className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 focus:border-indigo-400 outline-none"
        >
          <option value="all">All priorities</option>
          {(["critical", "high", "medium", "low"] as TaskPriority[]).map((p) => (
            <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
          ))}
        </select>
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 focus:border-indigo-400 outline-none"
        >
          <option value="all">All statuses</option>
          {(["pending", "ready", "blocked", "running", "completed", "failed"] as TaskStatus[]).map((s) => (
            <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
          ))}
        </select>
        <span className="ml-auto text-xs text-slate-400">{filtered.length} of {tasks.length} tasks</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-slate-100 bg-slate-50 text-slate-500">
            <tr>
              <TH col="id" label="Task ID" />
              <TH col="task_type" label="Type" />
              <TH col="complexity" label="Complexity" />
              <TH col="priority" label="Priority" />
              <th className="whitespace-nowrap px-3 py-2 font-medium">Capability</th>
              <TH col="estimated_workload_ms" label="Est. workload" />
              <TH col="dependency_ids" label="Deps" />
              <TH col="status" label="Status" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {filtered.length === 0 && (
              <tr>
                <td colSpan={8} className="py-8 text-center text-slate-400">
                  No tasks match the current filters.
                </td>
              </tr>
            )}
            {filtered.map((task) => {
              const Icon = TYPE_ICON[task.task_type];
              const estMs = task.estimated_workload_ms;
              const fmtEst = estMs >= 1000 ? `${(estMs / 1000).toFixed(1)} s` : `${estMs} ms`;
              return (
                <tr key={task.id} className="hover:bg-slate-50">
                  <td className="whitespace-nowrap px-3 py-1.5 font-mono text-slate-700">{task.id}</td>
                  <td className="whitespace-nowrap px-3 py-1.5">
                    <span className="flex items-center gap-1.5">
                      <Icon className="h-3 w-3 shrink-0" style={{ color: TYPE_COLOR[task.task_type] }} />
                      <span className="text-slate-700">{task.task_type}</span>
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5">
                    <ComplexityDots complexity={task.complexity} />
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5">
                    <PriorityBadge priority={task.priority} />
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5">
                    <span className="rounded-full bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">
                      {task.required_capability}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5 tabular-nums text-slate-600">{fmtEst}</td>
                  <td className="whitespace-nowrap px-3 py-1.5">
                    {task.dependency_ids.length === 0 ? (
                      <span className="text-slate-400">—</span>
                    ) : (
                      <span className="font-mono text-[10px] text-slate-500" title={task.dependency_ids.join(", ")}>
                        {task.dependency_ids.slice(0, 2).join(", ")}
                        {task.dependency_ids.length > 2 && ` +${task.dependency_ids.length - 2}`}
                      </span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5">
                    <StatusPill status={task.status} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Page ────────────────────────────────────────────────────────────────────

type ActiveSection = "stats" | "dag" | "table";

export default function WorkloadPage() {
  const [config, setConfig] = useState<WorkloadGeneratorConfig>(DEFAULT_CONFIG);
  // Generate the initial workload immediately so the page isn't blank
  const [graph, setGraph] = useState<TaskGraph>(() => generateWorkload(DEFAULT_CONFIG));
  const [generating, setGenerating] = useState(false);
  const [activeSection, setActiveSection] = useState<ActiveSection>("stats");

  // Track whether the displayed graph matches the current config
  const lastGeneratedConfig = useRef<WorkloadGeneratorConfig>(DEFAULT_CONFIG);
  const isDirty =
    JSON.stringify(config) !== JSON.stringify(lastGeneratedConfig.current);

  const handleGenerate = () => {
    setGenerating(true);
    // Micro-delay so the spinner is visible
    setTimeout(() => {
      const newGraph = generateWorkload(config);
      setGraph(newGraph);
      lastGeneratedConfig.current = config;
      setGenerating(false);
    }, 80);
  };

  const SECTIONS = [
    { id: "stats" as ActiveSection, label: "Statistics" },
    { id: "dag"   as ActiveSection, label: "Dependency graph" },
    { id: "table" as ActiveSection, label: "Task table" },
  ];

  return (
    <>
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <SectionHeader
        title="Workload Generator"
        description="Generate synthetic computational workloads. The same workload (same seed) is passed identically to both managers during comparative experiments."
        icon={ListChecks}
      />

      {/* ── Config panel ───────────────────────────────────────────────────── */}
      <ConfigPanel
        config={config}
        onChange={setConfig}
        onGenerate={handleGenerate}
        generating={generating}
      />

      {/* Dirty state banner */}
      {isDirty && (
        <div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          Configuration has changed. Click <strong>Generate workload</strong> to apply.
        </div>
      )}

      {/* ── Graph ID badge ──────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs shadow-sm">
        <span className="text-slate-500">Active workload:</span>
        <code className="font-mono font-semibold text-indigo-700">{graph.graph_id}</code>
        <span className="text-slate-400">·</span>
        <span className="text-slate-500">{graph.tasks.length} tasks</span>
        <span className="text-slate-400">·</span>
        <span className="text-slate-500">seed {graph.seed}</span>
        <span className="text-slate-400">·</span>
        <span className="text-slate-500">generated {new Date(graph.generated_at).toLocaleTimeString()}</span>
        <span className="ml-auto rounded-full bg-emerald-100 px-2 py-0.5 text-emerald-700">
          Shared by both managers
        </span>
      </div>

      {/* ── Section tabs ────────────────────────────────────────────────────── */}
      <div className="flex gap-1 border-b border-slate-200">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            id={`tab-${s.id}`}
            onClick={() => setActiveSection(s.id)}
            className={`-mb-px whitespace-nowrap border-b-2 px-4 py-1.5 text-sm transition ${
              activeSection === s.id
                ? "border-indigo-600 font-medium text-indigo-700"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* ── Statistics ─────────────────────────────────────────────────────── */}
      {activeSection === "stats" && <WorkloadStats graph={graph} />}

      {/* ── DAG visualization ──────────────────────────────────────────────── */}
      {activeSection === "dag" && (
        <div className="space-y-3">
          <div className="flex items-start gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
            <Layers className="mt-0.5 h-3.5 w-3.5 shrink-0 text-indigo-400" />
            <span>
              Tasks are arranged in topological layers (left to right).
              Each layer contains tasks whose all prerequisites are in earlier layers.
              <strong className="text-slate-700"> Click a node</strong> to inspect it.
              <strong className="text-slate-700"> Hover</strong> to highlight connections.
              For workloads &gt; 120 tasks, only the first 120 nodes are rendered.
            </span>
          </div>
          <DagViz graph={graph} maxNodes={120} />
        </div>
      )}

      {/* ── Task table ─────────────────────────────────────────────────────── */}
      {activeSection === "table" && <TaskTable graph={graph} />}
    </>
  );
}
