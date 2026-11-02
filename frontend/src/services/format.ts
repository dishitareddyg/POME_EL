export const fmtMs = (n: number) => `${Math.round(n).toLocaleString("en-US")} ms`;
export const fmtNum = (n: number, d = 1) => n.toFixed(d);
export const fmtPct = (n: number, d = 1) => `${n.toFixed(d)}%`;
export const fmtDate = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
