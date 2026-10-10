"use client";

import { ChevronDown, PenSquare } from "lucide-react";
import { useTaskGraph } from "@/lib/use-task-graph";
import { ScrollArea } from "@/components/ui/scroll-area";
import { StatusBadge } from "@/components/task/status-badge";

export function ProgressPanel() {
  const { data, loading } = useTaskGraph();
  const tasks = data?.tasks ?? [];
  const running = tasks.filter((t) => t.status === "running" || t.status === "in-review");
  const open = tasks.filter((t) => t.status === "open");
  const closed = tasks.filter((t) => t.status === "closed");

  return (
    <aside className="flex h-full w-full flex-col border-l border-border bg-background md:w-[340px] lg:w-[380px]">
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-border px-3">
        <button
          type="button"
          className="inline-flex items-center gap-1 text-[13px] font-medium text-foreground"
        >
          Progress
          <ChevronDown className="size-3.5 text-muted-foreground" strokeWidth={1.75} />
        </button>
        <button
          type="button"
          className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          aria-label="New update"
          disabled
        >
          <PenSquare className="size-3.5" strokeWidth={1.75} />
        </button>
      </div>

      <ScrollArea className="flex-1">
        {loading && !data ? (
          <p className="p-4 text-[13px] text-muted-foreground">Loading…</p>
        ) : !tasks.length ? (
          <p className="p-4 text-[13px] leading-relaxed text-muted-foreground">
            No graph yet. When the Agent finishes the strategy interview, it
            will create tasks here.
          </p>
        ) : (
          <div className="flex flex-col gap-4 p-3">
            <Summary label="In flight" tasks={running} />
            <Summary label="Ready" tasks={open} />
            <Summary label="Done" tasks={closed} />
            {data && data.pendingDecisions.length > 0 ? (
              <p className="text-xs text-amber-700">
                {data.pendingDecisions.length} open strateg
                {data.pendingDecisions.length === 1 ? "y decision" : "y decisions"}
              </p>
            ) : null}
          </div>
        )}
      </ScrollArea>
    </aside>
  );
}

function Summary({
  label,
  tasks,
}: {
  label: string;
  tasks: { id: string; title: string; status: import("@/lib/fixtures").TaskStatus }[];
}) {
  if (!tasks.length) return null;
  return (
    <section>
      <h3 className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </h3>
      <ul className="flex flex-col gap-1.5">
        {tasks.map((task) => (
          <li
            key={task.id}
            className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-accent/60"
          >
            <span className="truncate text-foreground">{task.title}</span>
            <StatusBadge status={task.status} />
          </li>
        ))}
      </ul>
    </section>
  );
}
