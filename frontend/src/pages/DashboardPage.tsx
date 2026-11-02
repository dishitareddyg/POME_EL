import { Activity, CheckCircle2, Clock, FlaskConical, Gauge, RefreshCw, Timer } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_TICK, GRID_STROKE, LEGEND_STYLE, TOOLTIP_STYLE } from "../components/chartStyle";
import ChartCard from "../components/ChartCard";
import ComparisonCard from "../components/ComparisonCard";
import DataTable, { type Column } from "../components/DataTable";
import MetricCard from "../components/MetricCard";
import SectionHeader from "../components/SectionHeader";
import StatusBadge from "../components/StatusBadge";
import { MANAGER_COLORS, MANAGER_LABELS } from "../data/managers";
import { MOCK_EXPERIMENTS } from "../data/mock";
import { useHealth } from "../hooks/useHealth";
import { fmtDate, fmtMs, fmtNum } from "../services/format";
import { completedOnly, latestExperiment, managerMean, mean } from "../services/selectors";
import type { DashboardExperimentRecord } from "../types/experiment";

const recentColumns: Column<DashboardExperimentRecord>[] = [
  { key: "id", header: "ID", render: (r) => `#${r.id}`, sortValue: (r) => r.id },
  { key: "name", header: "Name", sortValue: (r) => r.name, className: "font-sans text-slate-900" },
  { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> },
  { key: "workers", header: "Workers", align: "right", render: (r) => r.num_workers },
  { key: "tasks", header: "Tasks", align: "right", render: (r) => r.num_tasks },
  {
    key: "conv",
    header: "Conv. latency",
    align: "right",
    render: (r) => (r.results ? fmtMs(r.results.conventional.avg_latency_ms) : "—"),
  },
  {
    key: "pos",
    header: "POSDCORB latency",
    align: "right",
    render: (r) => (r.results ? fmtMs(r.results.posdcorb.avg_latency_ms) : "—"),
  },
  { key: "created", header: "Created", render: (r) => fmtDate(r.created_at) },
];

export default function DashboardPage() {
  const { data, error, loading, refresh } = useHealth();

  const completed = completedOnly(MOCK_EXPERIMENTS);
  const latest = latestExperiment(MOCK_EXPERIMENTS);
  const convLat = managerMean(completed, "conventional", "avg_latency_ms");
  const posLat = managerMean(completed, "posdcorb", "avg_latency_ms");
  const convTp = managerMean(completed, "conventional", "throughput_tps");
  const posTp = managerMean(completed, "posdcorb", "throughput_tps");
  const convSr = managerMean(completed, "conventional", "success_rate");
  const posSr = managerMean(completed, "posdcorb", "success_rate");

  const chartData = [...completed]
    .sort((a, b) => a.id - b.id)
    .slice(-8)
    .map((e) => ({
      name: `#${e.id}`,
      conventional: Math.round(e.results.conventional.avg_latency_ms),
      posdcorb: Math.round(e.results.posdcorb.avg_latency_ms),
    }));

  const recent = [...MOCK_EXPERIMENTS].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5);

  return (
    <>
      <SectionHeader
        title="Overview"
        description={`Aggregates over ${completed.length} completed experiments. Workers, tasks and hardware are identical across both managers.`}
        icon={Activity}
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricCard label="Total experiments" value={MOCK_EXPERIMENTS.length} icon={FlaskConical} hint={`${completed.length} completed`} />
        <MetricCard
          label="Latest experiment"
          value={`#${latest.id}`}
          icon={Clock}
          hint={
            <span className="inline-flex items-center gap-1">
              <StatusBadge status={latest.status} /> {fmtDate(latest.created_at)}
            </span>
          }
        />
        <MetricCard label="Conventional avg latency" value={Math.round(convLat).toLocaleString("en-US")} unit="ms" icon={Timer} accent={MANAGER_COLORS.conventional} />
        <MetricCard label="POSDCORB avg latency" value={Math.round(posLat).toLocaleString("en-US")} unit="ms" icon={Timer} accent={MANAGER_COLORS.posdcorb} />
        <MetricCard
          label="Average throughput"
          value={fmtNum(mean([convTp, posTp]))}
          unit="tasks/s"
          icon={Gauge}
          hint={`Conv ${fmtNum(convTp)} · POS ${fmtNum(posTp)}`}
        />
        <MetricCard
          label="Success rate"
          value={fmtNum(mean([convSr, posSr]))}
          unit="%"
          icon={CheckCircle2}
          hint={`Conv ${fmtNum(convSr)} · POS ${fmtNum(posSr)}`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <ChartCard title="Average latency by experiment" subtitle="Last 8 completed experiments, ms (lower is better)" height={260}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={GRID_STROKE} vertical={false} />
                <XAxis dataKey="name" tick={AXIS_TICK} tickLine={false} />
                <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={44} />
                <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f1f5f9" }} />
                <Legend wrapperStyle={LEGEND_STYLE} />
                <Bar dataKey="conventional" name={MANAGER_LABELS.conventional} fill={MANAGER_COLORS.conventional} radius={[2, 2, 0, 0]} />
                <Bar dataKey="posdcorb" name={MANAGER_LABELS.posdcorb} fill={MANAGER_COLORS.posdcorb} radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
        <div className="grid content-start gap-3">
          <ComparisonCard label="Avg latency" unit="ms" conventional={convLat} posdcorb={posLat} lowerIsBetter decimals={0} />
          <ComparisonCard label="Avg throughput" unit="tasks/s" conventional={convTp} posdcorb={posTp} />
          <ComparisonCard label="Success rate" unit="%" conventional={convSr} posdcorb={posSr} />
        </div>
      </div>

      <div className="space-y-2">
        <SectionHeader
          title="Recent experiments"
          actions={
            <a href="#/history" className="text-xs text-indigo-700 hover:underline">
              View all
            </a>
          }
        />
        <DataTable columns={recentColumns} rows={recent} rowKey={(r) => r.id} />
      </div>

      <section className="rounded-md border border-slate-200 bg-white p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">Backend status</h3>
          <button
            onClick={() => void refresh()}
            disabled={loading}
            className="inline-flex items-center gap-1 rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100 disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} /> Refresh
          </button>
        </div>
        {error && <p className="text-sm text-red-700">Cannot reach the backend: {error}. Is it running on port 8000?</p>}
        {data && (
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs text-slate-500">API</dt>
              <dd><StatusBadge ok label={data.status} /></dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">Database</dt>
              <dd><StatusBadge ok={data.database === "ok"} label={data.database} /></dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">Python</dt>
              <dd>{data.python_version}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">Host</dt>
              <dd>{data.cpu_count} CPUs, {data.memory_total_mb} MB RAM</dd>
            </div>
          </dl>
        )}
      </section>
    </>
  );
}
