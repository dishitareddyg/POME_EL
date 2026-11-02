/**
 * ComparisonPage — Module 9: Research Comparison Dashboard
 *
 * Conventional Manager vs POSDCORB Adaptive Manager.
 *
 * State machine:  idle → running → done | error
 *                 Reset always returns to idle.
 *
 * Data flow:
 *   1. User clicks "Run Comparison"
 *   2. POST /api/simulate  → SimulateResponse
 *   3. POST /api/metrics/compare → CompareResponse
 *   4. Render all sections from both responses
 *
 * Design rules:
 *   - Neutral language: never auto-claim one manager is "better"
 *   - Use ComparisonRow.better_manager + .interpretation from backend
 *   - N/A for unavailable host metrics (resource_monitor.available === false)
 *   - All existing components are reused; none are rewritten
 */
import {
  Activity,
  AlertTriangle,
  BarChart2,
  BookOpen,
  Clock,
  FlaskConical,
  Info,
  Layers,
  RotateCcw,
  Server,
  Shield,
  Users,
  Zap,
} from "lucide-react";
import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import ChartCard from "../components/ChartCard";
import ComparisonCard from "../components/ComparisonCard";
import DataTable, { type Column } from "../components/DataTable";
import MetricCard from "../components/MetricCard";
import SectionHeader from "../components/SectionHeader";
import StatusBadge from "../components/StatusBadge";
import Tabs from "../components/Tabs";
import {
  AXIS_TICK,
  GRID_STROKE,
  LEGEND_STYLE,
  TOOLTIP_STYLE,
} from "../components/chartStyle";
import { MANAGER_COLORS, MANAGER_LABELS } from "../data/managers";
import { api } from "../services/api";
import { fmtMs, fmtNum, fmtPct } from "../services/format";
import type { CompareResponse, ComparisonRow } from "../types/metrics";
import type { DecisionRecord, SimulateResponse } from "../types/simulation";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const DEFAULT_WORKLOAD = {
  num_tasks: 14,
  complexity: "medium" as const,
  dependency_percentage: 30,
  seed: 42,
};

const btn =
  "inline-flex items-center gap-1.5 rounded border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-40 bg-white hover:bg-slate-50";

// ---------------------------------------------------------------------------
// Helper utilities
// ---------------------------------------------------------------------------

function fmt(v: number | undefined | null, decimals = 1): string {
  if (v === undefined || v === null || isNaN(v)) return "—";
  return fmtNum(v, decimals);
}

function fmtMsOrDash(v: number | undefined | null): string {
  if (v === undefined || v === null || isNaN(v)) return "—";
  return fmtMs(v);
}

function fmtPctOrDash(v: number | undefined | null, multiply = false): string {
  if (v === undefined || v === null || isNaN(v)) return "—";
  return fmtPct(multiply ? v * 100 : v);
}

/** Signed delta badge (positive = POSDCORB larger). */
function DeltaBadge({ pct, baseline }: { pct: number; baseline?: number }) {
  // A percentage change relative to a zero baseline is undefined; the backend
  // reports 0.0 and explains this in the interpretation.  Show "—" instead of
  // a fabricated "+0.0%".
  if (baseline !== undefined && Math.abs(baseline) < 1e-9) {
    return <span className="text-xs text-slate-400" title="Baseline is zero; relative change undefined">—</span>;
  }
  const sign = pct >= 0 ? "+" : "";
  const color =
    Math.abs(pct) < 0.5
      ? "text-slate-500"
      : pct > 0
        ? "text-blue-700"
        : "text-amber-700";
  return (
    <span className={`text-xs font-medium ${color}`}>
      {sign}{pct.toFixed(1)}%
    </span>
  );
}

/** Neutral "better manager" label — shows raw API value, no emoji bias. */
function BetterBadge({ better }: { better: string }) {
  const label =
    better === "conventional"
      ? "Conventional"
      : better === "posdcorb"
        ? "POSDCORB"
        : better === "tie"
          ? "Tie"
          : "Context";
  const color =
    better === "conventional"
      ? "bg-amber-50 text-amber-700 border-amber-200"
      : better === "posdcorb"
        ? "bg-blue-50 text-blue-700 border-blue-200"
        : "bg-slate-50 text-slate-600 border-slate-200";
  return (
    <span className={`rounded border px-1.5 py-0.5 text-xs font-medium ${color}`}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Section A — Experiment Summary
// ---------------------------------------------------------------------------

function SummarySectionA({
  sim,
  cmp,
}: {
  sim: SimulateResponse;
  cmp: CompareResponse;
}) {
  const conv = sim.results["conventional"];
  const posd = sim.results["posdcorb"];
  const cm = cmp.conventional_metrics;
  const pm = cmp.posdcorb_metrics;

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Experiment Summary"
        description="High-level outcome for both managers on the same workload."
        icon={BookOpen}
      />
      {/* Manager identity row */}
      <div className="grid gap-3 sm:grid-cols-2">
        {(["conventional", "posdcorb"] as const).map((m) => {
          const r = sim.results[m];
          return (
            <div
              key={m}
              className="rounded-md border border-slate-200 border-t-4 bg-white p-3"
              style={{ borderTopColor: MANAGER_COLORS[m] }}
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold">{MANAGER_LABELS[m]}</span>
                <StatusBadge status="completed" label={sim.termination_reasons[m] ?? "done"} />
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <MetricCard label="Completed" value={r?.completed_tasks ?? "—"} accent={MANAGER_COLORS[m]} />
                <MetricCard label="Failed" value={r?.failed_tasks ?? "—"} />
                <MetricCard
                  label="Success rate"
                  value={r ? fmtPct(r.success_rate * 100) : "—"}
                  accent={MANAGER_COLORS[m]}
                />
                <MetricCard
                  label="Wall time"
                  value={r ? fmtMsOrDash(r.total_wall_ms) : "—"}
                />
              </div>
            </div>
          );
        })}
      </div>

      {/* Aggregate cards */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <MetricCard
          label="Conventional — decisions"
          value={conv?.total_decisions ?? "—"}
          icon={Zap}
          accent={MANAGER_COLORS.conventional}
        />
        <MetricCard
          label="POSDCORB — decisions"
          value={posd?.total_decisions ?? "—"}
          icon={Zap}
          accent={MANAGER_COLORS.posdcorb}
        />
        <MetricCard
          label="Conventional — avg latency"
          value={cm ? fmtMsOrDash(cm.performance.avg_task_latency_ms) : "—"}
          icon={Clock}
          accent={MANAGER_COLORS.conventional}
        />
        <MetricCard
          label="POSDCORB — avg latency"
          value={pm ? fmtMsOrDash(pm.performance.avg_task_latency_ms) : "—"}
          icon={Clock}
          accent={MANAGER_COLORS.posdcorb}
        />
        <MetricCard
          label="Conventional — active agents"
          value={cm ? cm.agents.total_active_agents : "—"}
          icon={Users}
          accent={MANAGER_COLORS.conventional}
        />
        <MetricCard
          label="POSDCORB — active agents"
          value={pm ? pm.agents.total_active_agents : "—"}
          icon={Users}
          accent={MANAGER_COLORS.posdcorb}
        />
        <MetricCard
          label="Conventional — overhead ratio"
          value={cm ? fmtPctOrDash(cm.management.coordination_overhead_ratio * 100) : "—"}
          icon={Activity}
          accent={MANAGER_COLORS.conventional}
        />
        <MetricCard
          label="POSDCORB — overhead ratio"
          value={pm ? fmtPctOrDash(pm.management.coordination_overhead_ratio * 100) : "—"}
          icon={Activity}
          accent={MANAGER_COLORS.posdcorb}
        />
      </div>

      {/* Metadata strip */}
      <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-2.5 text-xs text-slate-600">
        <span className="font-medium">Graph:</span> {sim.graph_id} &nbsp;·&nbsp;
        <span className="font-medium">Seed:</span> {sim.seed} &nbsp;·&nbsp;
        <span className="font-medium">Elapsed (wall):</span> {sim.real_elapsed_s.toFixed(2)}s &nbsp;·&nbsp;
        <span className="font-medium">Max wall ms:</span> {sim.max_wall_ms?.toLocaleString() ?? "—"}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section B — Performance Comparison
// ---------------------------------------------------------------------------

const PERF_METRIC_NAMES = [
  "throughput_tasks_per_sec",
  "task_success_rate",
  "avg_task_latency_ms",
  "total_execution_latency_ms",
];

function PerformanceSection({ cmp }: { cmp: CompareResponse }) {
  const rows = cmp.rows.filter((r) => PERF_METRIC_NAMES.includes(r.metric));
  const cm = cmp.conventional_metrics?.performance;
  const pm = cmp.posdcorb_metrics?.performance;

  const barData = rows.map((r) => ({
    name: r.metric.replace(/_/g, " ").replace(/ms$/, " (ms)").replace(/sec$/, "/s"),
    Conventional: r.conventional_value,
    POSDCORB: r.posdcorb_value,
  }));

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Performance Comparison"
        description="Task throughput, latency, and success rate for both managers."
        icon={BarChart2}
      />

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {cm && pm && (
          <>
            <ComparisonCard
              label="Throughput (tasks/s)"
              conventional={cm.throughput_tasks_per_sec}
              posdcorb={pm.throughput_tasks_per_sec}
              decimals={4}
            />
            <ComparisonCard
              label="Success rate"
              unit="%"
              conventional={cm.task_success_rate * 100}
              posdcorb={pm.task_success_rate * 100}
              decimals={1}
            />
            <ComparisonCard
              label="Avg task latency"
              unit="ms"
              conventional={cm.avg_task_latency_ms}
              posdcorb={pm.avg_task_latency_ms}
              lowerIsBetter
              decimals={0}
            />
            <ComparisonCard
              label="Total wall time"
              unit="ms"
              conventional={cm.total_execution_latency_ms}
              posdcorb={pm.total_execution_latency_ms}
              lowerIsBetter
              decimals={0}
            />
          </>
        )}
      </div>

      <ChartCard title="Performance metrics — side-by-side" subtitle="Values differ in scale; each metric is independent" height={260}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={barData} margin={{ left: 0, right: 8, top: 4, bottom: 40 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
            <XAxis dataKey="name" tick={{ ...AXIS_TICK, fontSize: 9 }} angle={-25} textAnchor="end" interval={0} />
            <YAxis tick={AXIS_TICK} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend wrapperStyle={LEGEND_STYLE} />
            <Bar dataKey="Conventional" fill={MANAGER_COLORS.conventional} radius={[2, 2, 0, 0]} />
            <Bar dataKey="POSDCORB" fill={MANAGER_COLORS.posdcorb} radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* All rows table */}
      <ComparisonRowTable rows={rows} title="Performance metric details" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section C — Resource & Agent Utilization
// ---------------------------------------------------------------------------

function ResourceSection({ cmp }: { cmp: CompareResponse }) {
  const cm = cmp.conventional_metrics;
  const pm = cmp.posdcorb_metrics;

  const agentBarData = cm && pm
    ? [
        {
          name: "Initial agents",
          Conventional: cm.agents.initial_agent_count,
          POSDCORB: pm.agents.initial_agent_count,
        },
        {
          name: "Dynamic activations",
          Conventional: cm.agents.dynamic_agent_count,
          POSDCORB: pm.agents.dynamic_agent_count,
        },
        {
          name: "Total active",
          Conventional: cm.agents.total_active_agents,
          POSDCORB: pm.agents.total_active_agents,
        },
        {
          name: "Failed agents",
          Conventional: cm.agents.failed_agent_count,
          POSDCORB: pm.agents.failed_agent_count,
        },
      ]
    : [];

  const agentRows = cmp.rows.filter((r) =>
    ["avg_agent_utilization", "activation_count", "failed_agent_count", "total_active_agents"].includes(r.metric)
  );

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Agent Utilization"
        description="How each manager used available and dynamically activated agents."
        icon={Users}
      />

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {cm && pm && (
          <>
            <ComparisonCard
              label="Avg agent utilization"
              unit="%"
              conventional={cm.agents.avg_agent_utilization * 100}
              posdcorb={pm.agents.avg_agent_utilization * 100}
              decimals={1}
            />
            <ComparisonCard
              label="Total active agents"
              conventional={cm.agents.total_active_agents}
              posdcorb={pm.agents.total_active_agents}
              decimals={0}
            />
            <ComparisonCard
              label="Dynamic activations"
              conventional={cm.agents.dynamic_agent_count}
              posdcorb={pm.agents.dynamic_agent_count}
              decimals={0}
            />
            <ComparisonCard
              label="Failed agents"
              conventional={cm.agents.failed_agent_count}
              posdcorb={pm.agents.failed_agent_count}
              lowerIsBetter
              decimals={0}
            />
          </>
        )}
      </div>

      <ChartCard title="Agent counts — Conventional vs POSDCORB" height={220}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={agentBarData} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
            <XAxis dataKey="name" tick={AXIS_TICK} />
            <YAxis tick={AXIS_TICK} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend wrapperStyle={LEGEND_STYLE} />
            <Bar dataKey="Conventional" fill={MANAGER_COLORS.conventional} radius={[2, 2, 0, 0]} />
            <Bar dataKey="POSDCORB" fill={MANAGER_COLORS.posdcorb} radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      <ComparisonRowTable rows={agentRows} title="Agent metric details" />

      {/* Host resource monitor note */}
      {cm && !cm.resource_monitor.available && (
        <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
          <Info className="h-3.5 w-3.5 shrink-0" />
          Host CPU/memory metrics unavailable (psutil not installed). Agent utilization values above are
          simulated — they reflect decision-latency model, not real CPU usage.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section D — Management Overhead
// ---------------------------------------------------------------------------

function ManagementSection({ cmp }: { cmp: CompareResponse }) {
  const cm = cmp.conventional_metrics?.management;
  const pm = cmp.posdcorb_metrics?.management;

  const overheadRows = cmp.rows.filter((r) =>
    [
      "total_decisions",
      "management_time_ms",
      "coordination_overhead_ratio",
      "planning_events",
      "staffing_decisions",
      "coordination_events",
      "replanning_events",
      "reassignment_count",
    ].includes(r.metric)
  );

  const barData = cm && pm
    ? [
        { name: "Total decisions", Conventional: cm.total_decisions, POSDCORB: pm.total_decisions },
        { name: "Planning events", Conventional: cm.planning_events, POSDCORB: pm.planning_events },
        { name: "Staffing", Conventional: cm.staffing_decisions, POSDCORB: pm.staffing_decisions },
        { name: "Coordination", Conventional: cm.coordination_events, POSDCORB: pm.coordination_events },
        { name: "Reassignments", Conventional: cm.reassignment_count, POSDCORB: pm.reassignment_count },
        { name: "Replanning", Conventional: cm.replanning_events, POSDCORB: pm.replanning_events },
      ]
    : [];

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Management Overhead"
        description="Decision counts and coordination cost for each manager."
        icon={Activity}
      />

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {cm && pm && (
          <>
            <ComparisonCard label="Total decisions" conventional={cm.total_decisions} posdcorb={pm.total_decisions} decimals={0} />
            <ComparisonCard label="Planning events" conventional={cm.planning_events} posdcorb={pm.planning_events} decimals={0} />
            <ComparisonCard label="Staffing decisions" conventional={cm.staffing_decisions} posdcorb={pm.staffing_decisions} decimals={0} />
            <ComparisonCard
              label="Overhead ratio"
              unit="%"
              conventional={cm.coordination_overhead_ratio * 100}
              posdcorb={pm.coordination_overhead_ratio * 100}
              lowerIsBetter
              decimals={2}
            />
          </>
        )}
      </div>

      <ChartCard title="Management event counts" height={240}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={barData} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
            <XAxis dataKey="name" tick={AXIS_TICK} />
            <YAxis tick={AXIS_TICK} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend wrapperStyle={LEGEND_STYLE} />
            <Bar dataKey="Conventional" fill={MANAGER_COLORS.conventional} radius={[2, 2, 0, 0]} />
            <Bar dataKey="POSDCORB" fill={MANAGER_COLORS.posdcorb} radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      <ComparisonRowTable rows={overheadRows} title="Management metric details" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section E — Dynamic Staffing (POSDCORB focus)
// ---------------------------------------------------------------------------

function StaffingSection({ cmp, sim }: { cmp: CompareResponse; sim: SimulateResponse }) {
  const pm = cmp.posdcorb_metrics;
  const cm = cmp.conventional_metrics;
  const report = sim.results["posdcorb"]?.management_report;

  const staffRows: { label: string; conv: string; posd: string }[] = [
    {
      label: "Initial agent count",
      conv: fmt(cm?.staffing.initial_agent_count, 0),
      posd: fmt(pm?.staffing.initial_agent_count, 0),
    },
    {
      label: "Max active agents",
      conv: fmt(cm?.staffing.max_active_agents, 0),
      posd: fmt(pm?.staffing.max_active_agents, 0),
    },
    {
      label: "Avg active agents",
      conv: fmt(cm?.staffing.avg_active_agents),
      posd: fmt(pm?.staffing.avg_active_agents),
    },
    {
      label: "Activation count",
      conv: fmt(cm?.staffing.activation_count, 0),
      posd: fmt(pm?.staffing.activation_count, 0),
    },
    {
      label: "Activation rate",
      conv: fmtPctOrDash(cm ? cm.staffing.activation_rate * 100 : null),
      posd: fmtPctOrDash(pm ? pm.staffing.activation_rate * 100 : null),
    },
    {
      label: "Est. activation cost",
      conv: fmtMsOrDash(cm?.staffing.estimated_activation_cost_ms),
      posd: fmtMsOrDash(pm?.staffing.estimated_activation_cost_ms),
    },
    {
      label: "Tasks per active agent",
      conv: fmt(cm?.staffing.tasks_per_active_agent),
      posd: fmt(pm?.staffing.tasks_per_active_agent),
    },
  ];

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Dynamic Staffing"
        description="How POSDCORB adjusts agent headcount vs the fixed pool of the Conventional manager."
        icon={Layers}
      />

      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-600">
            <tr>
              <th className="px-3 py-2 font-medium">Metric</th>
              <th className="px-3 py-2 font-medium" style={{ color: MANAGER_COLORS.conventional }}>Conventional</th>
              <th className="px-3 py-2 font-medium" style={{ color: MANAGER_COLORS.posdcorb }}>POSDCORB</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {staffRows.map((r) => (
              <tr key={r.label} className="hover:bg-slate-50">
                <td className="px-3 py-1.5 text-xs font-medium text-slate-700">{r.label}</td>
                <td className="px-3 py-1.5 text-xs tabular-nums text-slate-600">{r.conv}</td>
                <td className="px-3 py-1.5 text-xs tabular-nums text-slate-600">{r.posd}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* POSDCORB management report detail */}
      {report && (
        <div className="rounded-md border border-blue-100 bg-blue-50 px-4 py-3">
          <p className="mb-2 text-xs font-semibold text-blue-800">POSDCORB ManagementReport — staffing snapshot</p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-blue-900 sm:grid-cols-3">
            <span>Initial: <b>{report.agents.initial_agent_count}</b></span>
            <span>Active: <b>{report.agents.active_agent_count}</b></span>
            <span>Idle: <b>{report.agents.idle_agent_count}</b></span>
            <span>Busy: <b>{report.agents.busy_agent_count}</b></span>
            <span>Dynamically activated: <b>{report.agents.dynamically_activated_count}</b></span>
            <span>Failed: <b>{report.agents.failed_agent_count}</b></span>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-blue-900 sm:grid-cols-3">
            <span>Staffing decisions: <b>{report.management.staffing_decisions}</b></span>
            <span>Parallel groups: <b>{report.management.parallel_group_count}</b></span>
            <span>Plan updates: <b>{report.management.plan_updates}</b></span>
          </div>
        </div>
      )}

      {!report && (
        <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
          <Info className="h-3.5 w-3.5 shrink-0" />
          POSDCORB ManagementReport not available (simulation may have terminated early).
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section F — Reliability
// ---------------------------------------------------------------------------

function ReliabilitySection({ cmp }: { cmp: CompareResponse }) {
  const cm = cmp.conventional_metrics?.reliability;
  const pm = cmp.posdcorb_metrics?.reliability;

  const relRows = cmp.rows.filter((r) =>
    [
      "task_failure_count",
      "agent_failure_count",
      "retry_count",
      "recovery_count",
      "recovery_success_rate",
      "tasks_lost_due_to_failures",
    ].includes(r.metric)
  );

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Reliability"
        description="Failure rates, retry behaviour, and recovery effectiveness."
        icon={Shield}
      />

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {cm && pm && (
          <>
            <ComparisonCard label="Task failures" conventional={cm.task_failure_count} posdcorb={pm.task_failure_count} lowerIsBetter decimals={0} />
            <ComparisonCard label="Agent failures" conventional={cm.agent_failure_count} posdcorb={pm.agent_failure_count} lowerIsBetter decimals={0} />
            <ComparisonCard label="Retries" conventional={cm.retry_count} posdcorb={pm.retry_count} decimals={0} />
            <ComparisonCard label="Recoveries" conventional={cm.recovery_count} posdcorb={pm.recovery_count} decimals={0} />
            <ComparisonCard
              label="Recovery rate"
              unit="%"
              conventional={cm.recovery_success_rate * 100}
              posdcorb={pm.recovery_success_rate * 100}
              decimals={1}
            />
            <ComparisonCard label="Tasks lost" conventional={cm.tasks_lost_due_to_failures} posdcorb={pm.tasks_lost_due_to_failures} lowerIsBetter decimals={0} />
          </>
        )}
      </div>

      <ComparisonRowTable rows={relRows} title="Reliability metric details" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section G — Decision Sequence Timeline
// ---------------------------------------------------------------------------

/**
 * Approximate task execution timeline derived from the decision trace.
 *
 * NOTE: The trace records contain ISO wall-clock timestamps (when the Python
 * interpreter processed the decision), not simulated task start/end times.
 * We use decision-index as the x-axis — this is honest and clearly labeled.
 * Blocks represent "assign"/"evaluate"/"posdcorb:TASK_COMPLETED" decisions per agent.
 */
function TimelineSection({
  convDecisions,
  posdDecisions,
}: {
  convDecisions: DecisionRecord[];
  posdDecisions: DecisionRecord[];
}) {
  const [active, setActive] = useState<"conventional" | "posdcorb">("conventional");
  const decisions = active === "conventional" ? convDecisions : posdDecisions;
  const color = MANAGER_COLORS[active];

  // Build per-agent timeline strips
  const agentOrder: string[] = [];
  const agentMap = new Map<string, { idx: number; type: string; task: string | null }[]>();

  decisions.forEach((d, idx) => {
    const agentId = d.selected_agent_id ?? "__engine__";
    const agentName = d.selected_agent_name ?? agentId;
    const key = `${agentId}::${agentName}`;
    if (!agentMap.has(key)) {
      agentOrder.push(key);
      agentMap.set(key, []);
    }
    agentMap.get(key)!.push({ idx, type: d.manager_decision, task: d.task_id });
  });

  const total = decisions.length;
  const pxPerDecision = Math.max(3, Math.min(12, 900 / Math.max(1, total)));

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Decision Sequence Timeline"
        description={
          "Approximate: x-axis = decision index (not real time). " +
          "Each row = one agent; each block = one decision event involving that agent."
        }
        icon={Clock}
        actions={
          <Tabs
            items={[
              { id: "conventional", label: "Conventional" },
              { id: "posdcorb", label: "POSDCORB" },
            ]}
            active={active}
            onChange={setActive}
          />
        }
      />

      <div className="rounded-md border border-slate-200 bg-white p-3 overflow-x-auto">
        {agentOrder.length === 0 ? (
          <p className="py-4 text-center text-xs text-slate-500">No decision events with agent assignment.</p>
        ) : (
          <div className="space-y-1 min-w-[500px]">
            {agentOrder.map((key) => {
              const [, agentName] = key.split("::");
              const events = agentMap.get(key)!;
              return (
                <div key={key} className="flex items-center gap-2">
                  <span className="w-28 shrink-0 truncate text-right text-xs text-slate-500">{agentName}</span>
                  <div className="relative flex h-5 flex-1 items-center">
                    <div className="h-1 w-full rounded bg-slate-100" />
                    {events.map((ev) => {
                      const left = (ev.idx / Math.max(1, total)) * 100;
                      const isComplete =
                        ev.type === "evaluate" ||
                        ev.type.includes("TASK_COMPLETED") ||
                        ev.type === "assign";
                      return (
                        <div
                          key={ev.idx}
                          title={`[${ev.idx}] ${ev.type}${ev.task ? ` — ${ev.task}` : ""}`}
                          className="absolute h-3.5 rounded-sm"
                          style={{
                            left: `${left}%`,
                            width: `${pxPerDecision}px`,
                            backgroundColor: isComplete ? color : `${color}55`,
                            minWidth: "3px",
                          }}
                        />
                      );
                    })}
                  </div>
                </div>
              );
            })}
            <p className="mt-1 text-right text-xs text-slate-400">
              {total} decisions · {agentOrder.length} participants
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section H — Task Inspector
// ---------------------------------------------------------------------------

function TaskInspector({ cmp }: { cmp: CompareResponse }) {
  const convTasks = cmp.conventional_metrics?.tasks ?? {};
  const posdTasks = cmp.posdcorb_metrics?.tasks ?? {};
  const allIds = Array.from(new Set([...Object.keys(convTasks), ...Object.keys(posdTasks)])).sort();

  const [selected, setSelected] = useState<string>(allIds[0] ?? "");
  const ct = convTasks[selected];
  const pt = posdTasks[selected];

  const rows: { label: string; conv: string; posd: string }[] = [
    { label: "Status", conv: ct?.status ?? "—", posd: pt?.status ?? "—" },
    { label: "Assigned agent", conv: ct?.assigned_agent_id ?? "—", posd: pt?.assigned_agent_id ?? "—" },
    { label: "Queue time", conv: fmtMsOrDash(ct?.queue_time_ms), posd: fmtMsOrDash(pt?.queue_time_ms) },
    { label: "Execution time", conv: fmtMsOrDash(ct?.execution_time_ms), posd: fmtMsOrDash(pt?.execution_time_ms) },
    { label: "Total latency", conv: fmtMsOrDash(ct?.total_latency_ms), posd: fmtMsOrDash(pt?.total_latency_ms) },
    { label: "Retries", conv: fmt(ct?.retry_count, 0), posd: fmt(pt?.retry_count, 0) },
    { label: "Reassignments", conv: fmt(ct?.reassignment_count, 0), posd: fmt(pt?.reassignment_count, 0) },
  ];

  if (allIds.length === 0) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-4 text-xs text-slate-500">
        No per-task metrics available. Task trace requires decision records.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Task Inspector"
        description="Per-task metrics for a selected task, side by side for both managers."
        icon={Server}
      />
      <div className="flex items-center gap-2">
        <label className="text-xs font-medium text-slate-700">Task:</label>
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1 text-xs"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          {allIds.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
      </div>

      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-600">
            <tr>
              <th className="px-3 py-2 font-medium">Field</th>
              <th className="px-3 py-2 font-medium" style={{ color: MANAGER_COLORS.conventional }}>Conventional</th>
              <th className="px-3 py-2 font-medium" style={{ color: MANAGER_COLORS.posdcorb }}>POSDCORB</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r) => (
              <tr key={r.label} className="hover:bg-slate-50">
                <td className="px-3 py-1.5 text-xs font-medium text-slate-700">{r.label}</td>
                <td className="px-3 py-1.5 text-xs tabular-nums text-slate-600">{r.conv}</td>
                <td className="px-3 py-1.5 text-xs tabular-nums text-slate-600">{r.posd}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section I — Agent Inspector
// ---------------------------------------------------------------------------

function AgentInspector({ cmp }: { cmp: CompareResponse }) {
  const convAgents = cmp.conventional_metrics?.agents.per_agent ?? {};
  const posdAgents = cmp.posdcorb_metrics?.agents.per_agent ?? {};
  const allIds = Array.from(new Set([...Object.keys(convAgents), ...Object.keys(posdAgents)])).sort();

  const [selected, setSelected] = useState<string>(allIds[0] ?? "");
  const ca = convAgents[selected];
  const pa = posdAgents[selected];

  const rows: { label: string; conv: string; posd: string }[] = [
    { label: "Agent name", conv: ca?.agent_name ?? "—", posd: pa?.agent_name ?? "—" },
    { label: "Origin", conv: ca?.origin ?? "—", posd: pa?.origin ?? "—" },
    { label: "Tasks completed", conv: fmt(ca?.tasks_completed, 0), posd: fmt(pa?.tasks_completed, 0) },
    { label: "Tasks failed", conv: fmt(ca?.tasks_failed, 0), posd: fmt(pa?.tasks_failed, 0) },
    { label: "Busy time", conv: fmtMsOrDash(ca?.busy_time_ms), posd: fmtMsOrDash(pa?.busy_time_ms) },
    { label: "Available time", conv: fmtMsOrDash(ca?.available_time_ms), posd: fmtMsOrDash(pa?.available_time_ms) },
    { label: "Utilization", conv: ca ? fmtPctOrDash(ca.agent_utilization * 100) : "—", posd: pa ? fmtPctOrDash(pa.agent_utilization * 100) : "—" },
    { label: "Dynamically activated", conv: ca ? String(ca.is_activated) : "—", posd: pa ? String(pa.is_activated) : "—" },
  ];

  if (allIds.length === 0) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-4 text-xs text-slate-500">
        No per-agent metrics available.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Agent Inspector"
        description="Per-agent utilization and task outcomes for a selected agent."
        icon={Users}
      />
      <div className="flex items-center gap-2">
        <label className="text-xs font-medium text-slate-700">Agent:</label>
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1 text-xs"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          {allIds.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
      </div>

      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-600">
            <tr>
              <th className="px-3 py-2 font-medium">Field</th>
              <th className="px-3 py-2 font-medium" style={{ color: MANAGER_COLORS.conventional }}>Conventional</th>
              <th className="px-3 py-2 font-medium" style={{ color: MANAGER_COLORS.posdcorb }}>POSDCORB</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r) => (
              <tr key={r.label} className="hover:bg-slate-50">
                <td className="px-3 py-1.5 text-xs font-medium text-slate-700">{r.label}</td>
                <td className="px-3 py-1.5 text-xs tabular-nums text-slate-600">{r.conv}</td>
                <td className="px-3 py-1.5 text-xs tabular-nums text-slate-600">{r.posd}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section J — Decision Log
// ---------------------------------------------------------------------------

interface DecisionLogRow {
  idx: number;
  manager: string;
  action: string;
  task_id: string;
  agent: string;
  latency_ms: string;
  reason: string;
}

const DECISION_LOG_COLUMNS: Column<DecisionLogRow>[] = [
  {
    key: "idx",
    header: "#",
    align: "right",
    sortValue: (r) => r.idx,
    className: "w-10 text-slate-400",
  },
  {
    key: "manager",
    header: "Manager",
    render: (r) => (
      <span
        className="rounded px-1.5 py-0.5 text-xs font-medium text-white"
        style={{ backgroundColor: MANAGER_COLORS[r.manager as "conventional" | "posdcorb"] ?? "#94a3b8" }}
      >
        {r.manager === "conventional" ? "Conv" : "POSD"}
      </span>
    ),
  },
  {
    key: "action",
    header: "Action",
    className: "font-mono text-xs",
    sortValue: (r) => r.action,
  },
  {
    key: "task_id",
    header: "Task",
    className: "text-xs text-slate-500",
  },
  {
    key: "agent",
    header: "Agent",
    className: "text-xs text-slate-500",
  },
  {
    key: "latency_ms",
    header: "Latency",
    align: "right",
    sortValue: (r) => parseFloat(r.latency_ms),
    className: "text-xs tabular-nums text-slate-500",
  },
  {
    key: "reason",
    header: "Reason",
    className: "max-w-xs truncate text-xs text-slate-600",
  },
];

function DecisionLogSection({
  convDecisions,
  posdDecisions,
}: {
  convDecisions: DecisionRecord[];
  posdDecisions: DecisionRecord[];
}) {
  const PAGE = 50;
  const [page, setPage] = useState(0);

  const allRaw = [
    ...convDecisions.map((d) => ({ manager: "conventional", ...d })),
    ...posdDecisions.map((d) => ({ manager: "posdcorb", ...d })),
  ];

  const totalPages = Math.ceil(allRaw.length / PAGE);
  const pageSlice = allRaw.slice(page * PAGE, (page + 1) * PAGE);

  const rows: DecisionLogRow[] = pageSlice.map((d, i) => ({
    idx: page * PAGE + i + 1,
    manager: d.manager,
    action: d.manager_decision,
    task_id: d.task_id ?? "—",
    agent: d.selected_agent_name ?? d.selected_agent_id ?? "—",
    latency_ms: d.decision_latency_ms.toFixed(1),
    reason: d.reason,
  }));

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Decision Log"
        description={`All ${allRaw.length} decisions across both managers — paginated (${PAGE}/page).`}
        icon={BookOpen}
        actions={
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span>{page * PAGE + 1}–{Math.min((page + 1) * PAGE, allRaw.length)} of {allRaw.length}</span>
            <button
              className="rounded border border-slate-300 px-2 py-0.5 disabled:opacity-40"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              ←
            </button>
            <button
              className="rounded border border-slate-300 px-2 py-0.5 disabled:opacity-40"
              disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              →
            </button>
          </div>
        }
      />
      <DataTable
        columns={DECISION_LOG_COLUMNS}
        rows={rows}
        rowKey={(r) => r.idx}
        emptyMessage="No decision events recorded."
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section K — Full metric comparison table (all 27 rows)
// ---------------------------------------------------------------------------

function ComparisonRowTable({
  rows,
  title,
}: {
  rows: ComparisonRow[];
  title?: string;
}) {
  if (rows.length === 0) return null;

  const columns: Column<ComparisonRow>[] = [
    {
      key: "metric",
      header: "Metric",
      className: "font-mono text-xs",
      sortValue: (r) => r.metric,
    },
    {
      key: "conventional_value",
      header: "Conventional",
      align: "right",
      sortValue: (r) => r.conventional_value,
      render: (r) => <span className="tabular-nums text-xs">{r.conventional_value.toFixed(3)}</span>,
    },
    {
      key: "posdcorb_value",
      header: "POSDCORB",
      align: "right",
      sortValue: (r) => r.posdcorb_value,
      render: (r) => <span className="tabular-nums text-xs">{r.posdcorb_value.toFixed(3)}</span>,
    },
    {
      key: "pct_difference",
      header: "Δ%",
      align: "right",
      sortValue: (r) => r.pct_difference,
      render: (r) => <DeltaBadge pct={r.pct_difference} baseline={r.conventional_value} />,
    },
    {
      key: "better_manager",
      header: "Favours",
      render: (r) => <BetterBadge better={r.better_manager} />,
    },
    {
      key: "interpretation",
      header: "Interpretation",
      className: "max-w-xs truncate text-xs text-slate-500",
    },
  ];

  return (
    <details className="group">
      <summary className="cursor-pointer list-none rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-100">
        {title ?? "Metric details"} ({rows.length} rows) <span className="ml-1 text-slate-400 group-open:hidden">▶</span><span className="ml-1 text-slate-400 hidden group-open:inline">▼</span>
      </summary>
      <div className="mt-1">
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.metric}
          emptyMessage="No rows."
        />
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// All-metrics table
// ---------------------------------------------------------------------------

function AllMetricsSection({ cmp }: { cmp: CompareResponse }) {
  return (
    <div className="space-y-3">
      <SectionHeader
        title="All Metrics"
        description="Complete comparison table — all 27 metrics side by side."
        icon={BarChart2}
      />
      <DataTable
        columns={[
          { key: "metric", header: "Metric", className: "font-mono text-xs", sortValue: (r: ComparisonRow) => r.metric },
          {
            key: "conventional_value",
            header: "Conventional",
            align: "right",
            sortValue: (r: ComparisonRow) => r.conventional_value,
            render: (r: ComparisonRow) => <span className="tabular-nums text-xs">{r.conventional_value.toFixed(4)}</span>,
          },
          {
            key: "posdcorb_value",
            header: "POSDCORB",
            align: "right",
            sortValue: (r: ComparisonRow) => r.posdcorb_value,
            render: (r: ComparisonRow) => <span className="tabular-nums text-xs">{r.posdcorb_value.toFixed(4)}</span>,
          },
          {
            key: "pct_difference",
            header: "Δ%",
            align: "right",
            sortValue: (r: ComparisonRow) => r.pct_difference,
            render: (r: ComparisonRow) => <DeltaBadge pct={r.pct_difference} baseline={r.conventional_value} />,
          },
          {
            key: "better_manager",
            header: "Favours",
            render: (r: ComparisonRow) => <BetterBadge better={r.better_manager} />,
          },
          {
            key: "higher_is_better",
            header: "Higher=Better",
            render: (r: ComparisonRow) => (
              <span className="text-xs text-slate-500">
                {r.higher_is_better === null
                  ? "Context"
                  : r.higher_is_better
                    ? "Yes"
                    : "No"}
              </span>
            ),
          },
          {
            key: "interpretation",
            header: "Interpretation",
            className: "max-w-xs truncate text-xs text-slate-500",
          },
        ]}
        rows={cmp.rows}
        rowKey={(r) => r.metric}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Config form
// ---------------------------------------------------------------------------

interface RunConfig {
  numTasks: number;
  complexity: "low" | "medium" | "high";
  depPct: number;
  seed: number;
  maxDecisions: number;
  maxWallMs: number;
  failureProb: number;
  maxAgents: number;
}

function ConfigForm({
  config,
  onChange,
  disabled,
}: {
  config: RunConfig;
  onChange: (c: RunConfig) => void;
  disabled: boolean;
}) {
  const field = (label: string, key: keyof RunConfig, type: "number" | "select", opts?: string[]) => (
    <div>
      <label className="block text-xs font-medium text-slate-700 mb-0.5">{label}</label>
      {type === "select" ? (
        <select
          disabled={disabled}
          className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs disabled:opacity-60"
          value={config[key] as string}
          onChange={(e) => onChange({ ...config, [key]: e.target.value })}
        >
          {opts!.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : (
        <input
          type="number"
          disabled={disabled}
          className="w-full rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-60"
          value={config[key] as number}
          onChange={(e) => onChange({ ...config, [key]: Number(e.target.value) })}
        />
      )}
    </div>
  );

  return (
    <div className="rounded-md border border-slate-200 bg-white p-3">
      <p className="mb-3 text-xs font-semibold text-slate-700">Experiment Configuration</p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
        {field("Tasks", "numTasks", "number")}
        {field("Complexity", "complexity", "select", ["low", "medium", "high"])}
        {field("Dep %", "depPct", "number")}
        {field("RNG seed", "seed", "number")}
        {field("Max decisions", "maxDecisions", "number")}
        {field("Max wall ms", "maxWallMs", "number")}
        {field("Failure prob", "failureProb", "number")}
        {field("Max agents", "maxAgents", "number")}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page state type
// ---------------------------------------------------------------------------

type PageStatus = "idle" | "running" | "done" | "error";

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type TabId =
  | "summary"
  | "performance"
  | "agents"
  | "management"
  | "staffing"
  | "reliability"
  | "timeline"
  | "tasks"
  | "agentinspect"
  | "log"
  | "all";

const TABS: { id: TabId; label: string }[] = [
  { id: "summary", label: "Summary" },
  { id: "performance", label: "Performance" },
  { id: "agents", label: "Agents" },
  { id: "management", label: "Management" },
  { id: "staffing", label: "Staffing" },
  { id: "reliability", label: "Reliability" },
  { id: "timeline", label: "Timeline" },
  { id: "tasks", label: "Tasks" },
  { id: "agentinspect", label: "Agent Detail" },
  { id: "log", label: "Decision Log" },
  { id: "all", label: "All Metrics" },
];

export default function ComparisonPage() {
  const [status, setStatus] = useState<PageStatus>("idle");
  const [simResponse, setSimResponse] = useState<SimulateResponse | null>(null);
  const [cmpResponse, setCmpResponse] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("summary");

  const [config, setConfig] = useState<RunConfig>({
    numTasks: DEFAULT_WORKLOAD.num_tasks,
    complexity: DEFAULT_WORKLOAD.complexity,
    depPct: DEFAULT_WORKLOAD.dependency_percentage,
    seed: DEFAULT_WORKLOAD.seed,
    maxDecisions: 10_000,
    maxWallMs: 300_000,
    failureProb: 0,
    maxAgents: 8,
  });

  const isRunning = status === "running";

  async function handleRun() {
    setStatus("running");
    setError(null);
    setSimResponse(null);
    setCmpResponse(null);

    try {
      // Step 1: simulate both managers
      const sim = await api.simulate({
        workload_config: {
          num_tasks: config.numTasks,
          complexity: config.complexity,
          dependency_percentage: config.depPct,
          seed: config.seed,
        },
        manager_types: ["conventional", "posdcorb"],
        rng_seed: config.seed,
        max_decisions: config.maxDecisions,
        max_wall_ms: config.maxWallMs,
        failure_probability: config.failureProb > 0 ? config.failureProb : null,
        max_agents: config.maxAgents > 0 ? config.maxAgents : null,
      });
      setSimResponse(sim);

      // Validate both managers ran
      if (!sim.experiments["conventional"] || !sim.experiments["posdcorb"]) {
        const missing = Object.entries(sim.errors)
          .map(([m, e]) => `${m}: ${e}`)
          .join("; ");
        throw new Error(
          `Both managers must run successfully. Errors: ${missing || "unknown"}`
        );
      }

      // Step 2: compare metrics
      // The experiments dict uses summary_dict() which has total_duration_ms (not total_wall_ms).
      // The backend _build_experiment_result helper reads total_duration_ms.
      // We pass the experiments dict directly — it already has the right shape.
      const cmp = await api.compareMetrics({
        experiments: {
          conventional: sim.experiments["conventional"] as unknown as Record<string, unknown>,
          posdcorb: sim.experiments["posdcorb"] as unknown as Record<string, unknown>,
        },
        trace_decisions: {
          conventional: (sim.results["conventional"]?.decisions ?? []) as unknown[],
          posdcorb: (sim.results["posdcorb"]?.decisions ?? []) as unknown[],
        },
      });
      setCmpResponse(cmp);
      setStatus("done");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus("error");
    }
  }

  function handleReset() {
    setStatus("idle");
    setSimResponse(null);
    setCmpResponse(null);
    setError(null);
    setActiveTab("summary");
  }

  const convDecisions: DecisionRecord[] = simResponse?.results["conventional"]?.decisions ?? [];
  const posdDecisions: DecisionRecord[] = simResponse?.results["posdcorb"]?.decisions ?? [];

  return (
    <>
      {/* Page header */}
      <SectionHeader
        title="Research Comparison Dashboard"
        description="Conventional Manager vs POSDCORB Adaptive Manager — same workload, same agents, same seed."
        icon={FlaskConical}
        actions={
          <>
            <button className={btn} onClick={handleRun} disabled={isRunning}>
              {isRunning ? (
                <>
                  <Activity className="h-4 w-4 animate-pulse" /> Running…
                </>
              ) : (
                <>
                  <FlaskConical className="h-4 w-4" /> Run Comparison
                </>
              )}
            </button>
            <button className={btn} onClick={handleReset} disabled={isRunning}>
              <RotateCcw className="h-4 w-4" /> Reset
            </button>
          </>
        }
      />

      {/* Config form */}
      <ConfigForm config={config} onChange={setConfig} disabled={isRunning} />

      {/* Status banners */}
      {status === "idle" && (
        <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          Configure the experiment above then click <strong>Run Comparison</strong> to simulate both managers.
        </div>
      )}
      {isRunning && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <span className="animate-pulse">●</span>&nbsp; Simulating both managers… this may take several seconds.
        </div>
      )}
      {status === "error" && error && (
        <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div><strong>Error:</strong> {error}</div>
        </div>
      )}
      {status === "done" && simResponse && (
        <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-800">
          Comparison complete — {simResponse.real_elapsed_s.toFixed(2)}s elapsed. Graph: {simResponse.graph_id}, seed: {simResponse.seed}.
        </div>
      )}

      {/* Results — only shown when done */}
      {status === "done" && simResponse && cmpResponse && (
        <div className="space-y-4">
          <Tabs items={TABS} active={activeTab} onChange={setActiveTab} />

          {activeTab === "summary" && (
            <SummarySectionA sim={simResponse} cmp={cmpResponse} />
          )}
          {activeTab === "performance" && (
            <PerformanceSection cmp={cmpResponse} />
          )}
          {activeTab === "agents" && (
            <ResourceSection cmp={cmpResponse} />
          )}
          {activeTab === "management" && (
            <ManagementSection cmp={cmpResponse} />
          )}
          {activeTab === "staffing" && (
            <StaffingSection cmp={cmpResponse} sim={simResponse} />
          )}
          {activeTab === "reliability" && (
            <ReliabilitySection cmp={cmpResponse} />
          )}
          {activeTab === "timeline" && (
            <TimelineSection
              convDecisions={convDecisions}
              posdDecisions={posdDecisions}
            />
          )}
          {activeTab === "tasks" && (
            <TaskInspector cmp={cmpResponse} />
          )}
          {activeTab === "agentinspect" && (
            <AgentInspector cmp={cmpResponse} />
          )}
          {activeTab === "log" && (
            <DecisionLogSection
              convDecisions={convDecisions}
              posdDecisions={posdDecisions}
            />
          )}
          {activeTab === "all" && (
            <AllMetricsSection cmp={cmpResponse} />
          )}
        </div>
      )}
    </>
  );
}
