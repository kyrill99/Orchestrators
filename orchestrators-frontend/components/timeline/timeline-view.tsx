"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Ban } from "lucide-react";
import { getAssignee, loadAssignee } from "@/lib/assignees";
import type { Task, TaskStatus } from "@/lib/fixtures";
import { useTaskGraph } from "@/lib/use-task-graph";
import { cn } from "@/lib/utils";
import { AssigneeAvatar } from "@/components/task/assignee-picker";
import { StatusIcon } from "@/components/task/status-icon";
import { AvatarStack } from "@/components/presence/presence";
import { presenceColor, useTaskPresence } from "@/lib/presence";

const DAY_MS = 86_400_000;
const MIN_DAY_WIDTH = 36;
const MIN_RANGE_DAYS = 28;
const ROW_HEIGHT = 72;
const HEADER_HEIGHT = 56;
const CHART_PAD_X = 24; // px-6
const MIN_BAR_WIDTH = 200;
const BAR_HEIGHT = 36; // h-9
/** Vertical center of a bar within its row. */
const BAR_MID_Y = ROW_HEIGHT / 2;
/** Indent from the start of the blocker (not its end). */
const DEP_CASCADE_INDENT = 48;
/** Blocked-by links: Linear-ish red. */
const DEP_STROKE = "#E5484D";
const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

const MONTH_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

const statusAccent: Record<TaskStatus, string> = {
  open: "",
  running: "ring-1 ring-inset ring-sky-500/30",
  "in-review": "ring-1 ring-inset ring-amber-500/30",
  closed: "ring-1 ring-inset ring-emerald-500/25",
};

function parseDay(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

function addDays(utc: number, days: number) {
  return utc + days * DAY_MS;
}

function daysBetween(a: number, b: number) {
  return Math.round((b - a) / DAY_MS);
}

function buildRange(items: Task[], todayIso: string) {
  const today = parseDay(todayIso);
  if (!items.length) {
    return {
      rangeStart: addDays(today, -7),
      rangeEnd: addDays(today, 21),
      totalDays: 28,
    };
  }
  const starts = items.map((t) => parseDay(t.startDate));
  const ends = items.map((t) => parseDay(t.endDate));
  let rangeStart = addDays(Math.min(...starts, today), -3);
  let rangeEnd = addDays(Math.max(...ends, today), 5);
  if (daysBetween(rangeStart, rangeEnd) < MIN_RANGE_DAYS) {
    rangeEnd = addDays(rangeStart, MIN_RANGE_DAYS);
  }
  return {
    rangeStart,
    rangeEnd,
    totalDays: daysBetween(rangeStart, rangeEnd),
  };
}

function dependencyDepths(tasks: Task[]): Map<string, number> {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  function walk(id: string): number {
    if (depth.has(id)) return depth.get(id)!;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const task = byId.get(id);
    const d =
      !task || task.blockedBy.length === 0
        ? 0
        : 1 + Math.max(0, ...task.blockedBy.map(walk));
    visiting.delete(id);
    depth.set(id, d);
    return d;
  }
  for (const t of tasks) walk(t.id);
  return depth;
}

function orderTasks(tasks: Task[], depth: Map<string, number>): Task[] {
  return [...tasks].sort(
    (a, b) =>
      (depth.get(a.id) ?? 0) - (depth.get(b.id) ?? 0) ||
      a.title.localeCompare(b.title),
  );
}

function monthSpans(rangeStart: number, rangeEnd: number) {
  const spans: { label: string; start: number }[] = [];
  let cursor = Date.UTC(
    new Date(rangeStart).getUTCFullYear(),
    new Date(rangeStart).getUTCMonth(),
    1,
  );
  while (cursor < rangeEnd) {
    const year = new Date(cursor).getUTCFullYear();
    const month = new Date(cursor).getUTCMonth();
    const next = Date.UTC(year, month + 1, 1);
    const clampedStart = Math.max(cursor, rangeStart);
    if (clampedStart < rangeEnd) {
      spans.push({
        label: MONTH_NAMES[month],
        start: daysBetween(rangeStart, clampedStart),
      });
    }
    cursor = next;
  }
  return spans;
}

function weekTicks(rangeStart: number, totalDays: number) {
  const ticks: { offset: number; day: number }[] = [];
  for (let i = 0; i <= totalDays; i++) {
    const date = new Date(addDays(rangeStart, i));
    if (date.getUTCDay() === 1 || i === 0) {
      ticks.push({ offset: i, day: date.getUTCDate() });
    }
  }
  return ticks;
}

/** Rough content width so long titles stay visible without clipping the card. */
function estimateBarContentWidth(task: Task) {
  const titleW = task.title.length * 7.2;
  return 20 + 14 + titleW + 28 + 20;
}

type Pt = { x: number; y: number };

/** Point on a cubic Bézier at t ∈ [0, 1]. */
function cubicAt(p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: number): Pt {
  const u = 1 - t;
  return {
    x:
      u * u * u * p0.x +
      3 * u * u * t * p1.x +
      3 * u * t * t * p2.x +
      t * t * t * p3.x,
    y:
      u * u * u * p0.y +
      3 * u * u * t * p1.y +
      3 * u * t * t * p2.y +
      t * t * t * p3.y,
  };
}

export function TimelineView() {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const todayRef = useRef<HTMLDivElement>(null);
  const [viewportWidth, setViewportWidth] = useState(0);
  const { data, loading, error } = useTaskGraph();
  // Teammates with a task open (avatars) or editing it (ring in their color).
  const taskPresence = useTaskPresence();
  const todayIso = useMemo(() => new Date().toISOString().slice(0, 10), []);

  const depths = useMemo(
    () => dependencyDepths(data?.tasks ?? []),
    [data?.tasks],
  );
  const ordered = useMemo(
    () => orderTasks(data?.tasks ?? [], depths),
    [data?.tasks, depths],
  );

  const { rangeStart, rangeEnd, totalDays } = buildRange(ordered, todayIso);
  const months = monthSpans(rangeStart, rangeEnd);
  const ticks = weekTicks(rangeStart, totalDays);
  const todayOffset = daysBetween(rangeStart, parseDay(todayIso));
  const todayDate = new Date(parseDay(todayIso));
  const todayLabel = `${MONTH_SHORT[todayDate.getUTCMonth()]} ${todayDate.getUTCDate()}`;

  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const measure = () => setViewportWidth(scroller.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(scroller);
    return () => observer.disconnect();
  }, [ordered.length]);

  const available =
    viewportWidth > 0 ? Math.max(viewportWidth - CHART_PAD_X * 2, 320) : 0;

  const dayWidth = Math.max(
    MIN_DAY_WIDTH,
    available > 0 ? available / totalDays : MIN_DAY_WIDTH,
  );

  const bars = useMemo(() => {
    // Depth order so we can cascade each dependent to start after its blockers.
    const draft = ordered.map((task, index) => {
      const start = daysBetween(rangeStart, parseDay(task.startDate));
      const duration =
        daysBetween(parseDay(task.startDate), parseDay(task.endDate)) + 1;
      const scheduleWidth = Math.max(duration * dayWidth, MIN_BAR_WIDTH);
      const width = Math.max(scheduleWidth, estimateBarContentWidth(task));
      return {
        task,
        index,
        start,
        width,
        left: start * dayWidth,
        midY: index * ROW_HEIGHT + BAR_MID_Y,
      };
    });
    const byId = new Map(draft.map((b) => [b.task.id, b]));
    for (const bar of draft) {
      let left = bar.left;
      for (const blockerId of bar.task.blockedBy) {
        const blocker = byId.get(blockerId);
        if (!blocker) continue;
        left = Math.max(left, blocker.left + DEP_CASCADE_INDENT);
      }
      bar.left = left;
    }
    return draft;
  }, [ordered, rangeStart, dayWidth]);

  const contentWidth = Math.max(
    totalDays * dayWidth,
    ...bars.map((b) => b.left + b.width),
    available,
  );

  /**
   * Always back (right) of blocker → front (left) of blocked.
   * Cascade layout keeps that hop short so curves stay readable.
   */
  const depPaths = useMemo(() => {
    const byId = new Map(bars.map((b) => [b.task.id, b]));
    const seen = new Set<string>();
    const siblingOrd = new Map<string, number>();
    const paths: {
      id: string;
      d: string;
      midX: number;
      midY: number;
      label: string;
    }[] = [];

    const add = (blockerId: string, blockedId: string, edgeId: string) => {
      const key = `${blockerId}>${blockedId}`;
      if (seen.has(key)) return;
      const blocker = byId.get(blockerId);
      const blocked = byId.get(blockedId);
      if (!blocker || !blocked) return;
      seen.add(key);

      const ord = siblingOrd.get(blockerId) ?? 0;
      siblingOrd.set(blockerId, ord + 1);

      // Back of blocker → front of blocked. Control points stay on each
      // endpoint’s x and pull almost the full dy so the S is nearly
      // vertical in the ~48px cascade gap.
      const p0: Pt = {
        x: blocker.left + blocker.width,
        y: blocker.midY,
      };
      const p3: Pt = { x: blocked.left, y: blocked.midY };
      const dy = p3.y - p0.y;
      const fan = ord * 3;
      const p1: Pt = {
        x: p0.x + fan,
        y: p0.y + dy * 0.95,
      };
      const p2: Pt = {
        x: p3.x + fan,
        y: p3.y - dy * 0.95,
      };

      const mid = cubicAt(p0, p1, p2, p3, 0.5);
      const d = `M ${p0.x} ${p0.y} C ${p1.x} ${p1.y}, ${p2.x} ${p2.y}, ${p3.x} ${p3.y}`;

      paths.push({
        id: edgeId,
        d,
        midX: mid.x,
        midY: mid.y,
        label: `Blocked by ${blocker.task.title}`,
      });
    };

    for (const edge of data?.edges ?? []) {
      add(edge.source, edge.target, edge.id);
    }
    for (const bar of bars) {
      for (const blocker of bar.task.blockedBy) {
        add(blocker, bar.task.id, `blocked-${blocker}-${bar.task.id}`);
      }
    }
    return paths;
  }, [bars, data?.edges]);

  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    const today = todayRef.current;
    if (!scroller || !today || viewportWidth === 0) return;
    const target = today.offsetLeft - scroller.clientWidth * 0.35;
    scroller.scrollLeft = Math.max(0, target);
  }, [todayOffset, contentWidth, viewportWidth]);

  if (loading && !data) {
    return (
      <div className="flex h-full items-center justify-center bg-canvas text-[13px] text-muted-foreground">
        Loading timeline…
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="flex h-full items-center justify-center bg-canvas px-6 text-center text-[13px] text-red-600">
        {error}
      </div>
    );
  }

  if (!ordered.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 bg-canvas px-6 text-center">
        <p className="text-[13px] font-medium text-foreground">No schedule yet</p>
        <p className="max-w-sm text-xs text-muted-foreground">
          Once the Agent drafts the project graph, tasks will show up here.
        </p>
      </div>
    );
  }

  return (
    <div ref={scrollerRef} className="h-full w-full overflow-auto bg-canvas">
      <div className="box-border min-h-full w-full min-w-full">
        <div
          className="relative w-full bg-background"
          style={{ minWidth: contentWidth + CHART_PAD_X * 2 }}
        >
          <div
            className="sticky top-0 z-20 border-b border-border bg-background/95 backdrop-blur-sm"
            style={{ height: HEADER_HEIGHT }}
          >
            <div
              className="relative h-full pt-2"
              style={{
                width: contentWidth,
                paddingLeft: CHART_PAD_X,
                paddingRight: CHART_PAD_X,
              }}
            >
              <div className="relative mb-1 h-4">
                {months.map((month) => (
                  <span
                    key={`${month.label}-${month.start}`}
                    className="absolute text-[11px] font-medium text-muted-foreground"
                    style={{ left: month.start * dayWidth }}
                  >
                    {month.label}
                  </span>
                ))}
              </div>
              <div className="relative h-6">
                {ticks.map((tick) => (
                  <span
                    key={tick.offset}
                    className="absolute text-[11px] tabular-nums text-muted-foreground/80"
                    style={{ left: tick.offset * dayWidth }}
                  >
                    {tick.day}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div
            className="relative py-4"
            style={{
              width: contentWidth,
              paddingLeft: CHART_PAD_X,
              paddingRight: CHART_PAD_X,
            }}
          >
            <div
              className="pointer-events-none absolute inset-y-0"
              style={{ left: CHART_PAD_X, width: contentWidth }}
              aria-hidden
            >
              {ticks.map((tick) => (
                <div
                  key={`grid-${tick.offset}`}
                  className="absolute top-0 bottom-0 border-l border-dashed border-border/80"
                  style={{ left: tick.offset * dayWidth }}
                />
              ))}
            </div>

            <div
              ref={todayRef}
              className="pointer-events-none absolute top-0 bottom-4 z-10"
              style={{ left: CHART_PAD_X + todayOffset * dayWidth }}
              aria-hidden
            >
              <div className="absolute top-1 left-1/2 flex -translate-x-1/2 items-center justify-center">
                <span className="flex h-6 items-center justify-center rounded-full bg-[#5E6AD2] px-2 text-[11px] font-semibold whitespace-nowrap text-white shadow-sm">
                  {todayLabel}
                </span>
              </div>
              <div className="absolute top-8 bottom-0 left-1/2 w-px -translate-x-1/2 bg-[#5E6AD2]" />
            </div>

            <ul
              className="relative"
              style={{ minHeight: ordered.length * ROW_HEIGHT }}
            >
              {depPaths.length > 0 ? (
                <>
                  <svg
                    className="pointer-events-none absolute top-0 left-0 z-[1]"
                    width={contentWidth}
                    height={ordered.length * ROW_HEIGHT}
                    aria-hidden
                  >
                    {depPaths.map((path) => (
                      <path
                        key={path.id}
                        d={path.d}
                        fill="none"
                        stroke={DEP_STROKE}
                        strokeOpacity={0.85}
                        strokeWidth={2}
                        strokeLinecap="round"
                        strokeDasharray="0.1 6"
                      />
                    ))}
                  </svg>
                  {depPaths.map((path) => (
                    <span
                      key={`${path.id}-badge`}
                      className="pointer-events-none absolute z-[2] flex size-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-[#E5484D] text-white shadow-sm"
                      style={{ left: path.midX, top: path.midY }}
                      title={path.label}
                      aria-hidden
                    >
                      <Ban className="size-3" strokeWidth={2.5} />
                    </span>
                  ))}
                </>
              ) : null}

              {bars.map(({ task, left, width }, index) => {
                const openEnded =
                  task.status === "open" || task.status === "running";
                const assignee = getAssignee(loadAssignee(task.id));
                const here = (taskPresence.get(task.id) ?? []).filter((p) => p.open);
                const editor = here.find((p) => p.editing);

                return (
                  <li
                    key={task.id}
                    className="relative"
                    style={{ height: ROW_HEIGHT }}
                  >
                    <Link
                      href={`/tasks/${task.id}`}
                      className={cn(
                        "absolute z-[2] flex h-9 items-center gap-2 rounded-full bg-neutral-100 py-0 pr-2.5 pl-2.5 text-left text-foreground transition-[box-shadow,transform] hover:-translate-y-px hover:shadow-md",
                        statusAccent[task.status],
                        openEnded && "border border-dashed border-neutral-300",
                      )}
                      style={{
                        left,
                        width,
                        top: (ROW_HEIGHT - BAR_HEIGHT) / 2,
                        boxShadow: editor
                          ? `0 0 0 2px ${presenceColor(editor.email)}`
                          : undefined,
                      }}
                      title={`${task.title} · ${task.startDate} → ${task.endDate}`}
                    >
                      <span
                        className="pointer-events-none absolute inset-y-0 left-0 rounded-full bg-neutral-200/90"
                        style={{
                          width: `${Math.round(task.progress * 100)}%`,
                        }}
                        aria-hidden
                      />
                      <StatusIcon
                        status={task.status}
                        className="relative z-[1] size-3.5 shrink-0"
                      />
                      <span className="relative z-[1] min-w-0 flex-1 whitespace-nowrap text-[13px] font-medium text-foreground">
                        {task.title}
                      </span>
                      <AvatarStack
                        people={here}
                        size={16}
                        max={3}
                        className="relative z-[1] shrink-0"
                      />
                      <AssigneeAvatar
                        assignee={assignee}
                        className="relative z-[1] ml-auto size-5 shrink-0 text-[10px]"
                      />
                    </Link>
                    {index < bars.length - 1 ? (
                      <div
                        className="pointer-events-none absolute right-0 bottom-0 left-0 border-b border-border/50"
                        aria-hidden
                      />
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
