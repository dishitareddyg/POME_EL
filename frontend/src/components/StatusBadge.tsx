import type { ExperimentStatus } from "../types/experiment";

type Tone = "green" | "red" | "blue" | "amber" | "slate";

const TONE_CLASS: Record<Tone, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  red: "bg-red-50 text-red-700 ring-red-200",
  blue: "bg-blue-50 text-blue-700 ring-blue-200",
  amber: "bg-amber-50 text-amber-700 ring-amber-200",
  slate: "bg-slate-100 text-slate-600 ring-slate-200",
};

const STATUS_TONE: Record<ExperimentStatus, Tone> = {
  pending: "slate",
  running: "blue",
  completed: "green",
  failed: "red",
};

interface Props {
  label?: string;
  /** Legacy boolean form: true = green, false = red. */
  ok?: boolean;
  /** Experiment status; picks tone and default label. */
  status?: ExperimentStatus;
  tone?: Tone;
}

export default function StatusBadge({ label, ok, status, tone }: Props) {
  const resolved: Tone = tone ?? (status ? STATUS_TONE[status] : ok === false ? "red" : ok ? "green" : "slate");
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_CLASS[resolved]}`}
    >
      {label ?? status ?? ""}
    </span>
  );
}
