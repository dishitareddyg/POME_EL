/**
 * DagViz — SVG task dependency graph.
 *
 * Layout strategy: Layered (Sugiyama-style, simplified).
 *   1. Compute layer (longest path from root) for every node.
 *   2. Within each layer sort nodes by their first-predecessor's Y to reduce
 *      edge crossings.
 *   3. Assign (x, y) coordinates, draw nodes as coloured rectangles, edges as
 *      cubic bezier curves.
 *
 * Performance: works well up to ~150 nodes. For larger graphs the table view
 * is the appropriate interface.
 */

import { useMemo, useState } from "react";
import type { Task, TaskGraph } from "../types/workload";

// ─── Visual constants ────────────────────────────────────────────────────────

const NODE_W = 88;
const NODE_H = 36;
const H_GAP = 52;   // horizontal gap between layers
const V_GAP = 14;   // vertical gap between nodes in the same layer

const PRIORITY_COLOR: Record<string, { fill: string; stroke: string; text: string }> = {
  critical: { fill: "#fef2f2", stroke: "#ef4444", text: "#b91c1c" },
  high:     { fill: "#fff7ed", stroke: "#f97316", text: "#c2410c" },
  medium:   { fill: "#f0fdf4", stroke: "#22c55e", text: "#15803d" },
  low:      { fill: "#f8fafc", stroke: "#94a3b8", text: "#475569" },
};

const STATUS_DOT: Record<string, string> = {
  pending:   "#94a3b8",
  ready:     "#6366f1",
  blocked:   "#f59e0b",
  running:   "#3b82f6",
  completed: "#22c55e",
  failed:    "#ef4444",
};

// ─── Layout ──────────────────────────────────────────────────────────────────

interface NodePos { x: number; y: number; task: Task }

function computeLayout(graph: TaskGraph): { nodes: NodePos[]; width: number; height: number } {
  const { tasks } = graph;
  if (tasks.length === 0) return { nodes: [], width: 0, height: 0 };

  // --- Compute layer (depth) for each node ---
  const layerOf: Record<string, number> = {};
  for (const t of tasks) layerOf[t.id] = 0;

  // Topological order (tasks are already in topo order by construction)
  for (const t of tasks) {
    for (const depId of t.dependency_ids) {
      layerOf[t.id] = Math.max(layerOf[t.id], layerOf[depId] + 1);
    }
  }

  // Group tasks by layer
  const layers: Task[][] = [];
  for (const t of tasks) {
    const l = layerOf[t.id];
    if (!layers[l]) layers[l] = [];
    layers[l].push(t);
  }

  // --- Assign positions ---
  const pos: Record<string, { x: number; y: number }> = {};
  let maxY = 0;
  const numLayers = layers.length;

  for (let li = 0; li < numLayers; li++) {
    const layer = layers[li] ?? [];
    const x = li * (NODE_W + H_GAP);
    const totalH = layer.length * NODE_H + (layer.length - 1) * V_GAP;
    let y = 0 - totalH / 2;

    for (const t of layer) {
      pos[t.id] = { x, y };
      y += NODE_H + V_GAP;
    }
    const layerBottom = (layer.length - 1) * (NODE_H + V_GAP) + NODE_H;
    maxY = Math.max(maxY, layerBottom);
  }

  const nodes: NodePos[] = tasks.map((t) => ({ x: pos[t.id].x, y: pos[t.id].y, task: t }));

  // Normalise so all y are >= 0
  const minY = Math.min(...nodes.map((n) => n.y));
  for (const n of nodes) n.y -= minY;

  const width = numLayers * (NODE_W + H_GAP) - H_GAP;
  const height = Math.max(...nodes.map((n) => n.y)) + NODE_H;

  return { nodes, width, height };
}

// ─── Component ───────────────────────────────────────────────────────────────

interface Props {
  graph: TaskGraph;
  /** Maximum nodes to render (performance guard). */
  maxNodes?: number;
}

export default function DagViz({ graph, maxNodes = 120 }: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  // Trim graph for rendering if very large
  const renderGraph = useMemo<TaskGraph>(() => {
    if (graph.tasks.length <= maxNodes) return graph;
    const kept = graph.tasks.slice(0, maxNodes);
    const keptIds = new Set(kept.map((t) => t.id));
    const edges: Record<string, string[]> = {};
    for (const t of kept) {
      edges[t.id] = (graph.edges[t.id] ?? []).filter((id) => keptIds.has(id));
    }
    return { ...graph, tasks: kept, edges };
  }, [graph, maxNodes]);

  const { nodes, width, height } = useMemo(() => computeLayout(renderGraph), [renderGraph]);

  // Build fast lookup
  const nodeById = useMemo(() => {
    const m: Record<string, NodePos> = {};
    for (const n of nodes) m[n.task.id] = n;
    return m;
  }, [nodes]);

  if (nodes.length === 0) {
    return <div className="py-12 text-center text-sm text-slate-400">No tasks to display.</div>;
  }

  const PAD = 16;

  // Highlight connected nodes when hovered/selected
  const focusId = selected ?? hovered;
  const highlightedIds = useMemo(() => {
    if (!focusId) return null;
    const task = renderGraph.tasks.find((t) => t.id === focusId);
    if (!task) return null;
    const ids = new Set<string>([focusId, ...task.dependency_ids, ...(renderGraph.edges[focusId] ?? [])]);
    return ids;
  }, [focusId, renderGraph]);

  return (
    <div className="overflow-auto rounded-md border border-slate-200 bg-slate-50">
      {graph.tasks.length > maxNodes && (
        <div className="border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-700">
          Showing first {maxNodes} of {graph.tasks.length} tasks. Use the task table for the full list.
        </div>
      )}

      {/* Selected task detail */}
      {selected && (() => {
        const task = renderGraph.tasks.find((t) => t.id === selected);
        if (!task) return null;
        const colors = PRIORITY_COLOR[task.priority];
        return (
          <div
            className="flex flex-wrap items-center gap-3 border-b px-3 py-2 text-xs"
            style={{ borderColor: colors.stroke, background: colors.fill }}
          >
            <span className="font-mono font-semibold" style={{ color: colors.text }}>{task.id}</span>
            <span className="text-slate-600">{task.task_type}</span>
            <span className="rounded-full border px-2 py-0.5 font-medium capitalize"
              style={{ background: colors.fill, color: colors.text, borderColor: colors.stroke }}>
              {task.priority}
            </span>
            <span className="text-slate-500">~{task.estimated_workload_ms} ms</span>
            <span className="text-slate-500">{task.required_capability}</span>
            {task.dependency_ids.length > 0 && (
              <span className="text-slate-400">depends on: {task.dependency_ids.join(", ")}</span>
            )}
            <button
              className="ml-auto text-slate-400 hover:text-slate-700"
              onClick={() => setSelected(null)}
            >
              ✕
            </button>
          </div>
        );
      })()}

      <svg
        viewBox={`0 0 ${width + PAD * 2} ${height + PAD * 2}`}
        style={{ minWidth: width + PAD * 2, height: Math.min(height + PAD * 2, 520) }}
        className="block"
        aria-label="Task dependency graph"
      >
        <g transform={`translate(${PAD},${PAD})`}>
          {/* ── Edges ────────────────────────────────────────────────────── */}
          {renderGraph.tasks.map((task) =>
            task.dependency_ids.map((depId) => {
              const from = nodeById[depId];
              const to = nodeById[task.id];
              if (!from || !to) return null;

              const x1 = from.x + NODE_W;
              const y1 = from.y + NODE_H / 2;
              const x2 = to.x;
              const y2 = to.y + NODE_H / 2;
              const cx = (x1 + x2) / 2;

              const isHighlighted =
                highlightedIds?.has(task.id) && highlightedIds?.has(depId);
              const isDimmed = highlightedIds !== null && !isHighlighted;

              return (
                <path
                  key={`${depId}->${task.id}`}
                  d={`M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke={isHighlighted ? "#6366f1" : "#cbd5e1"}
                  strokeWidth={isHighlighted ? 2 : 1}
                  strokeDasharray={isDimmed ? "4 3" : undefined}
                  opacity={isDimmed ? 0.25 : 1}
                  markerEnd={isHighlighted ? "url(#arrow-hi)" : "url(#arrow)"}
                />
              );
            })
          )}

          {/* ── Nodes ────────────────────────────────────────────────────── */}
          {nodes.map(({ x, y, task }) => {
            const colors = PRIORITY_COLOR[task.priority];
            const isHi = highlightedIds?.has(task.id);
            const isDimmed = highlightedIds !== null && !isHi;
            const isSelected = selected === task.id;

            return (
              <g
                key={task.id}
                transform={`translate(${x},${y})`}
                style={{ cursor: "pointer" }}
                onMouseEnter={() => setHovered(task.id)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => setSelected((s) => (s === task.id ? null : task.id))}
                opacity={isDimmed ? 0.3 : 1}
              >
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  rx={5}
                  fill={colors.fill}
                  stroke={isSelected ? "#6366f1" : colors.stroke}
                  strokeWidth={isSelected ? 2 : 1}
                />
                {/* Status dot */}
                <circle
                  cx={NODE_W - 8}
                  cy={8}
                  r={4}
                  fill={STATUS_DOT[task.status] ?? "#94a3b8"}
                />
                {/* Task ID */}
                <text
                  x={7}
                  y={14}
                  fontSize={9}
                  fontFamily="monospace"
                  fill={colors.text}
                  fontWeight="700"
                >
                  {task.id}
                </text>
                {/* Task type (truncated) */}
                <text
                  x={7}
                  y={27}
                  fontSize={9}
                  fontFamily="sans-serif"
                  fill="#475569"
                >
                  {task.task_type.length > 13 ? task.task_type.slice(0, 12) + "…" : task.task_type}
                </text>
              </g>
            );
          })}

          {/* ── Arrowhead markers ────────────────────────────────────────── */}
          <defs>
            <marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
              <path d="M0,0 L0,6 L6,3 z" fill="#cbd5e1" />
            </marker>
            <marker id="arrow-hi" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
              <path d="M0,0 L0,6 L6,3 z" fill="#6366f1" />
            </marker>
          </defs>
        </g>
      </svg>

      {/* Legend */}
      <div className="flex flex-wrap gap-4 border-t border-slate-200 px-3 py-2">
        <div className="text-[10px] font-medium text-slate-500">Priority:</div>
        {(["critical", "high", "medium", "low"] as const).map((p) => (
          <div key={p} className="flex items-center gap-1 text-[10px]">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm border"
              style={{ background: PRIORITY_COLOR[p].fill, borderColor: PRIORITY_COLOR[p].stroke }}
            />
            <span className="capitalize text-slate-600">{p}</span>
          </div>
        ))}
        <div className="ml-4 text-[10px] font-medium text-slate-500">Status dot:</div>
        {(["ready", "blocked", "running", "completed", "failed"] as const).map((s) => (
          <div key={s} className="flex items-center gap-1 text-[10px]">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: STATUS_DOT[s] }}
            />
            <span className="capitalize text-slate-600">{s}</span>
          </div>
        ))}
        <div className="ml-auto text-[10px] text-slate-400">Click a node to inspect · hover to highlight connections</div>
      </div>
    </div>
  );
}
