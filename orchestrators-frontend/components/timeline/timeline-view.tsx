"use client";

import { useLayoutEffect, useRef } from "react";
import Link from "next/link";
import {
  Flag,
  Search,
  FlaskConical,
  GitBranch,
  Hammer,
  Rocket,
  type LucideIcon,
} from "lucide-react";
import {
  TIMELINE_TODAY,
  tasks,
  type Task,
  type TaskKind,
  type TaskStatus,
} from "@/lib/fixtures";
import { cn } from "@/lib/utils";

const DAY_MS = 86_400_000;
const DAY_WIDTH = 36;
const ROW_HEIGHT = 56;
const HEADER_HEIGHT = 56;
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

const kindIcon: Record<TaskKind, LucideIcon> = {
  start: Flag,
  task: FlaskConical,
  decision: GitBranch,
  end: Rocket,
};

const kindIconOverride: Partial<Record<string, LucideIcon>> = {
  research: Search,
  implement: Hammer,
  deploy: Rocket,
};

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

function buildRange(items: Task[]) {
  const starts = items.map((t) => parseDay(t.startDate));
  const ends = items.map((t) => parseDay(t.endDate));
  const rangeStart = addDays(Math.min(...starts), -3);
  const rangeEnd = addDays(Math.max(...ends), 5);
  return {
    rangeStart,
    rangeEnd,
    totalDays: daysBetween(rangeStart, rangeEnd),
  };
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

export function TimelineView() {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const todayRef = useRef<HTMLDivElement>(null);

  const ordered = [
    "start",
    "research",
    "proto-1",
    "proto-2",
    "proto-3",
    "decision",
    "implement",
    "deploy",
  ]
    .map((id) => tasks.find((t) => t.id === id))
    .filter((t): t is Task => Boolean(t));

  const { rangeStart, rangeEnd, totalDays } = buildRange(ordered);
  const width = totalDays * DAY_WIDTH;
  const months = monthSpans(rangeStart, rangeEnd);
  const ticks = weekTicks(rangeStart, totalDays);
  const todayOffset = daysBetween(rangeStart, parseDay(TIMELINE_TODAY));
  const todayDay = new Date(parseDay(TIMELINE_TODAY)).getUTCDate();

  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    const today = todayRef.current;
    if (!scroller || !today) return;
    const target =
      today.offsetLeft - scroller.clientWidth * 0.35;
    scroller.scrollLeft = Math.max(0, target);
  }, [todayOffset]);

  return (
    <div ref={scrollerRef} className="h-full overflow-auto bg-canvas">
      <div className="inline-block min-w-full p-4 sm:p-6">
        <div
          className="relative rounded-xl border border-border bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
          style={{ width: width + 48 }}
        >
          <div
            className="sticky top-0 z-20 border-b border-border bg-background/95 backdrop-blur-sm"
            style={{ height: HEADER_HEIGHT }}
          >
            <div className="relative h-full px-6 pt-2" style={{ width }}>
              <div className="relative mb-1 h-4">
                {months.map((month) => (
                  <span
                    key={`${month.label}-${month.start}`}
                    className="absolute text-[11px] font-medium text-muted-foreground"
                    style={{ left: month.start * DAY_WIDTH }}
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
                    style={{ left: tick.offset * DAY_WIDTH }}
                  >
                    {tick.day}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="relative px-6 py-4" style={{ width }}>
            <div
              className="pointer-events-none absolute inset-y-0 left-6"
              style={{ width }}
              aria-hidden
            >
              {ticks.map((tick) => (
                <div
                  key={`grid-${tick.offset}`}
                  className="absolute top-0 bottom-0 border-l border-dashed border-border/80"
                  style={{ left: tick.offset * DAY_WIDTH }}
                />
              ))}
            </div>

            <div
              ref={todayRef}
              className="pointer-events-none absolute top-0 bottom-4 z-10"
              style={{ left: 24 + todayOffset * DAY_WIDTH }}
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
              {ordered.map((task, index) => {
                const start = daysBetween(
                  rangeStart,
                  parseDay(task.startDate),
                );
                const duration =
                  daysBetween(
                    parseDay(task.startDate),
                    parseDay(task.endDate),
                  ) + 1;
                const Icon =
                  kindIconOverride[task.id] ?? kindIcon[task.kind];
                const openEnded =
                  task.status === "open" || task.status === "running";

                return (
                  <li
                    key={task.id}
                    className="relative"
                    style={{ height: ROW_HEIGHT }}
                  >
                    <Link
                      href={`/tasks/${task.id}`}
                      className={cn(
                        "absolute top-2.5 flex h-10 items-center gap-2 overflow-hidden rounded-full bg-neutral-100 pr-4 pl-2 text-left text-foreground transition-[box-shadow,transform] hover:-translate-y-px hover:shadow-md",
                        statusAccent[task.status],
                        openEnded && "border border-dashed border-neutral-300",
                      )}
                      style={{
                        left: start * DAY_WIDTH,
                        width: Math.max(duration * DAY_WIDTH, 148),
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
                      <span
                        className={cn(
                          "relative z-[1] flex size-6 shrink-0 items-center justify-center rounded-full bg-white shadow-sm",
                          task.status === "running" &&
                            "ring-2 ring-sky-400/50",
                          task.status === "in-review" &&
                            "ring-2 ring-amber-400/50",
                        )}
                      >
                        <Icon
                          className="size-3.5 text-foreground/80"
                          strokeWidth={1.75}
                        />
                        {task.status === "in-review" ||
                        task.status === "running" ? (
                          <span className="absolute -top-0.5 -right-0.5 size-1.5 rounded-full bg-rose-500" />
                        ) : null}
                      </span>
                      <span className="relative z-[1] truncate text-[13px] font-medium">
                        {task.title}
                      </span>
                    </Link>
                    {index < ordered.length - 1 ? (
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
