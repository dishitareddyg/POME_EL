/**
 * SimulationPage — Module 7 wired frontend.
 *
 * Runs both managers against the same workload via POST /api/simulate and
 * displays side-by-side results.  Uses only existing design-system components
 * (MetricCard, StatusBadge, SectionHeader).
 *
 * State machine:
 *   idle → running → done | error
 *   Reset button always available to return to idle.
 */
import { Pause, Play, PlayCircle, RotateCcw } from "lucide-react";
import { useState } from "react";
import MetricCard from "../components/MetricCard";
import SectionHeader from "../components/SectionHeader";
import StatusBadge from "../components/StatusBadge";
import { MANAGER_COLORS, MANAGER_LABELS } from "../data/managers";
import { api } from "../services/api";
import type { ManagerType } from "../types/experiment";
import type { ManagementReport } from "../types/posdcorb";
import type { ManagerRunResult, SimulateResponse } from "../types/simulation";

// ---------------------------------------------------------------------------
// Shared button style
// ---------------------------------------------------------------------------

const btn =
  "inline-flex items-center gap-1.5 rounded border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-40";

// ---------------------------------------------------------------------------
// Default workload — small enough to run fast in a browser demo
// ---------------------------------------------------------------------------

const DEFAULT_WORKLOAD_CONFIG = {
  num_tasks: 12,
  complexity: "medium" as const,
  dependency_percentage: 30,
  seed: 42,
};

// ---------------------------------------------------------------------------
// ManagerPanel
// ---------------------------------------------------------------------------

function ManagerPanel({
  manager,
  result,
  running,
}: {
  manager: ManagerType;
  result?: ManagerRunResult;
  running: boolean;
}) {
  const statusLabel = running ? "running" : result ? "completed" : "idle";
  const statusType = running
    ? "running"
    : result
      ? "completed"
      : "pending";

  return (
    <section
      className="rounded-md border border-slate-200 border-t-4 bg-white p-4"
      style={{ borderTopColor: MANAGER_COLORS[manager] }}
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold">{MANAGER_LABELS[manager]}</h3>
        <StatusBadge status={statusType} label={statusLabel} />
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <MetricCard
          label="Tasks done"
          value={result ? result.completed_tasks : "—"}
          accent={MANAGER_COLORS[manager]}
        />
        <MetricCard
          label="Pending"
          value={
            result
              ? Math.max(
                  0,
                  result.total_tasks - result.completed_tasks - result.failed_tasks
                )
              : "—"
          }
          hint="Not yet completed or failed"
        />
        <MetricCard
          label="Avg decision latency"
          value={result ? Math.round(result.avg_decision_latency_ms) : "—"}
          unit="ms"
          hint="Mean think-time per latency-bearing decision"
        />
        <MetricCard
          label="Success"
          value={
            result ? `${(result.success_rate * 100).toFixed(1)}` : "—"
          }
          unit="%"
        />
      </div>
      {result && (
        <p className="mt-2 text-xs text-slate-400">
          {result.total_decisions} decisions · {Math.round(result.total_wall_ms)}ms
          simulated wall time · terminated: {result.termination_reason ?? "—"}
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// PosdcorbStatusPanel
// ---------------------------------------------------------------------------

/**
 * POSDCORB STATUS — exposes the seven POSDCORB functions and the live
 * ManagementReport produced by the POSDCORB Adaptive Manager.
 */
function PosdcorbStatusPanel({
  report,
  budgetLimitMs,
}: {
  report?: ManagementReport;
  budgetLimitMs?: number;
}) {
  const m = report?.management;
  const a = report?.agents;
  const w = report?.workload;
  const b = report?.budget;

  const budgetPct =
    b && budgetLimitMs && budgetLimitMs > 0
      ? Math.min(100, Math.round((b.consumed_wall_ms / budgetLimitMs) * 100))
      : undefined;

  const rows: { label: string; value: string }[] = [
    {
      label: "Planning",
      value: m?.plan_created
        ? `Complete${m.plan_updates > 0 ? ` (${m.plan_updates} updates)` : ""}`
        : "—",
    },
    {
      label: "Organization",
      value: m ? `${m.parallel_group_count} parallel groups` : "—",
    },
    {
      label: "Staffing",
      value: a
        ? `${a.initial_agent_count} initial → ${a.active_agent_count} active`
        : "—",
    },
    {
      label: "Directing",
      value: m ? `${m.directives_issued} assignments` : "—",
    },
    {
      label: "Coordination",
      value: m ? `${m.coordination_events} events` : "—",
    },
    { label: "Reporting", value: report ? "Live" : "—" },
    {
      label: "Budget",
      value: b
        ? budgetPct !== undefined
          ? `${budgetPct}% consumed (${b.consumed_decisions} decisions)`
          : `${Math.round(b.consumed_wall_ms)}ms consumed (${b.consumed_decisions} decisions)`
        : "—",
    },
  ];

  return (
    <section
      className="rounded-md border border-slate-200 border-t-4 bg-white p-4"
      style={{ borderTopColor: MANAGER_COLORS.posdcorb }}
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold">POSDCORB STATUS</h3>
        <StatusBadge
          status={report ? "completed" : "pending"}
          label={report ? "live" : "idle"}
        />
      </div>

      <ul className="mb-3 divide-y divide-slate-100 rounded border border-slate-200">
        {rows.map((row) => (
          <li
            key={row.label}
            className="flex items-center justify-between px-3 py-1.5 text-xs"
          >
            <span className="font-medium text-slate-700">{row.label}</span>
            <span className="tabular-nums text-slate-500">{row.value}</span>
          </li>
        ))}
      </ul>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <MetricCard
          label="Active agents"
          value={a ? a.active_agent_count : "—"}
          accent={MANAGER_COLORS.posdcorb}
        />
        <MetricCard
          label="Pending tasks"
          value={w ? w.pending_tasks : "—"}
          accent={MANAGER_COLORS.posdcorb}
        />
        <MetricCard
          label="Completed tasks"
          value={w ? w.completed_tasks : "—"}
          accent={MANAGER_COLORS.posdcorb}
        />
        <MetricCard
          label="Replans"
          value={m ? m.replan_count : "—"}
          hint={
            m && m.recovery_count > 0
              ? `${m.recovery_count} recoveries`
              : undefined
          }
          accent={MANAGER_COLORS.posdcorb}
        />
      </div>

      <p className="mt-2 text-xs text-slate-400">
        {report
          ? "Values reflect the ManagementReport embedded in the execution trace."
          : "Run a POSDCORB simulation to populate planning, staffing and budget state."}
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// EventLog
// ---------------------------------------------------------------------------

function EventLog({ results }: { results?: SimulateResponse["results"] }) {
  if (!results) {
    return (
      <p className="py-4 text-center text-xs text-slate-500">
        No events. Start a simulation to see task assignments and manager
        decisions.
      </p>
    );
  }

  // Collect last 30 decisions across all managers, most recent first.
  const events = Object.entries(results).flatMap(([manager, res]) =>
    (res.decisions ?? []).slice(-15).map((d) => ({ manager, ...d }))
  );

  if (events.length === 0) {
    return (
      <p className="py-4 text-center text-xs text-slate-500">
        Simulation completed but no events were recorded.
      </p>
    );
  }

  return (
    <ul className="divide-y divide-slate-100 text-xs">
      {events.slice(-30).map((ev, i) => (
        <li
          key={i}
          className="flex items-start gap-2 px-1 py-1.5 hover:bg-slate-50"
        >
          <span
            className="mt-0.5 inline-block w-2 h-2 shrink-0 rounded-full"
            style={{
              backgroundColor:
                MANAGER_COLORS[ev.manager as ManagerType] ?? "#94a3b8",
            }}
          />
          <span className="font-mono text-slate-400 shrink-0 w-48 truncate">
            {ev.manager_decision}
          </span>
          <span className="text-slate-600 truncate">{ev.reason}</span>
          {ev.task_id && (
            <span className="ml-auto shrink-0 text-slate-400">
              {ev.task_id}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type PageStatus = "idle" | "running" | "done" | "error";

export default function SimulationPage() {
  const [status, setStatus] = useState<PageStatus>("idle");
  const [response, setResponse] = useState<SimulateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isRunning = status === "running";
  const isDone = status === "done" || status === "error";

  async function handleStart() {
    setStatus("running");
    setError(null);
    setResponse(null);
    try {
      const res = await api.simulate({
        workload_config: DEFAULT_WORKLOAD_CONFIG,
        manager_types: ["conventional", "posdcorb"],
        rng_seed: 42,
        max_decisions: 10_000,
        max_wall_ms: 300_000,
      });
      setResponse(res);
      setStatus("done");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus("error");
    }
  }

  function handleReset() {
    setStatus("idle");
    setResponse(null);
    setError(null);
  }

  const convResult = response?.results?.["conventional"];
  const posdResult = response?.results?.["posdcorb"];
  const posdReport = posdResult?.management_report ?? undefined;

  return (
    <>
      <SectionHeader
        title="Simulation"
        description="Side-by-side live view of both managers on identical inputs."
        icon={PlayCircle}
        actions={
          <>
            <button
              className={btn}
              onClick={handleStart}
              disabled={isRunning}
            >
              <Play className="h-4 w-4" />
              {isRunning ? "Running…" : "Start"}
            </button>
            {/* Pause not applicable for synchronous run — kept disabled */}
            <button className={btn} disabled>
              <Pause className="h-4 w-4" /> Pause
            </button>
            <button
              className={btn}
              onClick={handleReset}
              disabled={isRunning}
            >
              <RotateCcw className="h-4 w-4" /> Reset
            </button>
          </>
        }
      />

      {/* Status banner */}
      {status === "idle" && (
        <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          Click <strong>Start</strong> to run both managers against a{" "}
          {DEFAULT_WORKLOAD_CONFIG.num_tasks}-task workload (seed{" "}
          {DEFAULT_WORKLOAD_CONFIG.seed}).
        </div>
      )}
      {isRunning && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 animate-pulse">
          Simulation running — both managers executing the same workload…
        </div>
      )}
      {status === "error" && error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          <strong>Error:</strong> {error}
        </div>
      )}
      {status === "done" && response && (
        <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-800">
          Simulation complete in{" "}
          <strong>{response.real_elapsed_s.toFixed(2)}s</strong> (graph{" "}
          {response.graph_id}, seed {response.seed}).
        </div>
      )}

      {/* Manager panels */}
      <div className="grid gap-4 xl:grid-cols-2">
        <ManagerPanel
          manager="conventional"
          result={convResult}
          running={isRunning}
        />
        <ManagerPanel
          manager="posdcorb"
          result={posdResult}
          running={isRunning}
        />
      </div>

      {/* POSDCORB deep-dive */}
      <PosdcorbStatusPanel
        report={posdReport}
        budgetLimitMs={response?.max_wall_ms ?? undefined}
      />

      {/* Event log */}
      <section className="rounded-md border border-slate-200 bg-white p-4">
        <h3 className="mb-2 text-sm font-semibold">Event log</h3>
        <EventLog results={isDone ? response?.results : undefined} />
      </section>
    </>
  );
}
