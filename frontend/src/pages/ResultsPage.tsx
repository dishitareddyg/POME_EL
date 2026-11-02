import { BarChart3 } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_TICK, GRID_STROKE, LEGEND_STYLE, TOOLTIP_STYLE } from "../components/chartStyle";
import ChartCard from "../components/ChartCard";
import ComparisonCard from "../components/ComparisonCard";
import DataTable, { type Column } from "../components/DataTable";
import SectionHeader from "../components/SectionHeader";
import Tabs from "../components/Tabs";
import { MANAGER_COLORS, MANAGER_LABELS } from "../data/managers";
import { MOCK_EXPERIMENTS } from "../data/mock";
import { completedOnly, type CompletedExperiment } from "../services/selectors";

type Tab = "overview" | "latency" | "resources" | "data";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "latency", label: "Latency" },
  { id: "resources", label: "Resources" },
  { id: "data", label: "Data table" },
];

const completed = completedOnly(MOCK_EXPERIMENTS).sort((a, b) => a.id - b.id);

const tableColumns: Column<CompletedExperiment>[] = [
  { key: "id", header: "ID", sortValue: (e) => e.id, render: (e) => `#${e.id}` },
  { key: "cl", header: "Conv. latency (ms)", align: "right", sortValue: (e) => e.results.conventional.avg_latency_ms, render: (e) => e.results.conventional.avg_latency_ms },
  { key: "pl", header: "POS latency (ms)", align: "right", sortValue: (e) => e.results.posdcorb.avg_latency_ms, render: (e) => e.results.posdcorb.avg_latency_ms },
  { key: "ct", header: "Conv. tput", align: "right", sortValue: (e) => e.results.conventional.throughput_tps, render: (e) => e.results.conventional.throughput_tps.toFixed(1) },
  { key: "pt", header: "POS tput", align: "right", sortValue: (e) => e.results.posdcorb.throughput_tps, render: (e) => e.results.posdcorb.throughput_tps.toFixed(1) },
  { key: "cs", header: "Conv. success %", align: "right", sortValue: (e) => e.results.conventional.success_rate, render: (e) => e.results.conventional.success_rate.toFixed(1) },
  { key: "ps", header: "POS success %", align: "right", sortValue: (e) => e.results.posdcorb.success_rate, render: (e) => e.results.posdcorb.success_rate.toFixed(1) },
];

const series = (get: (e: CompletedExperiment, m: "conventional" | "posdcorb") => number) =>
  completed.map((e) => ({ name: `#${e.id}`, conventional: get(e, "conventional"), posdcorb: get(e, "posdcorb") }));

function LinesChart({ data, unit }: { data: ReturnType<typeof series>; unit: string }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={GRID_STROKE} vertical={false} />
        <XAxis dataKey="name" tick={AXIS_TICK} tickLine={false} />
        <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={44} unit={unit} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Legend wrapperStyle={LEGEND_STYLE} />
        <Line type="monotone" dataKey="conventional" name={MANAGER_LABELS.conventional} stroke={MANAGER_COLORS.conventional} strokeWidth={2} dot={{ r: 2 }} />
        <Line type="monotone" dataKey="posdcorb" name={MANAGER_LABELS.posdcorb} stroke={MANAGER_COLORS.posdcorb} strokeWidth={2} dot={{ r: 2 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export default function ResultsPage({ initialId }: { initialId?: number }) {
  const fallback = completed[completed.length - 1];
  const [selected, setSelected] = useState<number>(completed.some((e) => e.id === initialId) ? (initialId as number) : fallback.id);
  const [tab, setTab] = useState<Tab>("overview");
  const e = completed.find((x) => x.id === selected) ?? fallback;
  const c = e.results.conventional;
  const p = e.results.posdcorb;

  const latencyBars = [
    { metric: "Average", conventional: c.avg_latency_ms, posdcorb: p.avg_latency_ms },
    { metric: "P95", conventional: c.p95_latency_ms, posdcorb: p.p95_latency_ms },
  ];

  return (
    <>
      <SectionHeader
        title="Results"
        description={`${e.name} · ${e.num_workers} workers · ${e.num_tasks} tasks · seed ${e.random_seed}`}
        icon={BarChart3}
        actions={
          <select
            value={selected}
            onChange={(ev) => setSelected(Number(ev.target.value))}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-sm"
            aria-label="Select experiment"
          >
            {[...completed].reverse().map((x) => (
              <option key={x.id} value={x.id}>#{x.id} {x.name}</option>
            ))}
          </select>
        }
      />
      <Tabs items={TABS} active={tab} onChange={setTab} />

      {tab === "overview" && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <ComparisonCard label="Average latency" unit="ms" conventional={c.avg_latency_ms} posdcorb={p.avg_latency_ms} lowerIsBetter decimals={0} />
          <ComparisonCard label="P95 latency" unit="ms" conventional={c.p95_latency_ms} posdcorb={p.p95_latency_ms} lowerIsBetter decimals={0} />
          <ComparisonCard label="Throughput" unit="tasks/s" conventional={c.throughput_tps} posdcorb={p.throughput_tps} />
          <ComparisonCard label="Success rate" unit="%" conventional={c.success_rate} posdcorb={p.success_rate} />
          <ComparisonCard label="CPU utilisation" unit="%" conventional={c.cpu_pct} posdcorb={p.cpu_pct} lowerIsBetter decimals={0} />
          <ComparisonCard label="Memory" unit="MB" conventional={c.memory_mb} posdcorb={p.memory_mb} lowerIsBetter decimals={0} />
        </div>
      )}

      {tab === "latency" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard title={`Latency, experiment #${e.id}`} subtitle="ms (lower is better)">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={latencyBars} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={GRID_STROKE} vertical={false} />
                <XAxis dataKey="metric" tick={AXIS_TICK} tickLine={false} />
                <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={44} />
                <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f1f5f9" }} />
                <Legend wrapperStyle={LEGEND_STYLE} />
                <Bar dataKey="conventional" name={MANAGER_LABELS.conventional} fill={MANAGER_COLORS.conventional} radius={[2, 2, 0, 0]} />
                <Bar dataKey="posdcorb" name={MANAGER_LABELS.posdcorb} fill={MANAGER_COLORS.posdcorb} radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
          <ChartCard title="Average latency across experiments" subtitle="ms">
            <LinesChart data={series((x, m) => x.results[m].avg_latency_ms)} unit="" />
          </ChartCard>
        </div>
      )}

      {tab === "resources" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard title="CPU utilisation across experiments" subtitle="%">
            <LinesChart data={series((x, m) => x.results[m].cpu_pct)} unit="%" />
          </ChartCard>
          <ChartCard title="Memory across experiments" subtitle="MB">
            <LinesChart data={series((x, m) => x.results[m].memory_mb)} unit="" />
          </ChartCard>
        </div>
      )}

      {tab === "data" && <DataTable columns={tableColumns} rows={completed} rowKey={(x) => x.id} />}
    </>
  );
}
