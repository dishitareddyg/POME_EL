import { FlaskConical, Menu, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import BackendStatus from "../components/BackendStatus";
import StatusBadge from "../components/StatusBadge";
import { NAV_ITEMS } from "../data/navigation";
import type { RouteId } from "../hooks/useHashRoute";

interface Props {
  children: ReactNode;
  route?: RouteId;
}

function Nav({ route, onNavigate }: { route: RouteId; onNavigate: () => void }) {
  return (
    <nav className="flex-1 space-y-0.5 p-2">
      {NAV_ITEMS.map(({ id, label, href, icon: Icon }) => {
        const active = id === route;
        return (
          <a
            key={id}
            href={href}
            onClick={onNavigate}
            className={`flex items-center gap-2.5 rounded px-2.5 py-1.5 text-sm ${
              active ? "bg-indigo-50 font-medium text-indigo-700" : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </a>
        );
      })}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5 border-b border-slate-200 px-4 py-3">
      <FlaskConical className="h-5 w-5 text-indigo-600" />
      <div className="leading-tight">
        <div className="text-sm font-semibold">MAS Manager Simulator</div>
        <div className="text-[11px] text-slate-500">Conventional vs POSDCORB</div>
      </div>
    </div>
  );
}

export default function MainLayout({ children, route = "dashboard" }: Props) {
  const [open, setOpen] = useState(false);
  const current = NAV_ITEMS.find((n) => n.id === route);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      {/* Persistent sidebar (lg+) */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-56 flex-col border-r border-slate-200 bg-white lg:flex">
        <Brand />
        <Nav route={route} onNavigate={() => undefined} />
        <div className="border-t border-slate-200 px-4 py-3">
          <BackendStatus />
        </div>
      </aside>

      {/* Mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-slate-900/30" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-60 flex-col bg-white">
            <Brand />
            <Nav route={route} onNavigate={() => setOpen(false)} />
            <div className="border-t border-slate-200 px-4 py-3">
              <BackendStatus />
            </div>
          </aside>
        </div>
      )}

      <div className="lg:pl-56">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
          <button
            className="rounded p-1 text-slate-600 hover:bg-slate-100 lg:hidden"
            onClick={() => setOpen((o) => !o)}
            aria-label="Toggle navigation"
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
          <h1 className="text-sm font-semibold">{current?.label ?? "Dashboard"}</h1>
          <div className="ml-auto flex items-center gap-2">
            <StatusBadge tone="amber" label="Mock data" />
          </div>
        </header>
        <main className="mx-auto max-w-7xl space-y-5 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
