/**
 * AgentPoolPage — full Agent Pool module.
 *
 * Sections:
 *  1. Summary metric cards
 *  2. Agent Status Cards (per-agent live snapshot)
 *  3. CPU Utilization bar chart
 *  4. Agent table (sortable, with inline actions)
 *
 * State is managed with useState using MOCK_AGENTS as the initial value.
 * The data shape exactly matches the Agent interface / backend model,
 * so replacing useState with a real API call requires only swapping the
 * initialiser and the mutation functions.
 *
 * Origin distinction:
 *   - "initial"  → fixed pool; available to BOTH managers from t=0.
 *   - "dynamic"  → POSDCORB Staffing reserve; Conventional Manager ignores these.
 */

import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Cpu,
  Gauge,
  Layers,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  Users,
  XCircle,
  Zap,
} from "lucide-react";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AXIS_TICK, GRID_STROKE, TOOLTIP_STYLE } from "../components/chartStyle";
import ChartCard from "../components/ChartCard";
import MetricCard from "../components/MetricCard";
import SectionHeader from "../components/SectionHeader";
import AgentModal from "../components/AgentModal";
import { MOCK_AGENTS } from "../data/agents";
import type { Agent, AgentOrigin, AgentStatus } from "../types/agent";

// ─── Status helpers ──────────────────────────────────────────────────────────



const STATUS_BG: Record<AgentStatus, string> = {
  idle: "bg-slate-100 text-slate-600",
  running: "bg-indigo-50 text-indigo-700",
  waiting: "bg-amber-50 text-amber-700",
  failed: "bg-red-50 text-red-700",
  completed: "bg-emerald-50 text-emerald-700",
};

const STATUS_DOT: Record<AgentStatus, string> = {
  idle: "bg-slate-400",
  running: "bg-indigo-500 animate-pulse",
  waiting: "bg-amber-400",
  failed: "bg-red-500",
  completed: "bg-emerald-500",
};

const ORIGIN_BADGE: Record<AgentOrigin, string> = {
  initial: "bg-blue-50 text-blue-700 ring-blue-200",
  dynamic: "bg-purple-50 text-purple-700 ring-purple-200",
};

function StatusPill({ status }: { status: AgentStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BG[status]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[status]}`} />
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

function OriginBadge({ origin }: { origin: AgentOrigin }) {
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset capitalize ${ORIGIN_BADGE[origin]}`}>
      {origin}
    </span>
  );
}

// ─── Capability tag strip ────────────────────────────────────────────────────

function CapabilityTags({ caps, max = 3 }: { caps: string[]; max?: number }) {
  const visible = caps.slice(0, max);
  const hidden = caps.length - max;
  return (
    <div className="flex flex-wrap gap-1">
      {visible.map((c) => (
        <span
          key={c}
          className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600"
        >
          {c}
        </span>
      ))}
      {hidden > 0 && (
        <span className="rounded-full bg-slate-200 px-1.5 py-0.5 text-[10px] text-slate-500">
          +{hidden}
        </span>
      )}
    </div>
  );
}

// ─── Workload mini-bar ───────────────────────────────────────────────────────

function WorkloadBar({ current, max }: { current: number; max: number }) {
  const pct = max > 0 ? Math.min((current / max) * 100, 100) : 0;
  const color = pct >= 90 ? "bg-red-500" : pct >= 60 ? "bg-amber-400" : "bg-emerald-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 rounded-full bg-slate-200">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums text-xs text-slate-500">
        {current}/{max}
      </span>
    </div>
  );
}

// ─── CPU gauge ───────────────────────────────────────────────────────────────

function CpuBar({ pct }: { pct: number }) {
  const color = pct >= 85 ? "bg-red-500" : pct >= 60 ? "bg-amber-400" : "bg-indigo-500";
  return (
    <div className="flex items-center gap-2 min-w-[80px]">
      <div className="h-1.5 flex-1 rounded-full bg-slate-200">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right tabular-nums text-xs text-slate-500">{pct}%</span>
    </div>
  );
}

// ─── Agent Status Card ───────────────────────────────────────────────────────

function AgentStatusCard({ agent, onEdit, onRemove }: { agent: Agent; onEdit: () => void; onRemove: () => void }) {
  const successRate =
    agent.tasks_completed + agent.tasks_failed > 0
      ? ((agent.tasks_completed / (agent.tasks_completed + agent.tasks_failed)) * 100).toFixed(1)
      : "—";

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3.5 shadow-sm hover:shadow-md transition-shadow">
      {/* Top row */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-semibold text-slate-900">{agent.name}</span>
            <OriginBadge origin={agent.origin} />
          </div>
          <div className="mt-0.5 text-[11px] text-slate-400">{agent.id}</div>
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            onClick={onEdit}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition"
            title="Edit agent"
            aria-label={`Edit ${agent.name}`}
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={onRemove}
            className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600 transition"
            title="Remove agent"
            aria-label={`Remove ${agent.name}`}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Role + Status */}
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-xs text-slate-500">{agent.role}</span>
        <StatusPill status={agent.status} />
      </div>

      {/* CPU */}
      <div className="mt-2">
        <div className="mb-0.5 text-[10px] text-slate-400">CPU</div>
        <CpuBar pct={agent.cpu_usage} />
      </div>

      {/* Workload */}
      <div className="mt-2">
        <div className="mb-0.5 text-[10px] text-slate-400">Workload</div>
        <WorkloadBar current={agent.current_workload} max={agent.max_concurrent_tasks} />
      </div>

      {/* Stats row */}
      <div className="mt-3 grid grid-cols-3 gap-1 rounded-md bg-slate-50 p-2 text-center">
        <div>
          <div className="text-[10px] text-slate-400">Completed</div>
          <div className="text-xs font-semibold text-emerald-600">{agent.tasks_completed}</div>
        </div>
        <div>
          <div className="text-[10px] text-slate-400">Failed</div>
          <div className="text-xs font-semibold text-red-500">{agent.tasks_failed}</div>
        </div>
        <div>
          <div className="text-[10px] text-slate-400">Success</div>
          <div className="text-xs font-semibold text-slate-700">{successRate}{successRate !== "—" ? "%" : ""}</div>
        </div>
      </div>

      {/* Capabilities */}
      <div className="mt-2">
        <CapabilityTags caps={agent.capabilities} />
      </div>

      {/* Speed + Reliability row */}
      <div className="mt-2 flex gap-3">
        <div className="flex items-center gap-1 text-[10px] text-slate-400">
          <Zap className="h-3 w-3" />
          {agent.processing_speed.toFixed(2)}&times;
        </div>
        <div className="flex items-center gap-1 text-[10px] text-slate-400">
          <ShieldCheck className="h-3 w-3" />
          {(agent.reliability * 100).toFixed(0)}% reliable
        </div>
      </div>
    </div>
  );
}

// ─── Sort helpers ────────────────────────────────────────────────────────────

type SortKey = "name" | "role" | "status" | "cpu_usage" | "current_workload" | "reliability" | "tasks_completed" | "tasks_failed" | "processing_speed" | "origin";

function sortAgents(agents: Agent[], key: SortKey, dir: 1 | -1): Agent[] {
  return [...agents].sort((a, b) => {
    const va = a[key];
    const vb = b[key];
    return (va < vb ? -1 : va > vb ? 1 : 0) * dir;
  });
}

// ─── Unique ID generator (mock; backend will generate real UUIDs) ─────────────

let _uidCounter = 100;
const genId = () => `agt-${String(++_uidCounter).padStart(3, "0")}`;

// ─── Page ────────────────────────────────────────────────────────────────────

type OriginFilter = "all" | "initial" | "dynamic";
type StatusFilter = "all" | AgentStatus;
type ViewMode = "cards" | "table";

export default function AgentPoolPage() {
  const [agents, setAgents] = useState<Agent[]>(MOCK_AGENTS);

  // UI state
  const [viewMode, setViewMode] = useState<ViewMode>("cards");
  const [originFilter, setOriginFilter] = useState<OriginFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<1 | -1>(1);

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | undefined>(undefined);

  // Delete confirm
  const [deleteId, setDeleteId] = useState<string | null>(null);

  // ── Derived data ──────────────────────────────────────────────────────────

  const initialAgents = useMemo(() => agents.filter((a) => a.origin === "initial"), [agents]);
  const dynamicAgents = useMemo(() => agents.filter((a) => a.origin === "dynamic"), [agents]);

  const filteredAgents = useMemo(() => {
    let result = agents;
    if (originFilter !== "all") result = result.filter((a) => a.origin === originFilter);
    if (statusFilter !== "all") result = result.filter((a) => a.status === statusFilter);
    return sortAgents(result, sortKey, sortDir);
  }, [agents, originFilter, statusFilter, sortKey, sortDir]);

  const avgCpu = useMemo(() =>
    agents.length ? Math.round(agents.reduce((s, a) => s + a.cpu_usage, 0) / agents.length) : 0,
    [agents],
  );
  const avgReliability = useMemo(() =>
    agents.length ? (agents.reduce((s, a) => s + a.reliability, 0) / agents.length) * 100 : 0,
    [agents],
  );
  const totalConcurrency = useMemo(() => agents.reduce((s, a) => s + a.max_concurrent_tasks, 0), [agents]);
  const totalCompleted = useMemo(() => agents.reduce((s, a) => s + a.tasks_completed, 0), [agents]);

  const statusCounts = useMemo(() => {
    const counts: Record<AgentStatus, number> = { idle: 0, running: 0, waiting: 0, failed: 0, completed: 0 };
    agents.forEach((a) => counts[a.status]++);
    return counts;
  }, [agents]);

  const chartData = useMemo(() =>
    [...agents]
      .sort((a, b) => b.cpu_usage - a.cpu_usage)
      .map((a) => ({
        name: a.name,
        cpu: a.cpu_usage,
        workload_pct: a.max_concurrent_tasks > 0
          ? Math.round((a.current_workload / a.max_concurrent_tasks) * 100)
          : 0,
        origin: a.origin,
      })),
    [agents],
  );

  // ── Actions ───────────────────────────────────────────────────────────────

  const openAdd = () => { setEditingAgent(undefined); setModalOpen(true); };
  const openEdit = (agent: Agent) => { setEditingAgent(agent); setModalOpen(true); };
  const closeModal = () => { setModalOpen(false); setEditingAgent(undefined); };

  const handleSave = (payload: Omit<Agent, "id" | "current_workload" | "status" | "cpu_usage" | "tasks_completed" | "tasks_failed" | "created_at">) => {
    if (editingAgent) {
      // Edit
      setAgents((prev) => prev.map((a) => a.id === editingAgent.id ? { ...a, ...payload } : a));
    } else {
      // Add
      const newAgent: Agent = {
        id: genId(),
        current_workload: 0,
        status: "idle",
        cpu_usage: 0,
        tasks_completed: 0,
        tasks_failed: 0,
        created_at: new Date().toISOString(),
        ...payload,
      };
      setAgents((prev) => [...prev, newAgent]);
    }
    closeModal();
  };

  const confirmDelete = (id: string) => setDeleteId(id);
  const handleDelete = () => {
    if (deleteId) {
      setAgents((prev) => prev.filter((a) => a.id !== deleteId));
      setDeleteId(null);
    }
  };

  // ── Table column sort toggle ──────────────────────────────────────────────

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 1 ? -1 : 1));
    else { setSortKey(key); setSortDir(1); }
  };

  const SortIcon = ({ col }: { col: SortKey }) =>
    sortKey === col
      ? sortDir === 1 ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />
      : null;

  // ─────────────────────────────────────────────────────────────────────────

  return (
    <>
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <SectionHeader
        title="Agent Pool"
        description="Configure the worker agent pool. Initial agents are shared by both managers; dynamic agents are reserved for POSDCORB's Staffing function."
        icon={Users}
        actions={
          <button
            id="add-agent-btn"
            onClick={openAdd}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 transition"
          >
            <Plus className="h-3.5 w-3.5" />
            Add agent
          </button>
        }
      />

      {/* ── Summary Metric Cards ────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard
          label="Total agents"
          value={agents.length}
          hint={`${initialAgents.length} initial · ${dynamicAgents.length} dynamic`}
          icon={Users}
          accent="#6366f1"
        />
        <MetricCard
          label="Total concurrency"
          value={totalConcurrency}
          unit="slots"
          hint="Sum of max_concurrent_tasks"
          icon={Layers}
          accent="#f59e0b"
        />
        <MetricCard
          label="Mean CPU usage"
          value={`${avgCpu}%`}
          hint="Across all agents"
          icon={Cpu}
          accent={avgCpu >= 85 ? "#ef4444" : avgCpu >= 60 ? "#f59e0b" : "#22c55e"}
        />
        <MetricCard
          label="Mean reliability"
          value={`${avgReliability.toFixed(1)}%`}
          hint={`${totalCompleted} tasks completed total`}
          icon={ShieldCheck}
          accent="#22c55e"
        />
      </div>

      {/* ── Status summary strip ────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2">
        {(Object.entries(statusCounts) as [AgentStatus, number][]).map(([s, n]) => (
          <div
            key={s}
            className="flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs shadow-sm"
          >
            <span className={`h-2 w-2 rounded-full ${STATUS_DOT[s]}`} />
            <span className="capitalize text-slate-700">{s}</span>
            <span className="font-semibold text-slate-900">{n}</span>
          </div>
        ))}
      </div>

      {/* ── CPU Utilization Chart ───────────────────────────────────────────── */}
      <ChartCard
        title="Agent CPU utilisation"
        subtitle="Sorted by CPU % — initial agents shown in indigo, dynamic in purple"
        height={240}
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 40 }}>
            <CartesianGrid stroke={GRID_STROKE} vertical={false} />
            <XAxis
              dataKey="name"
              tick={{ ...AXIS_TICK, textAnchor: "end" }}
              tickLine={false}
              interval={0}
            />
            <YAxis
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={36}
              domain={[0, 100]}
              tickFormatter={(v: number) => `${v}%`}
            />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              cursor={{ fill: "#f1f5f9" }}
              formatter={(value: number) => [`${value}%`, "CPU"]}
            />
            <Bar dataKey="cpu" name="CPU %" radius={[3, 3, 0, 0]}>
              {chartData.map((entry, index) => (
                <Cell
                  key={index}
                  fill={entry.origin === "initial" ? "#6366f1" : "#a855f7"}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* ── Filters + View toggle ───────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {/* Origin filter */}
          <div className="flex rounded-md border border-slate-200 bg-white text-xs shadow-sm overflow-hidden">
            {(["all", "initial", "dynamic"] as OriginFilter[]).map((f) => (
              <button
                key={f}
                id={`filter-origin-${f}`}
                onClick={() => setOriginFilter(f)}
                className={`px-3 py-1.5 capitalize transition ${
                  originFilter === f ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                {f}
              </button>
            ))}
          </div>

          {/* Status filter */}
          <select
            id="filter-status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 shadow-sm focus:border-indigo-400 focus:outline-none"
          >
            <option value="all">All statuses</option>
            {(["idle", "running", "waiting", "failed", "completed"] as AgentStatus[]).map((s) => (
              <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
            ))}
          </select>

          <span className="text-xs text-slate-400">{filteredAgents.length} of {agents.length} agents</span>
        </div>

        {/* View mode toggle */}
        <div className="flex rounded-md border border-slate-200 bg-white text-xs shadow-sm overflow-hidden">
          <button
            id="view-cards"
            onClick={() => setViewMode("cards")}
            className={`flex items-center gap-1.5 px-3 py-1.5 transition ${viewMode === "cards" ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}
          >
            <Activity className="h-3.5 w-3.5" />
            Cards
          </button>
          <button
            id="view-table"
            onClick={() => setViewMode("table")}
            className={`flex items-center gap-1.5 px-3 py-1.5 transition ${viewMode === "table" ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}
          >
            <Layers className="h-3.5 w-3.5" />
            Table
          </button>
        </div>
      </div>

      {/* ── Agent Status Cards ──────────────────────────────────────────────── */}
      {viewMode === "cards" && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filteredAgents.length === 0 ? (
            <div className="col-span-full py-12 text-center text-sm text-slate-400">
              No agents match the current filters.
            </div>
          ) : (
            filteredAgents.map((agent) => (
              <AgentStatusCard
                key={agent.id}
                agent={agent}
                onEdit={() => openEdit(agent)}
                onRemove={() => confirmDelete(agent.id)}
              />
            ))
          )}
        </div>
      )}

      {/* ── Agent Table ─────────────────────────────────────────────────────── */}
      {viewMode === "table" && (
        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-200 bg-slate-50 text-slate-500">
              <tr>
                {([
                  ["name", "Name"],
                  ["role", "Role"],
                  ["origin", "Origin"],
                  ["status", "Status"],
                  ["cpu_usage", "CPU"],
                  ["current_workload", "Workload"],
                  ["processing_speed", "Speed"],
                  ["reliability", "Reliability"],
                  ["tasks_completed", "Completed"],
                  ["tasks_failed", "Failed"],
                ] as [SortKey, string][]).map(([key, label]) => (
                  <th key={key} className="whitespace-nowrap px-3 py-2 font-medium">
                    <button
                      onClick={() => toggleSort(key)}
                      className="inline-flex items-center gap-0.5 hover:text-slate-900"
                    >
                      {label}
                      <SortIcon col={key} />
                    </button>
                  </th>
                ))}
                <th className="px-3 py-2 text-right font-medium">Capabilities</th>
                <th className="px-3 py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredAgents.length === 0 && (
                <tr>
                  <td colSpan={12} className="py-8 text-center text-slate-400">
                    No agents match the current filters.
                  </td>
                </tr>
              )}
              {filteredAgents.map((agent) => {
                const successRate =
                  agent.tasks_completed + agent.tasks_failed > 0
                    ? ((agent.tasks_completed / (agent.tasks_completed + agent.tasks_failed)) * 100).toFixed(1) + "%"
                    : "—";
                return (
                  <tr key={agent.id} className="hover:bg-slate-50">
                    <td className="whitespace-nowrap px-3 py-2">
                      <div className="font-medium text-slate-900">{agent.name}</div>
                      <div className="text-[10px] text-slate-400">{agent.id}</div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-slate-600">{agent.role}</td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <OriginBadge origin={agent.origin} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <StatusPill status={agent.status} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <CpuBar pct={agent.cpu_usage} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <WorkloadBar current={agent.current_workload} max={agent.max_concurrent_tasks} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 tabular-nums text-slate-600">
                      {agent.processing_speed.toFixed(2)}&times;
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 tabular-nums text-slate-600">
                      {(agent.reliability * 100).toFixed(0)}%
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <span className="flex items-center gap-1 text-emerald-600">
                        <CheckCircle2 className="h-3 w-3" />
                        {agent.tasks_completed}
                        <span className="text-slate-400 text-[10px]">({successRate})</span>
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <span className="flex items-center gap-1 text-red-500">
                        <XCircle className="h-3 w-3" />
                        {agent.tasks_failed}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <CapabilityTags caps={agent.capabilities} max={2} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      <div className="flex justify-end gap-1">
                        <button
                          onClick={() => openEdit(agent)}
                          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition"
                          title="Edit"
                          aria-label={`Edit ${agent.name}`}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => confirmDelete(agent.id)}
                          className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600 transition"
                          title="Remove"
                          aria-label={`Remove ${agent.name}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Pool Breakdown Legend ───────────────────────────────────────────── */}
      <div className="rounded-md border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-700">
          <Gauge className="h-3.5 w-3.5 text-slate-400" />
          Pool composition
        </div>
        <div className="grid grid-cols-2 gap-4 text-xs text-slate-600 sm:grid-cols-4">
          <div>
            <div className="text-slate-400">Initial pool</div>
            <div className="font-semibold text-blue-700">{initialAgents.length} agents</div>
            <div className="text-[10px] text-slate-400">Available to both managers</div>
          </div>
          <div>
            <div className="text-slate-400">Dynamic pool</div>
            <div className="font-semibold text-purple-700">{dynamicAgents.length} agents</div>
            <div className="text-[10px] text-slate-400">POSDCORB Staffing reserve</div>
          </div>
          <div>
            <div className="text-slate-400">Currently running</div>
            <div className="font-semibold text-indigo-700">{statusCounts.running} agents</div>
          </div>
          <div>
            <div className="text-slate-400">Idle / waiting</div>
            <div className="font-semibold text-slate-700">{statusCounts.idle + statusCounts.waiting} agents</div>
          </div>
        </div>
      </div>

      {/* ── Add/Edit Modal ──────────────────────────────────────────────────── */}
      {modalOpen && (
        <AgentModal
          agent={editingAgent}
          onSave={handleSave}
          onClose={closeModal}
        />
      )}

      {/* ── Delete Confirm Dialog ───────────────────────────────────────────── */}
      {deleteId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-[2px]">
          <div className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-5 shadow-xl">
            <h3 className="text-sm font-semibold text-slate-900">Remove agent?</h3>
            <p className="mt-1 text-xs text-slate-500">
              This will remove <strong>{agents.find((a) => a.id === deleteId)?.name}</strong> from the pool.
              This action cannot be undone.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeleteId(null)}
                className="rounded border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                className="rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 transition"
              >
                Remove
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
