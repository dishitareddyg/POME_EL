import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

interface Props {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
  icon?: LucideIcon;
  accent?: string; // CSS color for the left rule, e.g. a manager color
}

export default function MetricCard({ label, value, unit, hint, icon: Icon, accent }: Props) {
  return (
    <div
      className="rounded-md border border-slate-200 border-l-4 bg-white px-3 py-2.5"
      style={accent ? { borderLeftColor: accent } : undefined}
    >
      <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
        <span className="truncate">{label}</span>
        {Icon && <Icon className="h-3.5 w-3.5 shrink-0" />}
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="truncate text-xl font-semibold tabular-nums text-slate-900">{value}</span>
        {unit && <span className="text-xs text-slate-500">{unit}</span>}
      </div>
      {hint && <div className="mt-0.5 truncate text-xs text-slate-500">{hint}</div>}
    </div>
  );
}
