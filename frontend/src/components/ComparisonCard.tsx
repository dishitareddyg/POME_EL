import { MANAGER_COLORS, MANAGER_SHORT } from "../data/managers";
import { pctDelta } from "../services/selectors";
import type { ManagerType } from "../types/experiment";

interface Props {
  label: string;
  unit?: string;
  conventional: number;
  posdcorb: number;
  lowerIsBetter?: boolean;
  decimals?: number;
}

export default function ComparisonCard({
  label,
  unit,
  conventional,
  posdcorb,
  lowerIsBetter = false,
  decimals = 1,
}: Props) {
  const max = Math.max(conventional, posdcorb, 1e-9);
  const delta = pctDelta(conventional, posdcorb);
  const improved = lowerIsBetter ? posdcorb < conventional : posdcorb > conventional;
  const rows: [ManagerType, number][] = [
    ["conventional", conventional],
    ["posdcorb", posdcorb],
  ];

  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2.5">
      <div className="mb-2 text-xs text-slate-500">{label}</div>
      <div className="space-y-1.5">
        {rows.map(([m, v]) => (
          <div key={m}>
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-slate-600">{MANAGER_SHORT[m]}</span>
              <span className="font-semibold tabular-nums text-slate-900">
                {v.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}
                {unit && <span className="ml-0.5 font-normal text-slate-500">{unit}</span>}
              </span>
            </div>
            <div className="mt-0.5 h-1.5 rounded-sm bg-slate-100">
              <div
                className="h-1.5 rounded-sm"
                style={{ width: `${(v / max) * 100}%`, backgroundColor: MANAGER_COLORS[m] }}
              />
            </div>
          </div>
        ))}
      </div>
      <div className={`mt-2 text-xs font-medium ${improved ? "text-emerald-700" : "text-red-700"}`}>
        {delta >= 0 ? "+" : "−"}
        {Math.abs(delta).toFixed(1)}% POSDCORB vs conventional
      </div>
    </div>
  );
}
