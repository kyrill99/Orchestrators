"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { GitBranch, ListTree } from "lucide-react";
import { TimelineView } from "@/components/timeline/timeline-view";
import { cn } from "@/lib/utils";

const TaskGraph = dynamic(
  () =>
    import("@/components/graph/task-graph").then((mod) => mod.TaskGraph),
  {
    ssr: false,
    loading: () => <div className="h-full w-full bg-canvas" aria-hidden />,
  },
);

type ViewMode = "graph" | "timeline";

export function ProjectWorkspace() {
  const [mode, setMode] = useState<ViewMode>("graph");

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-10 shrink-0 items-center border-b border-border px-3">
        <div
          className="inline-flex items-center rounded-md border border-border bg-background p-0.5"
          role="tablist"
          aria-label="Project view"
        >
          <ViewButton
            active={mode === "graph"}
            onClick={() => setMode("graph")}
            icon={<GitBranch className="size-3.5" strokeWidth={1.75} />}
            label="Graph"
          />
          <ViewButton
            active={mode === "timeline"}
            onClick={() => setMode("timeline")}
            icon={<ListTree className="size-3.5" strokeWidth={1.75} />}
            label="Timeline"
          />
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {mode === "graph" ? <TaskGraph /> : <TimelineView />}
      </div>
    </div>
  );
}

function ViewButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-[5px] px-2.5 text-xs font-medium transition-colors",
        active
          ? "bg-accent text-foreground shadow-[0_1px_1px_rgba(0,0,0,0.04)]"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}
