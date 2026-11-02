import { useEffect, useState } from "react";

export type RouteId = "dashboard" | "new" | "agents" | "workload" | "simulation" | "results" | "history" | "comparison" | "analysis" | "ablation";

const VALID: RouteId[] = ["dashboard", "new", "agents", "workload", "simulation", "results", "history", "comparison", "analysis", "ablation"];

export interface RouteState {
  route: RouteId;
  param: string | null;
}

function parse(): RouteState {
  const [first, second] = window.location.hash.replace(/^#\/?/, "").split("/");
  const route = (VALID as string[]).includes(first) ? (first as RouteId) : "dashboard";
  return { route, param: second ?? null };
}

export function useHashRoute(): RouteState {
  const [state, setState] = useState<RouteState>(parse);
  useEffect(() => {
    const onChange = () => setState(parse());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return state;
}
