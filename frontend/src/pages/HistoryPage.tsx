import { History, Search } from "lucide-react";
import { useMemo, useState } from "react";
import DataTable, { type Column } from "../components/DataTable";
import SectionHeader from "../components/SectionHeader";
import StatusBadge from "../components/StatusBadge";
import { MOCK_EXPERIMENTS } from "../data/mock";
import { fmtDate, fmtMs } from "../services/format";
import type { ExperimentRecord, ExperimentStatus } from "../types/experiment";

const columns: Column<ExperimentRecord>[] = [
  { key: "id", header: "ID", sortValue: (r) => r.id, render: (r) => `#${r.id}` },
  { key: "name", header: "Name", sortValue: (r) => r.name, className: "font-sans text-slate-900" },
  { key: "status", header: "Status", sortValue: (r) => r.status, render: (r) => <StatusBadge status={r.status} /> },
  { key: "workload", header: "Workload", className: "font-sans", sortValue: (r) => r.workload },
  { key: "workers", header: "Workers", align: "right", sortValue: (r) => r.num_workers, render: (r) => r.num_workers },
  { key: "tasks", header: "Tasks", align: "right", sortValue: (r) => r.num_tasks, render: (r) => r.num_tasks },
  { key: "seed", header: "Seed", align: "right", render: (r) => r.random_seed },
  { key: "conv", header: "Conv. latency", align: "right", render: (r) => (r.results ? fmtMs(r.results.conventional.avg_latency_ms) : "—") },
  { key: "pos", header: "POSDCORB latency", align: "right", render: (r) => (r.results ? fmtMs(r.results.posdcorb.avg_latency_ms) : "—") },
  { key: "created", header: "Created", sortValue: (r) => r.created_at, render: (r) => fmtDate(r.created_at) },
  {
    key: "open",
    header: "",
    render: (r) =>
      r.status === "completed" ? (
        <a href={`#/results/${r.id}`} className="font-sans text-xs text-indigo-700 hover:underline">Results</a>
      ) : null,
  },
];

export default function HistoryPage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<ExperimentStatus | "all">("all");

  const rows = useMemo(
    () =>
      MOCK_EXPERIMENTS.filter(
        (r) =>
          (status === "all" || r.status === status) &&
          (q.trim() === "" || r.name.toLowerCase().includes(q.toLowerCase()) || `#${r.id}` === q.trim()),
      ).sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [q, status],
  );

  return (
    <>
      <SectionHeader
        title="Experiment history"
        description={`${rows.length} of ${MOCK_EXPERIMENTS.length} experiments`}
        icon={History}
        actions={
          <>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1.5 h-3.5 w-3.5 text-slate-400" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search name or #id"
                className="w-44 rounded border border-slate-300 bg-white py-1 pl-7 pr-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as ExperimentStatus | "all")}
              className="rounded border border-slate-300 bg-white px-2 py-1 text-sm"
              aria-label="Filter by status"
            >
              <option value="all">All statuses</option>
              <option value="completed">Completed</option>
              <option value="running">Running</option>
              <option value="pending">Pending</option>
              <option value="failed">Failed</option>
            </select>
          </>
        }
      />
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} emptyMessage="No experiments match the filters." />
    </>
  );
}
