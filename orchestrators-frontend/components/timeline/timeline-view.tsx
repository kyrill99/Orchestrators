"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { getAssignee, loadAssignee } from "@/lib/assignees";
import type { Task, TaskStatus } from "@/lib/fixtures";
import { useTaskGraph } from "@/lib/use-task-graph";
import { cn } from "@/lib/utils";
import { AssigneeAvatar } from "@/components/task/assignee-picker";
import { StatusIcon } from "@/components/task/status-icon";

const DAY_MS = 86_400_000;
const MIN_DAY_WIDTH = 36;
const MIN_RANGE_DAYS = 28;
const ROW_HEIGHT = 56;
const HEADER_HEIGHT = 56;
const CHART_PAD_X = 24; // px-6
const MIN_BAR_WIDTH = 200;
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

function orderTasks(tasks: Task[]): Task[] {
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
  const idW = Math.max(task.id.length, 4) * 6.5;
  const titleW = task.title.length * 7.2;
  return 20 + idW + 14 + titleW + 28 + 20;
}

export function TimelineView() {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const todayRef = useRef<HTMLDivElement>(null);
  const [viewportWidth, setViewportWidth] = useState(0);
  const { data, loading, error } = useTaskGraph();
  const todayIso = useMemo(() => new Date().toISOString().slice(0, 10), []);

  const ordered = useMemo(
    () => orderTasks(data?.tasks ?? []),
    [data?.tasks],
  );

  const { rangeStart, rangeEnd, totalDays } = buildRange(ordered, todayIso);
  const months = monthSpans(rangeStart, rangeEnd);
  const ticks = weekTicks(rangeStart, totalDays);
  const todayOffset = daysBetween(rangeStart, parseDay(todayIso));
  const todayDay = new Date(parseDay(todayIso)).getUTCDate();

  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const measure = () => setViewportWidth(scroller.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(scroller);
    return () => observer.disconnect();
  }, [ordered.length]);

  const outerPad = 48; // p-4 / sm:p-6 approx; use min of measured
  const available =
    viewportWidth > 0 ? Math.max(viewportWidth - outerPad - CHART_PAD_X * 2, 320) : 0;

  const dayWidth = Math.max(
    MIN_DAY_WIDTH,
    available > 0 ? available / totalDays : MIN_DAY_WIDTH,
  );

  const bars = useMemo(() => {
    return ordered.map((task) => {
      const start = daysBetween(rangeStart, parseDay(task.startDate));
      const duration =
        daysBetween(parseDay(task.startDate), parseDay(task.endDate)) + 1;
      const scheduleWidth = Math.max(duration * dayWidth, MIN_BAR_WIDTH);
      const width = Math.max(scheduleWidth, estimateBarContentWidth(task));
      return { task, start, width, left: start * dayWidth };
    });
  }, [ordered, rangeStart, dayWidth]);

  const contentWidth = Math.max(
    totalDays * dayWidth,
    ...bars.map((b) => b.left + b.width),
    available,
  );

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
      <div className="box-border min-h-full w-full min-w-full p-4 sm:p-6">
        <div
          className="relative w-full rounded-xl border border-border bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
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
                <span className="flex size-6 items-center justify-center rounded-full bg-sky-500 text-[11px] font-semibold text-white shadow-sm">
                  {todayDay}
                </span>
              </div>
              <div className="absolute top-8 bottom-0 left-1/2 w-px -translate-x-1/2 bg-sky-500" />
            </div>

            <ul
              className="relative"
              style={{ minHeight: ordered.length * ROW_HEIGHT }}
            >
              {bars.map(({ task, left, width }, index) => {
                const openEnded =
                  task.status === "open" || task.status === "running";
                const assignee = getAssignee(loadAssignee(task.id));

                return (
                  <li
                    key={task.id}
                    className="relative"
                    style={{ height: ROW_HEIGHT }}
                  >
                    <Link
                      href={`/tasks/${task.id}`}
                      className={cn(
                        "absolute top-2.5 flex h-9 items-center gap-2 rounded-full bg-neutral-100 py-0 pr-2.5 pl-2.5 text-left text-foreground transition-[box-shadow,transform] hover:-translate-y-px hover:shadow-md",
                        statusAccent[task.status],
                        openEnded && "border border-dashed border-neutral-300",
                      )}
                      style={{
                        left,
                        width,
                        zIndex: 1,
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
                      <span className="relative z-[1] shrink-0 font-mono text-[11px] text-muted-foreground tabular-nums">
                        {task.id}
                      </span>
                      <StatusIcon
                        status={task.status}
                        className="relative z-[1] size-3.5 shrink-0"
                      />
                      <span className="relative z-[1] min-w-0 flex-1 whitespace-nowrap text-[13px] font-medium text-foreground">
                        {task.title}
                      </span>
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
