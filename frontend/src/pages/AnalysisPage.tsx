/**
 * AnalysisPage — Module 10: Experiment Analysis (Parameter Sweep)
 *
 * Allows users to define a parameter sweep across:
 *   task count, complexity, dependency %, failure probability, max agents
 *
 * For every configuration × repetition, both managers run against the
 * SAME workload so the strategy is the only independent variable.
 *
 * Charts rendered:
 *   A. Task count vs avg latency
 *   B. Task count vs throughput
 *   C. Task count vs success rate
 *   D. Task count vs coordination overhead
 *   E. Task count vs active agents
 *   F. Task count vs agent utilization
 *   G. Failure probability vs recovery rate
 *
 * Statistical note shown on all charts:
 *   Effect sizes (Cohen's d) are computed; p-values only appear when
 *   scipy was available on the server AND n_reps >= 3.
 *   The UI never claims statistical significance without a test.
 */
import {
  Activity,
  AlertTriangle,
  BarChart2,
  Download,
  FlaskConical,
  Info,
  LineChart,
  RotateCcw,
  Shield,
  Zap,
} from "lucide-react";
import { useState } from "react";
import {
  CartesianGrid,
  ErrorBar,
  Legend,
  Line,
  LineChart as ReLineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import ChartCard from "../components/ChartCard";
import DataTable, { type Column } from "../components/DataTable";
import MetricCard from "../components/MetricCard";
import SectionHeader from "../components/SectionHeader";
import Tabs from "../components/Tabs";
import {
  AXIS_TICK,
  GRID_STROKE,
  LEGEND_STYLE,
  TOOLTIP_STYLE,
} from "../components/chartStyle";
import { MANAGER_COLORS } from "../data/managers";
import { api } from "../services/api";
import { fmtNum, fmtPct } from "../services/format";
import type {
  BatchRequest,
  BatchResponse,
  ChartPoint,
  ConfigStats,
  EffectSize,
  RunRecord,
} from "../types/analysis";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CONV = MANAGER_COLORS.conventional;
const POSD = MANAGER_COLORS.posdcorb;

const btn =
  "inline-flex items-center gap-1.5 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-40 hover:bg-slate-50";

// ---------------------------------------------------------------------------
// Default sweep config
// ---------------------------------------------------------------------------

interface SweepForm {
  task_counts: string;       // comma-separated ints
  complexities: string;      // comma-separated
  dep_percentages: string;
  failure_probs: string;
  max_agents_list: string;
  repetitions: number;
  base_seed: number;
  max_decisions: number;
  max_wall_ms: number;
}

const DEFAULT_FORM: SweepForm = {
  task_counts: "6, 10, 14",
  complexities: "medium",
  dep_percentages: "25",
  failure_probs: "0.0",
  max_agents_list: "6",
  repetitions: 1,
  base_seed: 42,
  max_decisions: 3000,
  max_wall_ms: 120000,
};

// ---------------------------------------------------------------------------
// Form parsing
// ---------------------------------------------------------------------------

function parseNums(s: string): number[] {
  return s
    .split(",")
    .map((t) => parseFloat(t.trim()))
    .filter((n) => !isNaN(n));
}

function parseInts(s: string): number[] {
  return s
    .split(",")
    .map((t) => parseInt(t.trim(), 10))
    .filter((n) => !isNaN(n));
}

function parseStrs(s: string): string[] {
  return s
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

function formToRequest(f: SweepForm): BatchRequest {
  return {
    task_counts: parseInts(f.task_counts),
    complexities: parseStrs(f.complexities),
    dep_percentages: parseNums(f.dep_percentages),
    failure_probs: parseNums(f.failure_probs),
    max_agents_list: parseInts(f.max_agents_list),
    repetitions: f.repetitions,
    base_seed: f.base_seed,
    max_decisions: f.max_decisions,
    max_wall_ms: f.max_wall_ms,
  };
}

function estimatedRuns(f: SweepForm): number {
  return (
    parseInts(f.task_counts).length *
    parseStrs(f.complexities).length *
    parseNums(f.dep_percentages).length *
    parseNums(f.failure_probs).length *
    parseInts(f.max_agents_list).length *
    f.repetitions *
    2
  );
}

// ---------------------------------------------------------------------------
// CSV export
// ---------------------------------------------------------------------------

function exportCSV(runs: RunRecord[], batchId: string): void {
  const headers = [
    "run_id", "config_id", "rep", "seed", "task_count", "complexity",
    "dep_pct", "failure_prob", "max_agents", "manager",
    "completed_tasks", "failed_tasks", "success_rate", "total_wall_ms",
    "throughput_tasks_per_sec", "avg_task_latency_ms", "total_decisions",
    "coordination_overhead_ratio", "total_active_agents", "activation_count",
    "avg_agent_utilization", "recovery_count", "recovery_success_rate",
    "termination_reason", "error",
  ];
  const rows = runs.map((r) =>
    headers.map((h) => {
      const v = (r as unknown as Record<string, unknown>)[h];
      return String(v ?? "").replace(/,/g, ";");
    }).join(",")
  );
  const csv = [headers.join(","), ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `analysis-${batchId.slice(0, 8)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Chart helpers
// ---------------------------------------------------------------------------

/** Recharts data point — one row per x_value with conv & posd values. */
function toRechartsData(pts: ChartPoint[]) {
  return pts.map((p) => ({
    x: p.x_label,
    conv: p.conventional_mean,
    convErrUp: p.conventional_std,
    convErrDown: p.conventional_std,
    posd: p.posdcorb_mean,
    posdErrUp: p.posdcorb_std,
    posdErrDown: p.posdcorb_std,
    nReps: p.n_reps,
  }));
}

interface CompareLineChartProps {
  title: string;
  subtitle?: string;
  data: ChartPoint[];
  yLabel: string;
  yFormatter?: (v: number) => string;
  height?: number;
}

function CompareLineChart({
  title,
  subtitle,
  data,
  yLabel,
  yFormatter = (v) => fmtNum(v, 3),
  height = 240,
}: CompareLineChartProps) {
  const chartData = toRechartsData(data);
  if (chartData.length === 0) {
    return (
      <ChartCard title={title} subtitle={subtitle} height={height}>
        <div className="flex h-full items-center justify-center text-xs text-slate-500">
          No data — vary the parameter on the x-axis.
        </div>
      </ChartCard>
    );
  }

  return (
    <ChartCard
      title={title}
      subtitle={
        subtitle ??
        `Error bars = ±1 SD across repetitions (if reps > 1). n = ${chartData[0]?.nReps ?? 1} per point.`
      }
      height={height}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ReLineChart data={chartData} margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
          <XAxis dataKey="x" tick={AXIS_TICK} label={{ value: "Task count", position: "insideBottom", offset: -2, style: AXIS_TICK }} />
          <YAxis tick={AXIS_TICK} tickFormatter={yFormatter} label={{ value: yLabel, angle: -90, position: "insideLeft", style: AXIS_TICK }} />
          <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => yFormatter(v)} />
          <Legend wrapperStyle={LEGEND_STYLE} />
          <Line
            type="monotone"
            dataKey="conv"
            name="Conventional"
            stroke={CONV}
            strokeWidth={2}
            dot={{ r: 4, fill: CONV }}
          >
            <ErrorBar dataKey="convErrUp" direction="y" stroke={CONV} strokeOpacity={0.4} />
          </Line>
          <Line
            type="monotone"
            dataKey="posd"
            name="POSDCORB"
            stroke={POSD}
            strokeWidth={2}
            dot={{ r: 4, fill: POSD }}
          >
            <ErrorBar dataKey="posdErrUp" direction="y" stroke={POSD} strokeOpacity={0.4} />
          </Line>
        </ReLineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** Chart G — x axis is failure_prob, not task_count. */
function RecoveryChart({ data }: { data: ChartPoint[] }) {
  const chartData = data.map((p) => ({
    x: p.x_label,
    conv: parseFloat((p.conventional_mean * 100).toFixed(2)),
    convErr: p.conventional_std * 100,
    posd: parseFloat((p.posdcorb_mean * 100).toFixed(2)),
    posdErr: p.posdcorb_std * 100,
    nReps: p.n_reps,
  }));

  if (chartData.length === 0) {
    return (
      <ChartCard title="G. Failure Probability vs Recovery Rate" height={240}>
        <div className="flex h-full items-center justify-center text-xs text-slate-500">
          Add multiple failure probabilities to populate this chart.
        </div>
      </ChartCard>
    );
  }

  return (
    <ChartCard
      title="G. Failure Probability vs Recovery Rate"
      subtitle="Recovery rate = recoveries / max(1, total failures). Error bars = ±1 SD."
      height={240}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ReLineChart data={chartData} margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
          <XAxis dataKey="x" tick={AXIS_TICK} label={{ value: "Failure probability", position: "insideBottom", offset: -2, style: AXIS_TICK }} />
          <YAxis tick={AXIS_TICK} tickFormatter={(v) => `${v}%`} domain={[0, 100]} />
          <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => fmtPct(v)} />
          <Legend wrapperStyle={LEGEND_STYLE} />
          <ReferenceLine y={100} stroke="#94a3b8" strokeDasharray="4 2" label={{ value: "Perfect", position: "right", style: AXIS_TICK }} />
          <Line type="monotone" dataKey="conv" name="Conventional" stroke={CONV} strokeWidth={2} dot={{ r: 4, fill: CONV }}>
            <ErrorBar dataKey="convErr" direction="y" stroke={CONV} strokeOpacity={0.4} />
          </Line>
          <Line type="monotone" dataKey="posd" name="POSDCORB" stroke={POSD} strokeWidth={2} dot={{ r: 4, fill: POSD }}>
            <ErrorBar dataKey="posdErr" direction="y" stroke={POSD} strokeOpacity={0.4} />
          </Line>
        </ReLineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// Effect size table
// ---------------------------------------------------------------------------

const MAGNITUDE_COLOR: Record<string, string> = {
  negligible: "text-slate-500",
  small: "text-slate-700",
  medium: "text-amber-700",
  large: "text-blue-700",
  not_estimable: "text-slate-400",
};

const EFFECT_COLUMNS: Column<EffectSize>[] = [
  { key: "metric", header: "Metric", className: "font-mono text-xs", sortValue: (r) => r.metric },
  { key: "task_count", header: "Tasks", align: "right", sortValue: (r) => r.task_count },
  { key: "failure_prob", header: "Fail prob", align: "right", sortValue: (r) => r.failure_prob },
  {
    key: "mean_conventional",
    header: "Conv mean",
    align: "right",
    sortValue: (r) => r.mean_conventional,
    render: (r) => <span className="tabular-nums text-xs">{r.mean_conventional.toFixed(4)}</span>,
  },
  {
    key: "mean_posdcorb",
    header: "POSD mean",
    align: "right",
    sortValue: (r) => r.mean_posdcorb,
    render: (r) => <span className="tabular-nums text-xs">{r.mean_posdcorb.toFixed(4)}</span>,
  },
  {
    key: "cohens_d",
    header: "Cohen's d",
    align: "right",
    sortValue: (r) => Math.abs(r.cohens_d),
    render: (r) =>
      r.magnitude === "not_estimable" ? (
        <span className="text-xs text-slate-400">—</span>
      ) : (
        <span className="tabular-nums text-xs">{r.cohens_d.toFixed(3)}</span>
      ),
  },
  {
    key: "magnitude",
    header: "Magnitude",
    render: (r) => (
      <span className={`text-xs font-medium ${MAGNITUDE_COLOR[r.magnitude] ?? ""}`}>
        {r.magnitude}
      </span>
    ),
    sortValue: (r) => r.magnitude,
  },
  {
    key: "test_performed",
    header: "Test",
    render: (r) =>
      r.test_performed ? (
        <span className="text-xs text-slate-700">{r.test_performed} p={r.p_value?.toFixed(4)}</span>
      ) : (
        <span className="text-xs text-slate-400">none</span>
      ),
  },
  {
    key: "interpretation",
    header: "Interpretation",
    className: "max-w-sm truncate text-xs text-slate-500",
  },
];

// ---------------------------------------------------------------------------
// Stats summary table
// ---------------------------------------------------------------------------

function StatCell({ s }: { s: { mean: number; std: number; min: number; max: number; n: number } }) {
  return (
    <span className="tabular-nums text-xs">
      {s.mean.toFixed(3)}
      {s.n >= 2 && <span className="text-slate-400"> ±{s.std.toFixed(3)}</span>}
    </span>
  );
}

const STATS_COLUMNS: Column<ConfigStats>[] = [
  { key: "manager", header: "Manager", render: (r) => (
    <span className="rounded px-1.5 py-0.5 text-xs font-medium text-white"
      style={{ backgroundColor: MANAGER_COLORS[r.manager as "conventional" | "posdcorb"] ?? "#94a3b8" }}>
      {r.manager === "conventional" ? "Conv" : "POSD"}
    </span>
  )},
  { key: "task_count", header: "Tasks", align: "right", sortValue: (r) => r.task_count },
  { key: "complexity", header: "Complexity", sortValue: (r) => r.complexity },
  { key: "dep_pct", header: "Dep%", align: "right", sortValue: (r) => r.dep_pct },
  { key: "failure_prob", header: "FailP", align: "right", sortValue: (r) => r.failure_prob },
  { key: "n_reps", header: "Reps", align: "right", sortValue: (r) => r.n_reps },
  { key: "success_rate", header: "Success (mean±std)", render: (r) => <StatCell s={r.success_rate} /> },
  { key: "throughput", header: "Throughput", render: (r) => <StatCell s={r.throughput} /> },
  { key: "avg_latency_ms", header: "Avg latency ms", render: (r) => <StatCell s={r.avg_latency_ms} /> },
  { key: "coordination_overhead", header: "Overhead ratio", render: (r) => <StatCell s={r.coordination_overhead} /> },
  { key: "active_agents", header: "Active agents", render: (r) => <StatCell s={r.active_agents} /> },
  { key: "recovery_rate", header: "Recovery rate", render: (r) => <StatCell s={r.recovery_rate} /> },
];

// ---------------------------------------------------------------------------
// Raw runs table
// ---------------------------------------------------------------------------

const RAW_COLUMNS: Column<RunRecord>[] = [
  { key: "manager", header: "Mgr", render: (r) => (
    <span className="rounded px-1 py-0.5 text-xs font-medium text-white"
      style={{ backgroundColor: MANAGER_COLORS[r.manager as "conventional" | "posdcorb"] ?? "#94a3b8" }}>
      {r.manager === "conventional" ? "C" : "P"}
    </span>
  )},
  { key: "task_count", header: "Tasks", align: "right", sortValue: (r) => r.task_count },
  { key: "complexity", header: "Cx", sortValue: (r) => r.complexity },
  { key: "dep_pct", header: "Dep%", align: "right", sortValue: (r) => r.dep_pct },
  { key: "failure_prob", header: "FP", align: "right", sortValue: (r) => r.failure_prob },
  { key: "rep", header: "Rep", align: "right", sortValue: (r) => r.rep },
  { key: "seed", header: "Seed", align: "right", sortValue: (r) => r.seed },
  { key: "success_rate", header: "Success", align: "right", sortValue: (r) => r.success_rate,
    render: (r) => <span className="tabular-nums text-xs">{(r.success_rate * 100).toFixed(1)}%</span> },
  { key: "total_wall_ms", header: "Wall ms", align: "right", sortValue: (r) => r.total_wall_ms,
    render: (r) => <span className="tabular-nums text-xs">{Math.round(r.total_wall_ms).toLocaleString()}</span> },
  { key: "throughput_tasks_per_sec", header: "Tput/s", align: "right", sortValue: (r) => r.throughput_tasks_per_sec,
    render: (r) => <span className="tabular-nums text-xs">{r.throughput_tasks_per_sec.toFixed(4)}</span> },
  { key: "avg_task_latency_ms", header: "Avg lat ms", align: "right", sortValue: (r) => r.avg_task_latency_ms,
    render: (r) => <span className="tabular-nums text-xs">{r.avg_task_latency_ms.toFixed(1)}</span> },
  { key: "coordination_overhead_ratio", header: "Ovhd", align: "right", sortValue: (r) => r.coordination_overhead_ratio,
    render: (r) => <span className="tabular-nums text-xs">{(r.coordination_overhead_ratio * 100).toFixed(2)}%</span> },
  { key: "total_active_agents", header: "Agents", align: "right", sortValue: (r) => r.total_active_agents },
  { key: "recovery_success_rate", header: "Recov%", align: "right", sortValue: (r) => r.recovery_success_rate,
    render: (r) => <span className="tabular-nums text-xs">{(r.recovery_success_rate * 100).toFixed(1)}%</span> },
  { key: "termination_reason", header: "Reason", className: "text-xs text-slate-500", sortValue: (r) => r.termination_reason },
];

// ---------------------------------------------------------------------------
// Sweep config form
// ---------------------------------------------------------------------------

function SweepForm({
  form,
  onChange,
  disabled,
  estimated,
}: {
  form: SweepForm;
  onChange: (f: SweepForm) => void;
  disabled: boolean;
  estimated: number;
}) {
  const set = (key: keyof SweepForm, val: string | number) =>
    onChange({ ...form, [key]: val });

  const textField = (label: string, key: keyof SweepForm, hint: string) => (
    <div className="space-y-0.5">
      <label className="block text-xs font-medium text-slate-700">{label}</label>
      <input
        type="text"
        disabled={disabled}
        className="w-full rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-60"
        value={form[key] as string}
        onChange={(e) => set(key, e.target.value)}
      />
      <p className="text-[10px] text-slate-400">{hint}</p>
    </div>
  );

  const numField = (label: string, key: keyof SweepForm, min: number, max: number) => (
    <div className="space-y-0.5">
      <label className="block text-xs font-medium text-slate-700">{label}</label>
      <input
        type="number"
        min={min}
        max={max}
        disabled={disabled}
        className="w-full rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-60"
        value={form[key] as number}
        onChange={(e) => set(key, Number(e.target.value))}
      />
    </div>
  );

  const overLimit = estimated > 200;

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-slate-700">Parameter Sweep Configuration</p>
        <span className={`text-xs font-medium ${overLimit ? "text-red-600" : "text-slate-500"}`}>
          ≈{estimated} total runs {overLimit && "(exceeds 200 limit — reduce axes or reps)"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {textField("Task counts", "task_counts", "e.g. 6, 10, 14")}
        {textField("Complexities", "complexities", "low | medium | high")}
        {textField("Dependency %", "dep_percentages", "e.g. 25 or 10, 50")}
        {textField("Failure prob", "failure_probs", "e.g. 0.0, 0.1, 0.2")}
        {textField("Max agents", "max_agents_list", "e.g. 6 or 4, 8")}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {numField("Repetitions", "repetitions", 1, 10)}
        {numField("Base seed", "base_seed", 0, 9999)}
        {numField("Max decisions", "max_decisions", 100, 50000)}
        {numField("Max wall ms", "max_wall_ms", 1000, 600000)}
      </div>

      <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
        <span className="font-medium">How it works:</span> Each combination of the above axes creates one
        configuration. Both managers run against the <em>same</em> workload (same graph, same seed)
        for that configuration. Repetitions use seeds {" "}
        <code>base_seed + 0, +1, …</code>.
        Statistics (mean, median, SD, min, max) are computed across repetitions.
        Effect sizes use Cohen's d.{" "}
        <span className="text-amber-700 font-medium">
          Statistical significance is NOT claimed unless a Welch t-test was performed (requires scipy + n ≥ 3).
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Summary cards
// ---------------------------------------------------------------------------

function SummaryCards({ result }: { result: BatchResponse }) {
  const conv = result.raw_runs.filter((r) => r.manager === "conventional");
  const posd = result.raw_runs.filter((r) => r.manager === "posdcorb");

  const mean = (arr: number[]) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;

  const convSuccess = mean(conv.map((r) => r.success_rate));
  const posdSuccess = mean(posd.map((r) => r.success_rate));
  const largeEffects = result.effect_sizes.filter((e) => e.magnitude === "large" || e.magnitude === "medium");

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
      <MetricCard label="Total configs" value={result.total_configs} icon={FlaskConical} />
      <MetricCard label="Total runs" value={result.total_runs} icon={Zap} />
      <MetricCard label="Elapsed" value={`${result.elapsed_s.toFixed(1)}s`} icon={Activity} />
      <MetricCard label="Conv avg success" value={fmtPct(convSuccess * 100)} icon={BarChart2} accent={CONV} />
      <MetricCard label="POSD avg success" value={fmtPct(posdSuccess * 100)} icon={BarChart2} accent={POSD} />
      <MetricCard
        label="Med/large effects"
        value={largeEffects.length}
        hint="Cohen's d ≥ 0.5 across all metrics"
        icon={Shield}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// All charts section
// ---------------------------------------------------------------------------

function ChartsSection({ result }: { result: BatchResponse }) {
  const cd = result.chart_data;

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Comparison Charts"
        description="Lines show mean across repetitions; error bars = ±1 SD. x-axis varies task count (charts A–F) or failure probability (chart G). Each x value pools every configuration that shares it (all complexities, dependency levels and failure probabilities), so a point is not a single configuration."
        icon={LineChart}
      />

      <div className="flex items-start gap-2 rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          Effect sizes (Cohen's d) quantify magnitude of difference between managers.
          Significance tests (Welch t-test) only appear in the Effect Sizes table when scipy is installed and n ≥ 3.
          Lines that overlap do not necessarily indicate equivalent performance without a formal test.
        </span>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <CompareLineChart
          title="A. Task Count vs Avg Task Latency"
          data={cd.task_count_vs_latency}
          yLabel="ms"
          yFormatter={(v) => `${v.toFixed(1)} ms`}
        />
        <CompareLineChart
          title="B. Task Count vs Throughput"
          data={cd.task_count_vs_throughput}
          yLabel="tasks/s"
          yFormatter={(v) => v.toFixed(4)}
        />
        <CompareLineChart
          title="C. Task Count vs Success Rate"
          data={cd.task_count_vs_success_rate}
          yLabel="%"
          yFormatter={(v) => `${(v * 100).toFixed(1)}%`}
        />
        <CompareLineChart
          title="D. Task Count vs Coordination Overhead"
          data={cd.task_count_vs_coordination_overhead}
          yLabel="overhead ratio"
          yFormatter={(v) => `${(v * 100).toFixed(2)}%`}
        />
        <CompareLineChart
          title="E. Task Count vs Active Agents"
          data={cd.task_count_vs_active_agents}
          yLabel="agents"
          yFormatter={(v) => v.toFixed(1)}
        />
        <RecoveryChart data={cd.failure_prob_vs_recovery_rate} />
      </div>

      {cd.task_count_vs_cpu_note && (
        <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
          <Info className="h-3.5 w-3.5 shrink-0" />
          <span>{cd.task_count_vs_cpu_note}</span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Effect sizes section
// ---------------------------------------------------------------------------

function EffectSizeSection({ effects }: { effects: EffectSize[] }) {
  const large = effects.filter((e) => e.magnitude === "large");
  const medium = effects.filter((e) => e.magnitude === "medium");

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Effect Sizes (Cohen's d)"
        description="Quantifies magnitude of difference between managers per metric per configuration. Does NOT imply statistical significance."
        icon={BarChart2}
      />

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <MetricCard label="Large effects (|d|≥0.8)" value={large.length} />
        <MetricCard label="Medium effects (|d|≥0.5)" value={medium.length} />
        <MetricCard label="Total effect rows" value={effects.length} />
        <MetricCard
          label="Tests performed"
          value={effects.filter((e) => e.test_performed).length}
          hint="Welch t-test (requires scipy + n≥3)"
        />
      </div>

      <div className="flex items-start gap-2 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-800">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          <strong>Interpretation guide:</strong>{" "}
          <span className="text-slate-500">negligible |d|&lt;0.2</span> ·{" "}
          <span className="text-slate-700">small 0.2–0.5</span> ·{" "}
          <span className="text-amber-700">medium 0.5–0.8</span> ·{" "}
          <span className="text-blue-700">large |d|≥0.8</span> ·{" "}
          <span className="text-slate-400">not estimable = fewer than 2 reps</span>.
          Positive d = Conventional higher; negative d = POSDCORB higher.
          Direction of "better" depends on the metric.
        </span>
      </div>

      <DataTable
        columns={EFFECT_COLUMNS}
        rows={effects}
        rowKey={(r) => `${r.config_id}-${r.metric}`}
        emptyMessage="No effect sizes computed."
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Statistics table section
// ---------------------------------------------------------------------------

function StatsSection({ stats }: { stats: ConfigStats[] }) {
  return (
    <div className="space-y-3">
      <SectionHeader
        title="Aggregated Statistics"
        description="Mean ± SD per configuration per manager across all repetitions."
        icon={Activity}
      />
      <DataTable
        columns={STATS_COLUMNS}
        rows={stats}
        rowKey={(r) => `${r.config_id}-${r.manager}`}
        emptyMessage="No statistics computed."
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Raw runs section
// ---------------------------------------------------------------------------

function RawRunsSection({
  runs,
  batchId,
}: {
  runs: RunRecord[];
  batchId: string;
}) {
  const PAGE = 50;
  const [page, setPage] = useState(0);
  const total = runs.length;
  const totalPages = Math.ceil(total / PAGE);
  const slice = runs.slice(page * PAGE, (page + 1) * PAGE);

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Raw Run Records"
        description={`${total} individual manager runs. Sortable by any column.`}
        icon={Zap}
        actions={
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 text-xs text-slate-500">
              {page * PAGE + 1}–{Math.min((page + 1) * PAGE, total)} of {total}
              <button
                className="rounded border border-slate-300 px-2 py-0.5 disabled:opacity-40"
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
              >←</button>
              <button
                className="rounded border border-slate-300 px-2 py-0.5 disabled:opacity-40"
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => p + 1)}
              >→</button>
            </div>
            <button
              className={btn}
              onClick={() => exportCSV(runs, batchId)}
            >
              <Download className="h-4 w-4" /> Export CSV
            </button>
          </div>
        }
      />
      <DataTable
        columns={RAW_COLUMNS}
        rows={slice}
        rowKey={(r) => r.run_id}
        emptyMessage="No runs recorded."
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type PageStatus = "idle" | "running" | "done" | "error";
type TabId = "charts" | "effects" | "stats" | "raw";

const TABS: { id: TabId; label: string }[] = [
  { id: "charts", label: "Charts" },
  { id: "effects", label: "Effect Sizes" },
  { id: "stats", label: "Statistics" },
  { id: "raw", label: "Raw Runs" },
];

export default function AnalysisPage() {
  const [status, setStatus] = useState<PageStatus>("idle");
  const [form, setForm] = useState<SweepForm>(DEFAULT_FORM);
  const [result, setResult] = useState<BatchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("charts");

  const estimated = estimatedRuns(form);
  const isRunning = status === "running";
  const overLimit = estimated > 200;

  async function handleRun() {
    setStatus("running");
    setError(null);
    setResult(null);
    try {
      const req = formToRequest(form);
      const res = await api.runBatch(req);
      setResult(res);
      setStatus("done");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus("error");
    }
  }

  function handleReset() {
    setStatus("idle");
    setResult(null);
    setError(null);
    setActiveTab("charts");
  }

  return (
    <>
      <SectionHeader
        title="Experiment Analysis"
        description="Parameter sweep comparing Conventional and POSDCORB managers across multiple configurations."
        icon={LineChart}
        actions={
          <>
            <button
              className={btn}
              onClick={handleRun}
              disabled={isRunning || overLimit}
            >
              {isRunning ? (
                <><Activity className="h-4 w-4 animate-pulse" /> Running…</>
              ) : (
                <><FlaskConical className="h-4 w-4" /> Run Sweep</>
              )}
            </button>
            <button className={btn} onClick={handleReset} disabled={isRunning}>
              <RotateCcw className="h-4 w-4" /> Reset
            </button>
          </>
        }
      />

      <SweepForm
        form={form}
        onChange={setForm}
        disabled={isRunning}
        estimated={estimated}
      />

      {status === "idle" && (
        <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          Configure the parameter sweep above then click <strong>Run Sweep</strong>.
          Each combination runs both managers on the same workload.
        </div>
      )}

      {isRunning && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <span className="animate-pulse">●</span>&nbsp;
          Running {estimated} manager simulations… this may take {Math.ceil(estimated * 0.8)}–{Math.ceil(estimated * 2)}s.
        </div>
      )}

      {status === "error" && error && (
        <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div><strong>Error:</strong> {error}</div>
        </div>
      )}

      {status === "done" && result && (
        <>
          <SummaryCards result={result} />

          {result.errors.length > 0 && (
            <details className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <summary className="cursor-pointer font-medium">
                {result.errors.length} non-fatal run error(s) — click to expand
              </summary>
              <ul className="mt-2 space-y-0.5">
                {result.errors.map((e, i) => <li key={i}>• {e}</li>)}
              </ul>
            </details>
          )}

          <div className="space-y-4">
            <Tabs items={TABS} active={activeTab} onChange={setActiveTab} />
            {activeTab === "charts" && <ChartsSection result={result} />}
            {activeTab === "effects" && <EffectSizeSection effects={result.effect_sizes} />}
            {activeTab === "stats" && <StatsSection stats={result.stats} />}
            {activeTab === "raw" && (
              <RawRunsSection runs={result.raw_runs} batchId={result.batch_id} />
            )}
          </div>
        </>
      )}
    </>
  );
}
