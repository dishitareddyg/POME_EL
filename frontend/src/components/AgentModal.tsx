/**
 * AgentModal — Add or Edit an agent.
 *
 * Covers all mutable Agent fields:
 *   name, role, capabilities, processing_speed,
 *   reliability, max_concurrent_tasks, origin
 *
 * Runtime fields (status, cpu_usage, etc.) are set by the simulation engine
 * and are therefore not editable here.
 */

import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Agent, AgentOrigin, AgentRole, Capability } from "../types/agent";

const ROLES: AgentRole[] = [
  "Researcher",
  "Analyst",
  "Processor",
  "Validator",
  "Summarizer",
  "General Worker",
];

const PRESET_CAPABILITIES: Record<AgentRole, string[]> = {
  Researcher: ["literature-search", "web-search", "citation-extraction", "fact-checking", "data-collection", "api-integration"],
  Analyst: ["statistics", "data-visualization", "trend-analysis", "regression", "report-writing", "ml-analysis", "forecasting", "anomaly-detection"],
  Processor: ["text-processing", "parsing", "batch-execution", "format-conversion", "pipeline-execution", "parallel-processing", "caching"],
  Validator: ["quality-check", "schema-validation", "output-review"],
  Summarizer: ["text-summarization", "key-extraction", "abstract-writing"],
  "General Worker": ["general-tasks", "fallback-execution", "routing"],
};

interface Props {
  /** Existing agent to edit; undefined means "add new". */
  agent?: Agent;
  onSave: (payload: Omit<Agent, "id" | "current_workload" | "status" | "cpu_usage" | "tasks_completed" | "tasks_failed" | "created_at">) => void;
  onClose: () => void;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );
}

const INPUT_CLS =
  "rounded border border-slate-300 px-2.5 py-1.5 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-400 transition";

export default function AgentModal({ agent, onSave, onClose }: Props) {
  const isEdit = Boolean(agent);
  const dialogRef = useRef<HTMLDivElement>(null);

  const [name, setName] = useState(agent?.name ?? "");
  const [role, setRole] = useState<AgentRole>(agent?.role ?? "General Worker");
  const [capabilities, setCapabilities] = useState<Capability[]>(agent?.capabilities ?? []);
  const [capInput, setCapInput] = useState("");
  const [processingSpeed, setProcessingSpeed] = useState(agent?.processing_speed ?? 1.0);
  const [reliability, setReliability] = useState(agent?.reliability ?? 0.95);
  const [maxConcurrent, setMaxConcurrent] = useState(agent?.max_concurrent_tasks ?? 3);
  const [origin, setOrigin] = useState<AgentOrigin>(agent?.origin ?? "initial");
  const [error, setError] = useState<string | null>(null);

  // When role changes pre-fill capabilities if empty
  useEffect(() => {
    if (!isEdit && capabilities.length === 0) {
      setCapabilities(PRESET_CAPABILITIES[role].slice(0, 3));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role]);

  // Close on backdrop click
  const handleBackdrop = (e: React.MouseEvent) => {
    if (dialogRef.current && !dialogRef.current.contains(e.target as Node)) {
      onClose();
    }
  };

  const addCap = () => {
    const v = capInput.trim().toLowerCase().replace(/\s+/g, "-");
    if (!v) return;
    if (capabilities.includes(v)) { setCapInput(""); return; }
    setCapabilities((prev) => [...prev, v]);
    setCapInput("");
  };

  const removeCap = (cap: Capability) => setCapabilities((prev) => prev.filter((c) => c !== cap));

  const togglePreset = (cap: string) => {
    if (capabilities.includes(cap)) removeCap(cap);
    else setCapabilities((prev) => [...prev, cap]);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { setError("Name is required."); return; }
    if (capabilities.length === 0) { setError("Add at least one capability."); return; }
    setError(null);
    onSave({ name: name.trim(), role, capabilities, processing_speed: processingSpeed, reliability, max_concurrent_tasks: maxConcurrent, origin });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-[2px]"
      onClick={handleBackdrop}
    >
      <div
        ref={dialogRef}
        className="relative w-full max-w-lg rounded-lg border border-slate-200 bg-white shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label={isEdit ? "Edit agent" : "Add agent"}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3.5">
          <h2 className="text-sm font-semibold text-slate-900">{isEdit ? "Edit agent" : "Add agent"}</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="max-h-[80vh] overflow-y-auto">
          <div className="space-y-4 px-5 py-4">

            {/* Name + Role row */}
            <div className="grid grid-cols-2 gap-3">
              <Field label="Name">
                <input
                  id="agent-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Researcher-4"
                  className={INPUT_CLS}
                />
              </Field>
              <Field label="Role">
                <select
                  id="agent-role"
                  value={role}
                  onChange={(e) => setRole(e.target.value as AgentRole)}
                  className={INPUT_CLS}
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>{r}</option>
                  ))}
                </select>
              </Field>
            </div>

            {/* Origin */}
            <Field label="Origin">
              <div className="flex gap-3">
                {(["initial", "dynamic"] as AgentOrigin[]).map((o) => (
                  <label key={o} className="flex cursor-pointer items-center gap-1.5 text-sm text-slate-700">
                    <input
                      type="radio"
                      name="origin"
                      value={o}
                      checked={origin === o}
                      onChange={() => setOrigin(o)}
                      className="accent-indigo-600"
                    />
                    <span className="capitalize">{o}</span>
                    <span className="text-xs text-slate-400">
                      {o === "initial" ? "(both managers)" : "(POSDCORB only)"}
                    </span>
                  </label>
                ))}
              </div>
            </Field>

            {/* Processing speed */}
            <Field label={`Processing speed — ${processingSpeed.toFixed(2)}\u00d7`}>
              <input
                id="agent-speed"
                type="range"
                min={0.1}
                max={5.0}
                step={0.05}
                value={processingSpeed}
                onChange={(e) => setProcessingSpeed(Number(e.target.value))}
                className="w-full accent-indigo-600"
              />
              <div className="flex justify-between text-[10px] text-slate-400">
                <span>0.1\u00d7 (slow)</span>
                <span>1.0\u00d7 (baseline)</span>
                <span>5.0\u00d7 (fast)</span>
              </div>
            </Field>

            {/* Reliability */}
            <Field label={`Reliability — ${(reliability * 100).toFixed(1)}%`}>
              <input
                id="agent-reliability"
                type="range"
                min={0.5}
                max={1.0}
                step={0.01}
                value={reliability}
                onChange={(e) => setReliability(Number(e.target.value))}
                className="w-full accent-emerald-500"
              />
              <div className="flex justify-between text-[10px] text-slate-400">
                <span>50% (unreliable)</span>
                <span>95%</span>
                <span>100% (perfect)</span>
              </div>
            </Field>

            {/* Max concurrent tasks */}
            <Field label={`Max concurrent tasks — ${maxConcurrent}`}>
              <input
                id="agent-concurrency"
                type="range"
                min={1}
                max={16}
                step={1}
                value={maxConcurrent}
                onChange={(e) => setMaxConcurrent(Number(e.target.value))}
                className="w-full accent-amber-500"
              />
              <div className="flex justify-between text-[10px] text-slate-400">
                <span>1</span>
                <span>8</span>
                <span>16</span>
              </div>
            </Field>

            {/* Capabilities */}
            <Field label="Capabilities">
              <div className="space-y-2">
                {/* Preset chips for the selected role */}
                <div className="flex flex-wrap gap-1.5">
                  {PRESET_CAPABILITIES[role].map((cap) => {
                    const active = capabilities.includes(cap);
                    return (
                      <button
                        key={cap}
                        type="button"
                        onClick={() => togglePreset(cap)}
                        className={`rounded-full border px-2 py-0.5 text-xs transition ${
                          active
                            ? "border-indigo-500 bg-indigo-50 text-indigo-700"
                            : "border-slate-300 text-slate-500 hover:border-indigo-300"
                        }`}
                      >
                        {cap}
                      </button>
                    );
                  })}
                </div>

                {/* Custom capability input */}
                <div className="flex gap-2">
                  <input
                    value={capInput}
                    onChange={(e) => setCapInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCap(); } }}
                    placeholder="Add custom capability..."
                    className={`${INPUT_CLS} flex-1`}
                  />
                  <button
                    type="button"
                    onClick={addCap}
                    className="rounded border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 transition"
                  >
                    Add
                  </button>
                </div>

                {/* Selected tags */}
                <div className="flex flex-wrap gap-1.5">
                  {capabilities.map((cap) => (
                    <span
                      key={cap}
                      className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700"
                    >
                      {cap}
                      <button
                        type="button"
                        onClick={() => removeCap(cap)}
                        className="text-slate-400 hover:text-red-500 transition"
                        aria-label={`Remove ${cap}`}
                      >
                        &times;
                      </button>
                    </span>
                  ))}
                  {capabilities.length === 0 && (
                    <span className="text-xs text-slate-400">No capabilities selected.</span>
                  )}
                </div>
              </div>
            </Field>

            {error && <p className="text-xs text-red-600">{error}</p>}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 border-t border-slate-100 px-5 py-3">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 transition"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="rounded bg-indigo-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 transition"
            >
              {isEdit ? "Save changes" : "Add agent"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
