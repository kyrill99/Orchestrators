"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiBaseUrl, type AgentRun } from "@/lib/api";
import type { Task, TaskEdge, TaskStatus } from "@/lib/fixtures";
import { layoutGraph, mapApiTask, type ApiTask } from "@/lib/taskgraph-api";
import { cn } from "@/lib/utils";

const POLL_MS = 3000;
const WIDTH = 194;
const HEIGHT = 92;
const PAD = 8;
/** Accent used for agent activity elsewhere (graph node agent badge, Done). */
const ACCENT = "#5E6AD2";

/** Tasks and edges; a new object means the layout must be recomputed. */
type Structure = { key: string; tasks: Task[]; edges: TaskEdge[] };

type MiniGraphState = {
  /** False when the backend could not be reached. */
  online: boolean;
  structure: Structure;
  tasks: Map<string, { title: string; status: TaskStatus }>;
  /** taskId -> agent id of the active run. */
  running: Map<string, string>;
};

type BackendGraph = {
  tasks?: ApiTask[];
  edges?: TaskEdge[];
  activeRuns?: AgentRun[];
};

function structureKey(tasks: Task[], edges: TaskEdge[]) {
  return `${tasks.map((t) => t.id).join(",")}|${edges.map((e) => `${e.source}>${e.target}`).join(",")}`;
}

/** Keeps the previous structure object while ids and edges are unchanged. */
function toState(graph: BackendGraph | null, previous: MiniGraphState | null) {
  const tasks = (graph?.tasks ?? []).map(mapApiTask);
  const edges = (graph?.edges ?? []).map(({ id, source, target }) => ({
    id,
    source,
    target,
  }));
  const key = structureKey(tasks, edges);
  return {
    online: graph !== null,
    structure:
      previous && previous.structure.key === key
        ? previous.structure
        : { key, tasks, edges },
    tasks: new Map(
      tasks.map((t) => [t.id, { title: t.title, status: t.status }]),
    ),
    running: new Map(
      (graph?.activeRuns ?? []).map((run) => [run.taskId, run.agent.id]),
    ),
  } satisfies MiniGraphState;
}

/** Polls GET /api/taskgraph/graph every few seconds while the tab is visible. */
function useLiveGraph() {
  const [state, setState] = useState<MiniGraphState | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;

    const refresh = async () => {
      clearTimeout(timer);
      let graph: BackendGraph | null = null;
      try {
        const response = await fetch(`${apiBaseUrl()}/api/taskgraph/graph`);
        if (response.ok) graph = await response.json();
      } catch {
        graph = null;
      }
      if (cancelled) return;
      setState((previous) => toState(graph, previous));
      if (document.visibilityState === "visible") {
        timer = setTimeout(refresh, POLL_MS);
      }
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") refresh();
      else clearTimeout(timer);
    };

    refresh();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return state;
}

function statusStyle(status: TaskStatus) {
  switch (status) {
    case "closed":
      return { fill: ACCENT, stroke: ACCENT };
    case "running":
    case "in-review":
      return { fill: "#F2C94C", stroke: "#F2C94C" };
    default:
      return { fill: "var(--background)", stroke: "#a3a3a3" };
  }
}

/** Sidebar preview of the task graph; glows while agents are running. */
export function MiniGraph() {
  const router = useRouter();
  const graph = useLiveGraph();
  const [hovered, setHovered] = useState<string | null>(null);

  const structure = graph?.structure;
  const layout = useMemo(() => {
    if (!structure) return null;
    // Same layered top-to-bottom layout as the project graph, scaled to fit.
    const raw = Object.entries(layoutGraph(structure.tasks, structure.edges));
    if (raw.length === 0) return null;
    const xs = [...new Set(raw.map(([, p]) => p.x))];
    const ys = [...new Set(raw.map(([, p]) => p.y))];
    const [minX, maxX] = [Math.min(...xs), Math.max(...xs)];
    const [minY, maxY] = [Math.min(...ys), Math.max(...ys)];
    const innerW = WIDTH - PAD * 2;
    const innerH = HEIGHT - PAD * 2;
    const scale = (v: number, min: number, max: number, size: number) =>
      max === min ? size / 2 : ((v - min) / (max - min)) * size;
    const spacing = Math.min(
      xs.length > 1 ? innerW / (xs.length - 1) : innerW,
      ys.length > 1 ? innerH / (ys.length - 1) : innerH,
    );
    const radius = Math.max(1.5, Math.min(4, spacing * 0.32));
    const pos = new Map<string, { x: number; y: number }>();
    for (const [id, p] of raw) {
      pos.set(id, {
        x: PAD + scale(p.x, minX, maxX, innerW),
        y: PAD + scale(p.y, minY, maxY, innerH),
      });
    }
    const paths = structure.edges.flatMap(({ source, target }) => {
      const a = pos.get(source);
      const b = pos.get(target);
      if (!a || !b) return [];
      const mid = (a.y + b.y) / 2;
      return [
        {
          source,
          target,
          d: `M${a.x},${a.y} C${a.x},${mid} ${b.x},${mid} ${b.x},${b.y}`,
        },
      ];
    });
    return { pos, paths, radius };
  }, [structure]);

  if (!graph) {
    return <div className="h-[118px] rounded-lg border border-sidebar-border" />;
  }

  if (!layout) {
    // Same as the project graph view: no fixtures, just a quiet empty state.
    return (
      <Link
        href="/project"
        className="flex h-[118px] flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-sidebar-border text-[11px] text-muted-foreground transition-colors hover:border-foreground/15 hover:text-foreground"
      >
        <span className="flex gap-1.5" aria-hidden>
          <span className="size-1.5 rounded-full border border-current opacity-50" />
          <span className="size-1.5 rounded-full border border-current opacity-50" />
          <span className="size-1.5 rounded-full border border-current opacity-50" />
        </span>
        {graph.online ? "No tasks yet" : "Task graph offline"}
      </Link>
    );
  }

  const runningCount = graph.running.size;
  const active = runningCount > 0;
  const total = graph.tasks.size;
  const done = [...graph.tasks.values()].filter(
    (t) => t.status === "closed",
  ).length;
  const hoveredTask = hovered ? graph.tasks.get(hovered) : undefined;
  const hoveredPos = hovered ? layout.pos.get(hovered) : undefined;
  const r = layout.radius;

  return (
    <div
      role="link"
      tabIndex={0}
      aria-label={
        active
          ? `Task graph, ${runningCount} agents running. Open project`
          : "Task graph. Open project"
      }
      onClick={() => router.push("/project")}
      onKeyDown={(event) => {
        if (event.key === "Enter") router.push("/project");
      }}
      className={cn(
        "relative cursor-pointer overflow-visible rounded-lg border bg-background/60 transition-[border-color,box-shadow] duration-500",
        active
          ? "border-[#5E6AD2]/35 shadow-[0_0_0_1px_rgba(94,106,210,0.12),0_0_12px_rgba(94,106,210,0.22)] motion-safe:animate-[mini-graph-breathe_2.8s_ease-in-out_infinite]"
          : "border-sidebar-border hover:border-foreground/15",
      )}
    >
      <div className="flex h-6 items-center justify-between px-2 pt-1 text-[11px] text-muted-foreground">
        {active ? (
          <span className="flex items-center gap-1.5 font-medium text-[#5E6AD2]">
            <span className="relative flex size-1.5">
              <span className="absolute inline-flex size-full rounded-full bg-[#5E6AD2] opacity-60 motion-safe:animate-ping" />
              <span className="relative inline-flex size-1.5 rounded-full bg-[#5E6AD2]" />
            </span>
            {runningCount} {runningCount === 1 ? "agent" : "agents"} running
          </span>
        ) : (
          <span>Task graph</span>
        )}
        <span className="font-mono tabular-nums">
          {done}/{total}
        </span>
      </div>

      <svg
        width={WIDTH}
        height={HEIGHT}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="block"
        aria-hidden
      >
        <g fill="none" strokeWidth={0.75}>
          {layout.paths.map((p) => {
            const hot = graph.running.has(p.target);
            return (
              <path
                key={`${p.source}>${p.target}`}
                d={p.d}
                stroke={hot ? ACCENT : "#d4d4d4"}
                strokeOpacity={hot ? 0.55 : 1}
              />
            );
          })}
        </g>
        {graph.structure.tasks.map(({ id }) => {
          const p = layout.pos.get(id);
          const task = graph.tasks.get(id);
          if (!p || !task) return null;
          const running = graph.running.has(id);
          const style = statusStyle(task.status);
          return (
            <g
              key={id}
              className="cursor-pointer"
              onMouseEnter={() => setHovered(id)}
              onMouseLeave={() => setHovered((h) => (h === id ? null : h))}
              onClick={(event) => {
                event.stopPropagation();
                router.push(`/tasks/${encodeURIComponent(id)}`);
              }}
            >
              {running ? (
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={r + 1.5}
                  fill="none"
                  stroke={ACCENT}
                  strokeWidth={1}
                  className="origin-center opacity-50 [transform-box:fill-box] motion-safe:animate-[mini-graph-ping_1.8s_cubic-bezier(0,0,0.2,1)_infinite]"
                />
              ) : null}
              <circle
                cx={p.x}
                cy={p.y}
                r={hovered === id ? r + 0.75 : r}
                fill={running ? ACCENT : style.fill}
                stroke={running ? ACCENT : style.stroke}
                strokeWidth={1}
                className="transition-[r] duration-150"
              />
              {/* Larger invisible hit target for tiny nodes. */}
              <circle cx={p.x} cy={p.y} r={Math.max(r + 3, 6)} fill="transparent" />
            </g>
          );
        })}
      </svg>

      {hoveredTask && hoveredPos ? (
        <div
          role="tooltip"
          className="pointer-events-none absolute z-50 max-w-[180px] -translate-x-1/2 -translate-y-full truncate rounded-md bg-foreground px-2 py-1 text-[11px] text-background shadow-sm"
          style={{
            left: Math.min(Math.max(hoveredPos.x, 40), WIDTH - 40),
            top: 24 + hoveredPos.y - r - 6,
          }}
        >
          {hoveredTask.title}
        </div>
      ) : null}
    </div>
  );
}
