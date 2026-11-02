import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  FilePlus2,
  Loader2,
  RotateCcw,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import SectionHeader from "../components/SectionHeader";
import { MANAGER_LABELS } from "../data/managers";
import { api } from "../services/api";
import type {
  AgentConfig,
  CreateExperimentResponse,
  ExperimentConfig,
  ManagerType,
  PriorityDistribution,
  ResourceBudget,
  TaskComplexity,
  WorkloadConfig,
} from "../types/experiment";

// ---------------------------------------------------------------------------
// Default form state (mirrors backend defaults)
// ---------------------------------------------------------------------------

const DEFAULT_WORKLOAD: WorkloadConfig = {
  num_tasks: 200,
  task_complexity: "medium",
  dependency_percentage: 20,
  priority_distribution: { critical: 5, high: 20, medium: 50, low: 25 },
};

const DEFAULT_AGENTS: AgentConfig = {
  initial_agent_count: 8,
  max_agent_count: 16,
  agent_heterogeneity: 0.3,
  agent_failure_probability: 0.05,
};

const DEFAULT_RESOURCES: ResourceBudget = {
  max_execution_time_s: 300,
  max_manager_decisions: 10_000,
  max_llm_calls: 5_000,
  token_budget: 500_000,
};

interface FormState {
  // Identity
  name: string;
  random_seed: number;
  repetitions: number;
  // Sub-sections
  workload: WorkloadConfig;
  agents: AgentConfig;
  resources: ResourceBudget;
  manager_types: ManagerType[];
}

const DEFAULTS: FormState = {
  name: "",
  random_seed: 42,
  repetitions: 1,
  workload: DEFAULT_WORKLOAD,
  agents: DEFAULT_AGENTS,
  resources: DEFAULT_RESOURCES,
  manager_types: ["conventional", "posdcorb"],
};

// ---------------------------------------------------------------------------
// Shared UI primitives
// ---------------------------------------------------------------------------

const inputCls =
  "w-full rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm " +
  "text-slate-900 placeholder-slate-400 transition " +
  "focus:border-indigo-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20";

const sliderCls =
  "w-full h-1.5 rounded-full bg-slate-200 accent-indigo-600 cursor-pointer";

function Field({
  label,
  hint,
  error,
  children,
  id,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  id?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label
        htmlFor={id}
        className="text-xs font-semibold uppercase tracking-wide text-slate-500"
      >
        {label}
      </label>
      {children}
      {error ? (
        <span className="flex items-center gap-1 text-xs text-red-600">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          {error}
        </span>
      ) : hint ? (
        <span className="text-xs text-slate-400">{hint}</span>
      ) : null}
    </div>
  );
}

function SectionCard({
  title,
  badge,
  children,
}: {
  title: string;
  badge?: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-3.5">
        <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
        {badge && (
          <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-600">
            {badge}
          </span>
        )}
      </div>
      <div className="space-y-5 p-5">{children}</div>
    </div>
  );
}

function SliderField({
  id,
  label,
  hint,
  error,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  format?: (v: number) => string;
  onChange: (v: number) => void;
}) {
  const display = format ? format(value) : String(value);
  return (
    <Field label={label} hint={hint} error={error} id={id}>
      <div className="flex items-center gap-3">
        <input
          id={id}
          type="range"
          className={sliderCls}
          min={min}
          max={max}
          step={step ?? 1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        <span className="w-16 shrink-0 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-center text-xs font-mono font-semibold text-slate-700">
          {display}
        </span>
      </div>
    </Field>
  );
}

// ---------------------------------------------------------------------------
// Priority distribution sub-form
// ---------------------------------------------------------------------------

const PRIORITY_KEYS: (keyof PriorityDistribution)[] = [
  "critical",
  "high",
  "medium",
  "low",
];

const PRIORITY_COLORS: Record<keyof PriorityDistribution, string> = {
  critical: "bg-red-500",
  high: "bg-orange-400",
  medium: "bg-amber-400",
  low: "bg-slate-300",
};

function PriorityEditor({
  value,
  onChange,
  error,
}: {
  value: PriorityDistribution;
  onChange: (v: PriorityDistribution) => void;
  error?: string;
}) {
  const total = PRIORITY_KEYS.reduce((s, k) => s + value[k], 0);

  const setKey = (k: keyof PriorityDistribution, v: number) => {
    onChange({ ...value, [k]: v });
  };

  return (
    <div className="space-y-3">
      {/* Stacked bar */}
      <div className="flex h-3 w-full overflow-hidden rounded-full">
        {PRIORITY_KEYS.map((k) => (
          <div
            key={k}
            className={`${PRIORITY_COLORS[k]} transition-all`}
            style={{ width: `${value[k]}%` }}
            title={`${k}: ${value[k]}%`}
          />
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {PRIORITY_KEYS.map((k) => (
          <div key={k} className="space-y-1">
            <div className="flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${PRIORITY_COLORS[k]}`} />
              <span className="text-xs font-medium capitalize text-slate-600">
                {k}
              </span>
            </div>
            <input
              type="number"
              className={inputCls}
              min={0}
              max={100}
              value={value[k]}
              onChange={(e) => setKey(k, Math.max(0, Math.min(100, Number(e.target.value))))}
            />
          </div>
        ))}
      </div>

      <div
        className={`flex items-center gap-1 text-xs font-medium ${
          total === 100 ? "text-green-600" : "text-red-600"
        }`}
      >
        {total === 100 ? (
          <CheckCircle2 className="h-3.5 w-3.5" />
        ) : (
          <AlertTriangle className="h-3.5 w-3.5" />
        )}
        Total: {total}% {total !== 100 && "(must equal 100)"}
        {error && <span className="ml-1 text-red-600">— {error}</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Manager selector
// ---------------------------------------------------------------------------

const MANAGER_DESCRIPTIONS: Record<ManagerType, string> = {
  conventional: "Flat LLM supervisor issuing direct task instructions",
  posdcorb:
    "Structured manager using Planning, Organizing, Staffing, Directing, Coordinating, Reporting, Budgeting functions",
};

const MANAGER_ICONS: Record<ManagerType, string> = {
  conventional: "🤖",
  posdcorb: "🏛️",
};

function ManagerSelector({
  value,
  onChange,
  error,
}: {
  value: ManagerType[];
  onChange: (v: ManagerType[]) => void;
  error?: string;
}) {
  const toggle = (m: ManagerType) => {
    if (value.includes(m)) {
      onChange(value.filter((t) => t !== m));
    } else {
      onChange([...value, m]);
    }
  };

  const mgrs: ManagerType[] = ["conventional", "posdcorb"];

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        {mgrs.map((m) => {
          const active = value.includes(m);
          return (
            <button
              key={m}
              type="button"
              onClick={() => toggle(m)}
              className={`flex items-start gap-3 rounded-xl border-2 p-4 text-left transition-all ${
                active
                  ? "border-indigo-500 bg-indigo-50 shadow-sm"
                  : "border-slate-200 bg-white hover:border-slate-300"
              }`}
            >
              <div
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-lg ${
                  active ? "bg-indigo-100" : "bg-slate-100"
                }`}
              >
                {MANAGER_ICONS[m]}
              </div>
              <div className="min-w-0">
                <div className={`text-sm font-semibold ${active ? "text-indigo-700" : "text-slate-800"}`}>
                  {MANAGER_LABELS[m]}
                </div>
                <div className="mt-0.5 text-xs text-slate-500 leading-relaxed">
                  {MANAGER_DESCRIPTIONS[m]}
                </div>
              </div>
              <div
                className={`ml-auto mt-0.5 h-4 w-4 shrink-0 rounded border-2 transition-all ${
                  active
                    ? "border-indigo-500 bg-indigo-500"
                    : "border-slate-300 bg-white"
                }`}
              >
                {active && (
                  <CheckCircle2 className="h-3 w-3 text-white -translate-x-[1px] -translate-y-[1px]" />
                )}
              </div>
            </button>
          );
        })}
      </div>

      {value.length === 2 && (
        <div className="flex items-center gap-2 rounded-lg bg-indigo-50 px-3 py-2 text-xs font-medium text-indigo-700">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
          Both managers selected — ideal for comparative experiments
        </div>
      )}

      {error && (
        <span className="flex items-center gap-1 text-xs text-red-600">
          <AlertTriangle className="h-3 w-3" />
          {error}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

interface FormErrors {
  name?: string;
  random_seed?: string;
  repetitions?: string;
  num_tasks?: string;
  dependency_percentage?: string;
  priority_distribution?: string;
  initial_agent_count?: string;
  max_agent_count?: string;
  agent_heterogeneity?: string;
  agent_failure_probability?: string;
  max_execution_time_s?: string;
  max_manager_decisions?: string;
  max_llm_calls?: string;
  token_budget?: string;
  manager_types?: string;
}

function validate(f: FormState): FormErrors {
  const e: FormErrors = {};
  if (!f.name.trim()) e.name = "Name is required";
  else if (f.name.trim().length > 200) e.name = "Max 200 characters";

  if (f.repetitions < 1 || f.repetitions > 50) e.repetitions = "1–50";

  if (f.workload.num_tasks < 1 || f.workload.num_tasks > 10_000)
    e.num_tasks = "1–10 000";
  if (
    f.workload.dependency_percentage < 0 ||
    f.workload.dependency_percentage > 100
  )
    e.dependency_percentage = "0–100%";

  const prioritySum = PRIORITY_KEYS.reduce(
    (s, k) => s + f.workload.priority_distribution[k],
    0
  );
  if (prioritySum !== 100) e.priority_distribution = "Must sum to 100";

  if (f.agents.initial_agent_count < 1 || f.agents.initial_agent_count > 256)
    e.initial_agent_count = "1–256";
  if (f.agents.max_agent_count < f.agents.initial_agent_count)
    e.max_agent_count = "Must be ≥ initial count";
  if (f.agents.max_agent_count > 256) e.max_agent_count = "Max 256";

  if (f.agents.agent_heterogeneity < 0 || f.agents.agent_heterogeneity > 1)
    e.agent_heterogeneity = "0–1";
  if (
    f.agents.agent_failure_probability < 0 ||
    f.agents.agent_failure_probability > 1
  )
    e.agent_failure_probability = "0–1";

  if (f.resources.max_execution_time_s < 1)
    e.max_execution_time_s = "At least 1s";
  if (f.resources.max_manager_decisions < 1)
    e.max_manager_decisions = "At least 1";
  if (f.resources.max_llm_calls < 1) e.max_llm_calls = "At least 1";
  if (f.resources.token_budget < 1) e.token_budget = "At least 1";

  if (f.manager_types.length === 0)
    e.manager_types = "Select at least one manager";

  return e;
}

// ---------------------------------------------------------------------------
// Config summary panel
// ---------------------------------------------------------------------------

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2 border-b border-slate-100 last:border-0">
      <span className="text-xs text-slate-500 shrink-0">{label}</span>
      <span className="text-xs font-medium text-slate-800 text-right break-all">{value}</span>
    </div>
  );
}

function ConfigSummary({ f }: { f: FormState }) {
  return (
    <div className="space-y-0">
      <SummaryRow label="Experiment name" value={f.name || "—"} />
      <SummaryRow label="Random seed" value={String(f.random_seed)} />
      <SummaryRow label="Repetitions" value={String(f.repetitions)} />
      <SummaryRow label="Tasks" value={String(f.workload.num_tasks)} />
      <SummaryRow label="Complexity" value={f.workload.task_complexity} />
      <SummaryRow
        label="Dependencies"
        value={`${f.workload.dependency_percentage}%`}
      />
      <SummaryRow
        label="Priority dist."
        value={PRIORITY_KEYS.map((k) => `${k[0].toUpperCase()}:${f.workload.priority_distribution[k]}%`).join(" ")}
      />
      <SummaryRow
        label="Agents (init→max)"
        value={`${f.agents.initial_agent_count} → ${f.agents.max_agent_count}`}
      />
      <SummaryRow
        label="Heterogeneity"
        value={f.agents.agent_heterogeneity.toFixed(2)}
      />
      <SummaryRow
        label="Failure prob."
        value={`${(f.agents.agent_failure_probability * 100).toFixed(1)}%`}
      />
      <SummaryRow
        label="Max exec. time"
        value={`${f.resources.max_execution_time_s}s`}
      />
      <SummaryRow
        label="Manager decisions"
        value={f.resources.max_manager_decisions.toLocaleString()}
      />
      <SummaryRow
        label="LLM calls"
        value={f.resources.max_llm_calls.toLocaleString()}
      />
      <SummaryRow
        label="Token budget"
        value={f.resources.token_budget.toLocaleString()}
      />
      <SummaryRow
        label="Managers"
        value={
          f.manager_types.length === 0
            ? "None selected"
            : f.manager_types.map((m) => MANAGER_LABELS[m]).join(" + ")
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Success overlay
// ---------------------------------------------------------------------------

function SuccessPanel({
  response,
  onNew,
}: {
  response: CreateExperimentResponse;
  onNew: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-6 rounded-xl border border-green-200 bg-green-50 p-8 text-center shadow-sm">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-green-100">
        <CheckCircle2 className="h-8 w-8 text-green-600" />
      </div>
      <div className="space-y-1">
        <h3 className="text-lg font-semibold text-green-900">
          Experiment queued!
        </h3>
        <p className="text-sm text-green-700">{response.message}</p>
      </div>
      <div className="w-full rounded-lg border border-green-200 bg-white p-4 text-left space-y-2">
        <div className="flex justify-between text-xs">
          <span className="text-slate-500">Experiment ID</span>
          <span className="font-mono font-semibold text-slate-800 break-all">
            {response.experiment_id}
          </span>
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-slate-500">Name</span>
          <span className="font-semibold text-slate-800">{response.name}</span>
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-slate-500">Status</span>
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-700">
            {response.status}
          </span>
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-slate-500">Created</span>
          <span className="text-slate-800">
            {new Date(response.created_at * 1000).toLocaleString()}
          </span>
        </div>
      </div>
      <button
        type="button"
        onClick={onNew}
        className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 transition-colors"
      >
        <FilePlus2 className="h-4 w-4" />
        New experiment
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function NewExperimentPage() {
  const [f, setF] = useState<FormState>(DEFAULTS);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<CreateExperimentResponse | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  const errors = validate(f);
  const isValid = Object.keys(errors).length === 0;

  // Updaters
  const setWorkload = <K extends keyof WorkloadConfig>(
    k: K,
    v: WorkloadConfig[K]
  ) => setF((p) => ({ ...p, workload: { ...p.workload, [k]: v } }));

  const setAgents = <K extends keyof AgentConfig>(
    k: K,
    v: AgentConfig[K]
  ) => setF((p) => ({ ...p, agents: { ...p.agents, [k]: v } }));

  const setResources = <K extends keyof ResourceBudget>(
    k: K,
    v: ResourceBudget[K]
  ) => setF((p) => ({ ...p, resources: { ...p.resources, [k]: v } }));

  const num = (s: string) => (s === "" ? 0 : Number(s));

  const buildConfig = (): ExperimentConfig => ({
    name: f.name.trim(),
    random_seed: f.random_seed,
    repetitions: f.repetitions,
    workload: f.workload,
    agents: f.agents,
    resources: f.resources,
    managers: { manager_types: f.manager_types },
  });

  const handleSubmit = async () => {
    if (!isValid) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const resp = await api.createExperiment({ config: buildConfig() });
      setResult(resp);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleReset = () => {
    setF(DEFAULTS);
    setResult(null);
    setSubmitError(null);
    setShowPreview(false);
  };

  // -- Success state --
  if (result) {
    return (
      <>
        <SectionHeader
          title="New experiment"
          description="Experiment created successfully."
          icon={FilePlus2}
        />
        <div className="max-w-xl">
          <SuccessPanel response={result} onNew={handleReset} />
        </div>
      </>
    );
  }

  return (
    <>
      <SectionHeader
        title="New experiment"
        description="Configure a comparative run. Both managers receive identical workload, agents, and seed."
        icon={FilePlus2}
      />

      <div className="grid gap-5 xl:grid-cols-3">
        {/* ---- Left column: form ---- */}
        <div className="space-y-5 xl:col-span-2">

          {/* Experiment identity */}
          <SectionCard title="Experiment">
            <div className="grid gap-5 sm:grid-cols-3">
              <div className="sm:col-span-3">
                <Field label="Experiment name" error={errors.name} id="exp-name">
                  <input
                    id="exp-name"
                    className={inputCls}
                    value={f.name}
                    onChange={(e) => setF((p) => ({ ...p, name: e.target.value }))}
                    placeholder="e.g. Scaling — 200 tasks, mixed complexity"
                  />
                </Field>
              </div>
              <Field label="Random seed" hint="Same seed for all managers" id="exp-seed">
                <input
                  id="exp-seed"
                  type="number"
                  className={inputCls}
                  value={f.random_seed}
                  onChange={(e) => setF((p) => ({ ...p, random_seed: num(e.target.value) }))}
                />
              </Field>
              <Field label="Repetitions" hint="Independent runs per manager" error={errors.repetitions} id="exp-reps">
                <input
                  id="exp-reps"
                  type="number"
                  className={inputCls}
                  min={1}
                  max={50}
                  value={f.repetitions}
                  onChange={(e) => setF((p) => ({ ...p, repetitions: Math.max(1, num(e.target.value)) }))}
                />
              </Field>
            </div>
          </SectionCard>

          {/* Workload */}
          <SectionCard title="Workload">
            <div className="grid gap-5 sm:grid-cols-3">
              <Field label="Number of tasks" error={errors.num_tasks} id="wl-tasks">
                <input
                  id="wl-tasks"
                  type="number"
                  className={inputCls}
                  min={1}
                  max={10_000}
                  value={f.workload.num_tasks}
                  onChange={(e) => setWorkload("num_tasks", num(e.target.value))}
                />
              </Field>
              <Field label="Task complexity" id="wl-complexity">
                <select
                  id="wl-complexity"
                  className={inputCls}
                  value={f.workload.task_complexity}
                  onChange={(e) =>
                    setWorkload("task_complexity", e.target.value as TaskComplexity)
                  }
                >
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </Field>
              <SliderField
                id="wl-dep"
                label="Dependency %"
                hint="Tasks with ≥1 prerequisite"
                error={errors.dependency_percentage}
                value={f.workload.dependency_percentage}
                min={0}
                max={100}
                step={5}
                format={(v) => `${v}%`}
                onChange={(v) => setWorkload("dependency_percentage", v)}
              />
            </div>
            <div>
              <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Task priority distribution
              </div>
              <PriorityEditor
                value={f.workload.priority_distribution}
                onChange={(v) => setWorkload("priority_distribution", v)}
                error={errors.priority_distribution}
              />
            </div>
          </SectionCard>

          {/* Agents */}
          <SectionCard title="Agents">
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label="Initial agent count" error={errors.initial_agent_count} id="ag-init">
                <input
                  id="ag-init"
                  type="number"
                  className={inputCls}
                  min={1}
                  max={256}
                  value={f.agents.initial_agent_count}
                  onChange={(e) => setAgents("initial_agent_count", num(e.target.value))}
                />
              </Field>
              <Field label="Maximum agent count" error={errors.max_agent_count} id="ag-max">
                <input
                  id="ag-max"
                  type="number"
                  className={inputCls}
                  min={f.agents.initial_agent_count}
                  max={256}
                  value={f.agents.max_agent_count}
                  onChange={(e) => setAgents("max_agent_count", num(e.target.value))}
                />
              </Field>
            </div>
            <SliderField
              id="ag-heterogeneity"
              label="Agent heterogeneity"
              hint="0 = identical clones; 1 = maximally diverse skill sets"
              error={errors.agent_heterogeneity}
              value={f.agents.agent_heterogeneity}
              min={0}
              max={1}
              step={0.05}
              format={(v) => v.toFixed(2)}
              onChange={(v) => setAgents("agent_heterogeneity", v)}
            />
            <SliderField
              id="ag-failure"
              label="Agent failure probability"
              hint="Per-step probability any agent fails"
              error={errors.agent_failure_probability}
              value={f.agents.agent_failure_probability}
              min={0}
              max={1}
              step={0.01}
              format={(v) => `${(v * 100).toFixed(0)}%`}
              onChange={(v) => setAgents("agent_failure_probability", v)}
            />
          </SectionCard>

          {/* Resource constraints */}
          <SectionCard title="Resource Constraints">
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label="Max execution time (s)" error={errors.max_execution_time_s} id="rc-time">
                <input
                  id="rc-time"
                  type="number"
                  className={inputCls}
                  min={1}
                  value={f.resources.max_execution_time_s}
                  onChange={(e) => setResources("max_execution_time_s", num(e.target.value))}
                />
              </Field>
              <Field label="Max manager decisions" error={errors.max_manager_decisions} id="rc-decisions">
                <input
                  id="rc-decisions"
                  type="number"
                  className={inputCls}
                  min={1}
                  value={f.resources.max_manager_decisions}
                  onChange={(e) =>
                    setResources("max_manager_decisions", num(e.target.value))
                  }
                />
              </Field>
              <Field label="Max LLM calls" error={errors.max_llm_calls} id="rc-llm">
                <input
                  id="rc-llm"
                  type="number"
                  className={inputCls}
                  min={1}
                  value={f.resources.max_llm_calls}
                  onChange={(e) => setResources("max_llm_calls", num(e.target.value))}
                />
              </Field>
              <Field label="Token budget" error={errors.token_budget} id="rc-tokens">
                <input
                  id="rc-tokens"
                  type="number"
                  className={inputCls}
                  min={1}
                  value={f.resources.token_budget}
                  onChange={(e) => setResources("token_budget", num(e.target.value))}
                />
              </Field>
            </div>
          </SectionCard>

          {/* Manager selection */}
          <SectionCard title="Manager" badge="Select one or both">
            <ManagerSelector
              value={f.manager_types}
              onChange={(v) => setF((p) => ({ ...p, manager_types: v }))}
              error={errors.manager_types}
            />
          </SectionCard>

          {/* Submit bar */}
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            {submitError && (
              <div className="mb-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{submitError}</span>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <button
                id="btn-run-experiment"
                type="button"
                disabled={!isValid || submitting}
                onClick={handleSubmit}
                className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-all hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {submitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <FilePlus2 className="h-4 w-4" />
                )}
                {submitting ? "Submitting…" : "Run experiment"}
              </button>

              <button
                id="btn-reset-form"
                type="button"
                onClick={handleReset}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 transition-colors"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Reset
              </button>

              <button
                id="btn-toggle-preview"
                type="button"
                onClick={() => setShowPreview((s) => !s)}
                className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2.5 text-xs font-medium text-slate-500 hover:bg-slate-50 transition-colors"
              >
                {showPreview ? "Hide" : "Show"} JSON preview
                <ChevronRight
                  className={`h-3 w-3 transition-transform ${showPreview ? "rotate-90" : ""}`}
                />
              </button>

              <div className="w-full">
                {isValid ? (
                  <span className="flex items-center gap-1.5 text-xs font-medium text-green-600">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Configuration valid — ready to submit
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5 text-xs text-slate-400">
                    <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                    Fix highlighted fields before submitting
                  </span>
                )}
              </div>
            </div>

            {showPreview && (
              <pre className="mt-4 max-h-96 overflow-auto rounded-lg bg-slate-950 p-4 text-[11px] text-green-400 font-mono leading-relaxed">
                {JSON.stringify(buildConfig(), null, 2)}
              </pre>
            )}
          </div>
        </div>

        {/* ---- Right column: summary ---- */}
        <aside className="space-y-5">
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="mb-3 text-sm font-semibold text-slate-800">
              Configuration summary
            </h3>
            <ConfigSummary f={f} />
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="mb-3 text-sm font-semibold text-slate-800">
              Controlled variables
            </h3>
            <dl className="space-y-2">
              {[
                ["Workload & tasks", "identical"],
                ["Random seed", "identical"],
                ["Agent pool", "identical"],
                ["Hardware / budget", "identical"],
                ["Manager policy", "varies"],
              ].map(([label, val]) => (
                <div key={label} className="flex justify-between text-xs">
                  <dt className="text-slate-500">{label}</dt>
                  <dd
                    className={`font-semibold ${
                      val === "varies" ? "text-indigo-600" : "text-slate-700"
                    }`}
                  >
                    {val}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
            <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-amber-700">
              <AlertTriangle className="h-3.5 w-3.5" />
              Simulation not yet implemented
            </div>
            <p className="text-xs text-amber-600 leading-relaxed">
              Submitting will persist the configuration and return an experiment
              ID. The actual simulation engine will pick it up once available.
            </p>
          </div>
        </aside>
      </div>
    </>
  );
}
