/**
 * AblationPage — POSDCORB Ablation Study
 *
 * Runs the nine cumulative manager configurations against one identical
 * workload and presents:
 *   - comparison table (latency, throughput, success, CPU, overhead,
 *     agent utilisation, activation count)
 *   - grouped bar charts per metric and for directional improvements
 *   - improvement relative to the Conventional baseline
 *   - marginal contribution of each principle
 *
 * Fairness is enforced by the backend: every configuration receives the same
 * graph, agent pool, seed and resource envelope. The conditions banner shows
 * the fingerprint that makes this auditable.
 */
import {
  Activity,
  AlertTriangle,
  BarChart2,
  Crown,
  FlaskConical,
  Info,
  Layers,
  RotateCcw,
  Scale,
  Zap,
} from "lucide-react";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
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
import {
  AXIS_TICK,
  GRID_STROKE,
  LEGEND_STYLE,
  TOOLTIP_STYLE,
} from "../components/chartStyle";
import { api } from "../services/api";
import type {
  AblationConfiguration,
  AblationMetricKey,
  AblationMetricValue,
  AblationPrincipleContribution,
  AblationRawRun,
  AblationStudyRequest,
  AblationStudyResponse,
} from "../types/ablation";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const btn =
  "inline-flex items-center gap-1.5 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-40 hover:bg-slate-50";

const METRIC_ORDER: AblationMetricKey[] = [
  "latency_ms",
  "throughput_tasks_per_sec",
  "success_rate",
  "cpu_utilization",
  "coordination_overhead",
  "agent_utilization",
  "activation_count",
];

const METRIC_LABEL: Record<AblationMetricKey, string> = {
  latency_ms: "Avg latency",
  throughput_tasks_per_sec: "Throughput",
  success_rate: "Success rate",
  cpu_utilization: "CPU utilisation",
  coordination_overhead: "Coordination overhead",
  agent_utilization: "Agent utilisation",
  activation_count: "Agent activations",
};

const METRIC_UNIT: Record<AblationMetricKey, string> = {
  latency_ms: "ms",
  throughput_tasks_per_sec: "tasks/s",
  success_rate: "%",
  cpu_utilization: "%",
  coordination_overhead: "%",
  agent_utilization: "%",
  activation_count: "count",
};

const METRIC_FORMAT: Record<AblationMetricKey, (v: number) => string> = {
  latency_ms: (v) => v.toFixed(1),
  throughput_tasks_per_sec: (v) => v.toFixed(3),
  success_rate: (v) => `${v.toFixed(1)}%`,
  cpu_utilization: (v) => `${v.toFixed(1)}%`,
  coordination_overhead: (v) => `${v.toFixed(1)}%`,
  agent_utilization: (v) => `${v.toFixed(1)}%`,
  activation_count: (v) => v.toFixed(0),
};

const CONFIG_COLORS = [
  "#d97706", // baseline — amber
  "#0ea5e9",
  "#0284c7",
  "#2563eb",
  "#4f46e5",
  "#7c3aed",
  "#9333ea",
  "#c026d3",
  "#db2777",
];

const IMPROVEMENT_SERIES: {
  key: AblationMetricKey;
  label: string;
  color: string;
}[] = [
  { key: "latency_ms", label: "Latency", color: "#0ea5e9" },
  { key: "throughput_tasks_per_sec", label: "Throughput", color: "#2563eb" },
  { key: "success_rate", label: "Success", color: "#16a34a" },
  { key: "cpu_utilization", label: "CPU", color: "#ca8a04" },
  { key: "coordination_overhead", label: "Overhead", color: "#dc2626" },
  { key: "agent_utilization", label: "Agent util.", color: "#7c3aed" },
];

const MECHANISM_CODES: Record<string, string> = {
  planning: "P",
  organizing: "O",
  staffing: "S",
  directing: "D",
  coordinating: "CO",
  reporting: "R",
  budgeting: "B",
  adaptive: "A",
};

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const fmtImprovement = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return "n/a";
  const sign = v > 0.0001 ? "+" : "";
  return `${sign}${v.toFixed(1)}%`;
};

const improvementClass = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return "text-slate-400";
  if (v > 0.0001) return "text-emerald-700";
  if (v < -0.0001) return "text-red-600";
  return "text-slate-500";
};

const colorFor = (index: number): string =>
  CONFIG_COLORS[index % CONFIG_COLORS.length];

const activeMechanisms = (config: AblationConfiguration): string =>
  Object.entries(config.mechanism_activity)
    .filter(([, count]) => count > 0)
    .map(([key]) => MECHANISM_CODES[key] ?? key)
    .join(" · ") || "—";

function MetricCell({
  metric,
  value,
}: {
  metric: AblationMetricKey;
  value: AblationMetricValue;
}) {
  const fmt = METRIC_FORMAT[metric];
  return (
    <span className="tabular-nums text-xs">
      {fmt(value.mean)}
      {value.n >= 2 && (
        <span className="text-slate-400"> ±{fmt(value.std)}</span>
      )}
    </span>
  );
}

function ImprovementCell({ value }: { value: number | null | undefined }) {
  return (
    <span className={`tabular-nums text-xs ${improvementClass(value)}`}>
      {fmtImprovement(value)}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Form
// ---------------------------------------------------------------------------

interface StudyForm {
  taskCount: number;
  complexity: "low" | "medium" | "high";
  dependencyPct: number;
  repetitions: number;
  baseSeed: number;
  failureProbability: number;
  maxAgents: string;
  maxDecisions: number;
  maxWallMs: number;
}

const DEFAULT_FORM: StudyForm = {
  taskCount: 20,
  complexity: "medium",
  dependencyPct: 25,
  repetitions: 1,
  baseSeed: 42,
  failureProbability: 0,
  maxAgents: "",
  maxDecisions: 5000,
  maxWallMs: 120000,
};

function formToRequest(form: StudyForm): AblationStudyRequest {
  const parsedAgents = parseInt(form.maxAgents, 10);
  return {
    workload: {
      num_tasks: form.taskCount,
      complexity: form.complexity,
      dependency_percentage: form.dependencyPct,
      seed: form.baseSeed,
    },
    repetitions: form.repetitions,
    base_seed: form.baseSeed,
    max_decisions: form.maxDecisions,
    max_wall_ms: form.maxWallMs,
    max_agents: Number.isNaN(parsedAgents) ? null : parsedAgents,
    failure_probability: form.failureProbability,
  };
}

function StudyFormPanel({
  form,
  onChange,
  disabled,
}: {
  form: StudyForm;
  onChange: (form: StudyForm) => void;
  disabled: boolean;
}) {
  const set = <K extends keyof StudyForm>(key: K, value: StudyForm[K]) =>
    onChange({ ...form, [key]: value });

  const numberField = (
    label: string,
    key: keyof StudyForm,
    min: number,
    max: number,
    step = 1
  ) => (
    <div className="space-y-0.5">
      <label className="block text-xs font-medium text-slate-700">{label}</label>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        className="w-full rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-60"
        value={form[key] as number}
        onChange={(e) => set(key, Number(e.target.value) as StudyForm[typeof key])}
      />
    </div>
  );

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold text-slate-700">
          Shared Experimental Conditions
        </p>
        <span className="text-xs text-slate-500">
          9 configurations × {form.repetitions} repetition
          {form.repetitions === 1 ? "" : "s"} = {9 * form.repetitions} runs
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {numberField("Tasks", "taskCount", 1, 200)}
        <div className="space-y-0.5">
          <label className="block text-xs font-medium text-slate-700">
            Complexity
          </label>
          <select
            disabled={disabled}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs disabled:opacity-60"
            value={form.complexity}
            onChange={(e) =>
              set("complexity", e.target.value as StudyForm["complexity"])
            }
          >
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </div>
        {numberField("Dependency %", "dependencyPct", 0, 100)}
        {numberField("Failure probability", "failureProbability", 0, 1, 0.05)}
        {numberField("Repetitions", "repetitions", 1, 10)}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {numberField("Base seed", "baseSeed", 0, 99999)}
        {numberField("Max decisions", "maxDecisions", 100, 50000, 100)}
        {numberField("Max wall (ms)", "maxWallMs", 1000, 600000, 1000)}
        <div className="space-y-0.5">
          <label className="block text-xs font-medium text-slate-700">
            Max active agents
          </label>
          <input
            type="number"
            min={1}
            placeholder="no cap"
            disabled={disabled}
            className="w-full rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-60"
            value={form.maxAgents}
            onChange={(e) => set("maxAgents", e.target.value)}
          />
        </div>
      </div>

      <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
        <span className="font-medium">Fairness contract:</span> all nine
        configurations run on the <em>same</em> generated graph, the same agent
        pool and the same resource envelope. Repetitions use seeds{" "}
        <code>base_seed + 0, +1, …</code>; each seed is shared by every
        configuration. Only the enabled POSDCORB principles differ.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

function SummaryCards({ result }: { result: AblationStudyResponse }) {
  const baseline = result.configurations.find(
    (c) => c.id === result.baseline_config_id
  );
  const ranked = [...result.configurations].sort(
    (a, b) =>
      (b.composite_improvement ?? Number.NEGATIVE_INFINITY) -
      (a.composite_improvement ?? Number.NEGATIVE_INFINITY)
  );
  const leader = ranked[0];
  const bestLatency = [...result.configurations].sort(
    (a, b) => a.metrics.latency_ms.mean - b.metrics.latency_ms.mean
  )[0];
  const improvedCount = result.configurations.filter(
    (c) => (c.composite_improvement ?? 0) > 0.0001
  ).length;

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
      <MetricCard label="Total runs" value={result.total_runs} icon={Zap} />
      <MetricCard
        label="Elapsed"
        value={`${result.elapsed_s.toFixed(2)}s`}
        icon={Activity}
      />
      <MetricCard
        label="Best composite"
        value={fmtImprovement(leader?.composite_improvement)}
        hint={leader?.label}
        icon={Crown}
        accent="#16a34a"
      />
      <MetricCard
        label="Lowest latency"
        value={
          bestLatency
            ? METRIC_FORMAT.latency_ms(bestLatency.metrics.latency_ms.mean)
            : "—"
        }
        unit="ms"
        hint={bestLatency?.label}
        icon={Scale}
        accent="#0ea5e9"
      />
      <MetricCard
        label="Baseline latency"
        value={
          baseline
            ? METRIC_FORMAT.latency_ms(baseline.metrics.latency_ms.mean)
            : "—"
        }
        unit="ms"
        hint="Conventional Manager"
        icon={BarChart2}
        accent="#d97706"
      />
      <MetricCard
        label="Configs beating baseline"
        value={`${improvedCount}/${result.configurations.length}`}
        hint="Positive composite improvement"
        icon={Layers}
        accent="#7c3aed"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Conditions banner
// ---------------------------------------------------------------------------

function ConditionsBanner({ result }: { result: AblationStudyResponse }) {
  const c = result.conditions;
  return (
    <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900">
      <div className="flex items-start gap-2">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="space-y-1">
          <p>
            <strong>Identical conditions verified.</strong> Fingerprint{" "}
            <code className="rounded bg-white/70 px-1">{c.fingerprint}</code> ·
            agent pool {c.agent_pool.total} ({c.agent_pool.initial} initial +{" "}
            {c.agent_pool.dynamic} dynamic, source: {result.agent_source}) ·
            seeds {c.simulation.seeds.join(", ")} · failure probability{" "}
            {c.simulation.failure_probability ?? 0}
          </p>
          <p className="text-blue-800/80">{c.note}</p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Comparison table
// ---------------------------------------------------------------------------

function comparisonColumns(
  configurations: AblationConfiguration[]
): Column<AblationConfiguration>[] {
  const indexOf = (id: string) =>
    configurations.findIndex((c) => c.id === id);

  return [
    {
      key: "config",
      header: "Configuration",
      sortValue: (r) => r.label,
      render: (r) => (
        <div className="flex items-center gap-2">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ backgroundColor: colorFor(indexOf(r.id)) }}
          />
          <div className="min-w-0">
            <div className="truncate text-xs font-medium text-slate-900">
              {r.label}
            </div>
            <div className="text-[10px] text-slate-400">
              {r.manager_type === "conventional"
                ? "fixed pool"
                : r.adaptive_mechanisms
                ? "principles + adaptive"
                : "principles only"}
            </div>
          </div>
        </div>
      ),
    },
    {
      key: "principles",
      header: "Principles",
      sortValue: (r) => r.enabled_principles.length,
      render: (r) => (
        <span className="font-mono text-[11px] text-slate-600">
          {r.manager_type === "conventional" ? "none" : r.principle_codes}
        </span>
      ),
    },
    {
      key: "activity",
      header: "Active mechanisms",
      render: (r) => (
        <span className="text-[11px] text-slate-500" title="Mean trace events">
          {activeMechanisms(r)}
        </span>
      ),
    },
    {
      key: "rank",
      header: "Rank",
      align: "right",
      sortValue: (r) => r.rank ?? 999,
      render: (r) => (
        <span className="tabular-nums text-xs font-medium">{r.rank ?? "—"}</span>
      ),
    },
    ...METRIC_ORDER.map<Column<AblationConfiguration>>((metric) => ({
      key: metric,
      header: METRIC_LABEL[metric],
      align: "right",
      sortValue: (r) => r.metrics[metric].mean,
      render: (r) => <MetricCell metric={metric} value={r.metrics[metric]} />,
    })),
    {
      key: "composite",
      header: "Composite Δ",
      align: "right",
      sortValue: (r) => r.composite_improvement ?? -Infinity,
      render: (r) => <ImprovementCell value={r.composite_improvement} />,
    },
  ];
}

function ComparisonSection({
  result,
}: {
  result: AblationStudyResponse;
}) {
  const columns = useMemo(
    () => comparisonColumns(result.configurations),
    [result.configurations]
  );

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Configuration Comparison"
        description="Mean ± SD across repetitions. Activation count is descriptive; all other metrics feed the composite score."
        icon={BarChart2}
      />
      <DataTable
        columns={columns}
        rows={result.configurations}
        rowKey={(r) => r.id}
        emptyMessage="No configurations returned."
      />
      <p className="text-[11px] text-slate-500">
        Composite Δ is the mean directional improvement across latency,
        throughput, success, CPU utilisation, coordination overhead and agent
        utilisation. Positive = better than the Conventional baseline.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------

function MetricBarChart({
  metric,
  configurations,
}: {
  metric: AblationMetricKey;
  configurations: AblationConfiguration[];
}) {
  const data = configurations.map((c, index) => ({
    id: c.id,
    name: c.short_label,
    label: c.label,
    value: c.metrics[metric].mean,
    color: colorFor(index),
  }));
  const fmt = METRIC_FORMAT[metric];

  return (
    <ChartCard
      title={METRIC_LABEL[metric]}
      subtitle={`Unit: ${METRIC_UNIT[metric]} · mean across repetitions`}
      height={230}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
          <XAxis dataKey="name" tick={AXIS_TICK} interval={0} angle={-25} textAnchor="end" height={48} />
          <YAxis tick={AXIS_TICK} width={52} tickFormatter={(v) => fmt(Number(v))} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value) => [fmt(Number(value)), METRIC_LABEL[metric]]}
            labelFormatter={(name) => {
              const row = data.find((d) => d.name === name);
              return row?.label ?? String(name);
            }}
          />
          <Bar dataKey="value" radius={[3, 3, 0, 0]}>
            {data.map((d) => (
              <Cell key={d.id} fill={d.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function MetricChartsSection({
  result,
}: {
  result: AblationStudyResponse;
}) {
  return (
    <div className="space-y-3">
      <SectionHeader
        title="Grouped Metric Charts"
        description="One bar per configuration, coloured consistently across all seven charts."
        icon={BarChart2}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        {METRIC_ORDER.map((metric) => (
          <MetricBarChart
            key={metric}
            metric={metric}
            configurations={result.configurations}
          />
        ))}
      </div>
    </div>
  );
}

function ImprovementChart({
  result,
}: {
  result: AblationStudyResponse;
}) {
  const data = result.configurations.map((c) => {
    const row: Record<string, string | number> = { name: c.short_label };
    for (const series of IMPROVEMENT_SERIES) {
      row[series.key] =
        c.improvements_vs_baseline[series.key]?.improvement_pct ?? 0;
    }
    return row;
  });

  return (
    <ChartCard
      title="Directional Improvement vs Baseline"
      subtitle="Positive = better than the Conventional Manager; grouped bars show each metric series"
      height={300}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
          <XAxis dataKey="name" tick={AXIS_TICK} interval={0} angle={-25} textAnchor="end" height={48} />
          <YAxis
            tick={AXIS_TICK}
            width={48}
            tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value, name) => [
              `${Number(value).toFixed(1)}%`,
              String(name),
            ]}
          />
          <Legend wrapperStyle={LEGEND_STYLE} />
          <ReferenceLine y={0} stroke="#94a3b8" />
          {IMPROVEMENT_SERIES.map((series) => (
            <Bar
              key={series.key}
              dataKey={series.key}
              name={series.label}
              fill={series.color}
              radius={[2, 2, 0, 0]}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// Improvement table
// ---------------------------------------------------------------------------

function improvementColumns(): Column<AblationConfiguration>[] {
  return [
    {
      key: "config",
      header: "Configuration",
      sortValue: (r) => r.label,
      render: (r) => (
        <span className="text-xs font-medium text-slate-900">{r.label}</span>
      ),
    },
    ...METRIC_ORDER.map<Column<AblationConfiguration>>((metric) => ({
      key: `imp-${metric}`,
      header: METRIC_LABEL[metric],
      align: "right",
      sortValue: (r) =>
        r.improvements_vs_baseline[metric]?.improvement_pct ?? -Infinity,
      render: (r) => {
        const imp = r.improvements_vs_baseline[metric];
        if (metric === "activation_count") {
          const delta = imp?.absolute_delta ?? 0;
          return (
            <span className="tabular-nums text-xs text-slate-600">
              {delta > 0 ? "+" : ""}
              {delta.toFixed(1)}
            </span>
          );
        }
        return <ImprovementCell value={imp?.improvement_pct} />;
      },
    })),
    {
      key: "imp-composite",
      header: "Composite Δ",
      align: "right",
      sortValue: (r) => r.composite_improvement ?? -Infinity,
      render: (r) => <ImprovementCell value={r.composite_improvement} />,
    },
  ];
}

function ImprovementSection({ result }: { result: AblationStudyResponse }) {
  const columns = useMemo(() => improvementColumns(), []);
  return (
    <div className="space-y-3">
      <SectionHeader
        title="Improvement Relative to Baseline"
        description="Percent change is directional: positive always means better. Activation count is shown as a signed delta only."
        icon={Scale}
      />
      <ImprovementChart result={result} />
      <DataTable
        columns={columns}
        rows={result.configurations}
        rowKey={(r) => `imp-${r.id}`}
        emptyMessage="No improvement data."
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Principle contributions
// ---------------------------------------------------------------------------

function PrincipleChart({
  contributions,
}: {
  contributions: AblationPrincipleContribution[];
}) {
  const data = contributions.map((c) => ({
    name: c.label,
    value: c.composite_improvement ?? 0,
    config: c.config_label,
  }));

  return (
    <ChartCard
      title="Marginal Contribution of Each Principle"
      subtitle="Composite improvement of the rung that introduced the principle vs the previous rung"
      height={260}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
          <XAxis dataKey="name" tick={AXIS_TICK} interval={0} angle={-20} textAnchor="end" height={56} />
          <YAxis
            tick={AXIS_TICK}
            width={48}
            tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value) => [
              `${Number(value).toFixed(1)}%`,
              "Composite improvement",
            ]}
            labelFormatter={(name) => String(name)}
          />
          <ReferenceLine y={0} stroke="#94a3b8" />
          <Bar dataKey="value" radius={[3, 3, 0, 0]}>
            {data.map((d, index) => (
              <Cell
                key={`${d.name}-${index}`}
                fill={(d.value ?? 0) > 0 ? "#16a34a" : "#dc2626"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function PrincipleSection({ result }: { result: AblationStudyResponse }) {
  const columns: Column<AblationPrincipleContribution>[] = [
    {
      key: "principle",
      header: "Added principle",
      sortValue: (r) => r.label,
      render: (r) => (
        <span className="text-xs font-medium text-slate-900">{r.label}</span>
      ),
    },
    {
      key: "config",
      header: "Rung",
      render: (r) => (
        <div className="text-[11px] text-slate-500">
          <div className="text-slate-700">{r.config_label}</div>
          <div>vs {r.compared_to}</div>
        </div>
      ),
    },
    {
      key: "composite",
      header: "Composite Δ",
      align: "right",
      sortValue: (r) => r.composite_improvement ?? -Infinity,
      render: (r) => <ImprovementCell value={r.composite_improvement} />,
    },
    ...METRIC_ORDER.filter((m) => m !== "activation_count").map<
      Column<AblationPrincipleContribution>
    >((metric) => ({
      key: `eff-${metric}`,
      header: METRIC_LABEL[metric],
      align: "right",
      sortValue: (r) => r.effects[metric]?.improvement_pct ?? -Infinity,
      render: (r) => (
        <ImprovementCell value={r.effects[metric]?.improvement_pct} />
      ),
    })),
  ];

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Which Principles Contribute Most?"
        description="Each row compares a cumulative rung with the rung immediately before it, isolating the newly added principle."
        icon={Layers}
      />
      <PrincipleChart contributions={result.principle_contributions} />
      <DataTable
        columns={columns}
        rows={result.principle_contributions}
        rowKey={(r) => `${r.config_id}-${r.principle}`}
        emptyMessage="No principle contributions."
      />
      <div className="flex items-start gap-2 rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          A 0.0% contribution means the mechanism ran but did not change the
          measured outcome under these conditions. Check the{" "}
          <strong>Active mechanisms</strong> column: it lists trace events that
          actually executed. Contributions are workload-dependent, so change
          task count, dependencies or failure probability to probe other
          regimes.
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Raw runs
// ---------------------------------------------------------------------------

function RawRunsSection({ result }: { result: AblationStudyResponse }) {
  const rows = result.configurations.flatMap((c) => c.raw_runs);

  const columns: Column<AblationRawRun>[] = [
    {
      key: "config",
      header: "Configuration",
      sortValue: (r) => r.config_id,
      render: (r) => (
        <span className="text-[11px] text-slate-700">{r.config_id}</span>
      ),
    },
    { key: "rep", header: "Rep", align: "right", sortValue: (r) => r.repetition },
    { key: "seed", header: "Seed", align: "right", sortValue: (r) => r.seed },
    {
      key: "latency",
      header: "Latency",
      align: "right",
      sortValue: (r) => r.metrics.latency_ms,
      render: (r) => (
        <span className="tabular-nums text-xs">
          {METRIC_FORMAT.latency_ms(r.metrics.latency_ms)}
        </span>
      ),
    },
    {
      key: "throughput",
      header: "Throughput",
      align: "right",
      sortValue: (r) => r.metrics.throughput_tasks_per_sec,
      render: (r) => (
        <span className="tabular-nums text-xs">
          {METRIC_FORMAT.throughput_tasks_per_sec(
            r.metrics.throughput_tasks_per_sec
          )}
        </span>
      ),
    },
    {
      key: "success",
      header: "Success",
      align: "right",
      sortValue: (r) => r.metrics.success_rate,
      render: (r) => (
        <span className="tabular-nums text-xs">
          {METRIC_FORMAT.success_rate(r.metrics.success_rate)}
        </span>
      ),
    },
    {
      key: "cpu",
      header: "CPU",
      align: "right",
      sortValue: (r) => r.metrics.cpu_utilization,
      render: (r) => (
        <span className="tabular-nums text-xs">
          {METRIC_FORMAT.cpu_utilization(r.metrics.cpu_utilization)}
        </span>
      ),
    },
    {
      key: "overhead",
      header: "Overhead",
      align: "right",
      sortValue: (r) => r.metrics.coordination_overhead,
      render: (r) => (
        <span className="tabular-nums text-xs">
          {METRIC_FORMAT.coordination_overhead(
            r.metrics.coordination_overhead
          )}
        </span>
      ),
    },
    {
      key: "util",
      header: "Agent util.",
      align: "right",
      sortValue: (r) => r.metrics.agent_utilization,
      render: (r) => (
        <span className="tabular-nums text-xs">
          {METRIC_FORMAT.agent_utilization(r.metrics.agent_utilization)}
        </span>
      ),
    },
    {
      key: "activations",
      header: "Activations",
      align: "right",
      sortValue: (r) => r.metrics.activation_count,
      render: (r) => (
        <span className="tabular-nums text-xs">
          {r.metrics.activation_count.toFixed(0)}
        </span>
      ),
    },
    {
      key: "wall",
      header: "Wall ms",
      align: "right",
      sortValue: (r) => r.total_wall_ms,
      render: (r) => (
        <span className="tabular-nums text-xs">
          {Math.round(r.total_wall_ms).toLocaleString()}
        </span>
      ),
    },
    {
      key: "reason",
      header: "Termination",
      className: "text-[11px] text-slate-500",
      sortValue: (r) => r.termination_reason,
    },
  ];

  return (
    <details className="rounded-md border border-slate-200 bg-white">
      <summary className="cursor-pointer px-4 py-2.5 text-sm font-semibold text-slate-900">
        Raw run records ({rows.length})
      </summary>
      <div className="border-t border-slate-100 p-3">
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => `${r.config_id}-${r.repetition}`}
          emptyMessage="No raw runs."
        />
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type PageStatus = "idle" | "running" | "done" | "error";

export default function AblationPage() {
  const [status, setStatus] = useState<PageStatus>("idle");
  const [form, setForm] = useState<StudyForm>(DEFAULT_FORM);
  const [result, setResult] = useState<AblationStudyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isRunning = status === "running";
  const estimatedRuns = 9 * form.repetitions;

  async function handleRun() {
    setStatus("running");
    setError(null);
    setResult(null);
    try {
      const response = await api.runAblation(formToRequest(form));
      setResult(response);
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
  }

  return (
    <>
      <SectionHeader
        title="POSDCORB Ablation Study"
        description="Compare nine cumulative manager configurations under identical conditions to identify which POSDCORB mechanisms contribute most to system performance."
        icon={Layers}
        actions={
          <>
            <button className={btn} onClick={handleRun} disabled={isRunning}>
              {isRunning ? (
                <>
                  <Activity className="h-4 w-4 animate-pulse" /> Running…
                </>
              ) : (
                <>
                  <FlaskConical className="h-4 w-4" /> Run Study
                </>
              )}
            </button>
            <button className={btn} onClick={handleReset} disabled={isRunning}>
              <RotateCcw className="h-4 w-4" /> Reset
            </button>
          </>
        }
      />

      <StudyFormPanel form={form} onChange={setForm} disabled={isRunning} />

      {status === "idle" && (
        <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          Configure the shared conditions above, then click{" "}
          <strong>Run Study</strong>. All nine configurations run on the same
          workload and worker pool; only the enabled POSDCORB principles change.
        </div>
      )}

      {isRunning && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <span className="animate-pulse">●</span>&nbsp; Running {estimatedRuns}{" "}
          manager simulations… this may take a few seconds.
        </div>
      )}

      {status === "error" && error && (
        <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <strong>Error:</strong> {error}
          </div>
        </div>
      )}

      {status === "done" && result && (
        <>
          <SummaryCards result={result} />
          <ConditionsBanner result={result} />

          {result.errors.length > 0 && (
            <details className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <summary className="cursor-pointer font-medium">
                {result.errors.length} non-fatal run error(s) — click to expand
              </summary>
              <ul className="mt-2 space-y-0.5">
                {result.errors.map((item, index) => (
                  <li key={index}>• {item}</li>
                ))}
              </ul>
            </details>
          )}

          <ComparisonSection result={result} />
          <MetricChartsSection result={result} />
          <ImprovementSection result={result} />
          <PrincipleSection result={result} />
          <RawRunsSection result={result} />
        </>
      )}
    </>
  );
}
