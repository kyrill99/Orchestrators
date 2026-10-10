"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { apiBaseUrl, type AgentRun } from "@/lib/api";
import {
  edges as fixtureEdges,
  tasks as fixtureTasks,
  type TaskStatus,
} from "@/lib/fixtures";
import { layeredLayout, type LayoutEdge } from "@/lib/mini-graph-layout";
import { cn } from "@/lib/utils";

const POLL_MS = 3000;
const WIDTH = 194;
const HEIGHT = 92;
const PAD = 8;
/** Accent used for agent activity elsewhere (graph node agent badge, Done). */
const ACCENT = "#5E6AD2";

type MiniTask = { id: string; title: string; status: TaskStatus };

/** Ids and edges only; a new object means the layout must be recomputed. */
type Structure = { key: string; ids: string[]; edges: LayoutEdge[] };

type MiniGraphState = {
  structure: Structure;
  tasks: Map<string, MiniTask>;
  /** taskId -> agent id of the active run. */
  running: Map<string, string>;
};

type BackendGraph = {
  tasks?: MiniTask[];
  edges?: LayoutEdge[];
  activeRuns?: AgentRun[];
};

function structureKey(ids: string[], edges: LayoutEdge[]) {
  return `${ids.join(",")}|${edges.map((e) => `${e.source}>${e.target}`).join(",")}`;
}

/**
 * Like the project graph view, fall back to the demo fixtures while the
 * backend graph is empty or unreachable.
 */
function toState(graph: BackendGraph | null, previous: MiniGraphState | null) {
  const live = graph?.tasks?.length ? graph : null;
  const tasks = live?.tasks ?? fixtureTasks;
  const edges = (live?.edges ?? fixtureEdges).map(({ source, target }) => ({
    source,
    target,
  }));
  const ids = tasks.map((t) => t.id);
  const key = structureKey(ids, edges);
  return {
    structure:
      previous && previous.structure.key === key
        ? previous.structure
        : { key, ids, edges },
    tasks: new Map(
      tasks.map((t) => [t.id, { id: t.id, title: t.title, status: t.status }]),
    ),
    running: new Map(
      (live?.activeRuns ?? []).map((run) => [run.taskId, run.agent.id]),
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
    const { points, layers, maxLayerSize } = layeredLayout(
      structure.ids,
      structure.edges,
    );
    const innerW = WIDTH - PAD * 2;
    const innerH = HEIGHT - PAD * 2;
    const spacing = Math.min(
      layers > 1 ? innerW / (layers - 1) : innerW,
      innerH / maxLayerSize,
    );
    const radius = Math.max(1.5, Math.min(4, spacing * 0.32));
    const pos = new Map<string, { x: number; y: number }>();
    for (const [id, p] of points) {
      pos.set(id, { x: PAD + p.x * innerW, y: PAD + p.y * innerH });
    }
    const paths = structure.edges.flatMap(({ source, target }) => {
      const a = pos.get(source);
      const b = pos.get(target);
      if (!a || !b) return [];
      const mid = (a.x + b.x) / 2;
      return [
        {
          source,
          target,
          d: `M${a.x},${a.y} C${mid},${a.y} ${mid},${b.y} ${b.x},${b.y}`,
        },
      ];
    });
    return { pos, paths, radius };
  }, [structure]);

  if (!graph || !layout) {
    return <div className="h-[118px] rounded-lg border border-sidebar-border" />;
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
        {graph.structure.ids.map((id) => {
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
