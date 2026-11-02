import type { ReactNode } from "react";

interface Props {
  title: string;
  subtitle?: string;
  height?: number;
  actions?: ReactNode;
  children: ReactNode; // typically a Recharts chart inside <ResponsiveContainer>
}

export default function ChartCard({ title, subtitle, height = 240, actions, children }: Props) {
  return (
    <section className="rounded-md border border-slate-200 bg-white">
      <header className="flex items-start justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
        </div>
        {actions}
      </header>
      <div className="px-2 pb-2 pt-3" style={{ height }}>
        {children}
      </div>
    </section>
  );
}
