import { useHealth } from "../hooks/useHealth";

export default function BackendStatus() {
  const { data, error, loading } = useHealth();
  const ok = !!data && !error;
  const color = loading ? "bg-slate-300" : ok ? "bg-emerald-500" : "bg-red-500";
  const text = loading ? "Checking backend…" : ok ? "Backend online" : "Backend offline";
  return (
    <div className="flex items-center gap-2 text-xs text-slate-500">
      <span className={`h-2 w-2 rounded-full ${color}`} />
      {text}
    </div>
  );
}
