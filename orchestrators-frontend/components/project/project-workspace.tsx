"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
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
      <div className="flex h-11 shrink-0 items-center border-b border-border px-3">
        <div
          className="inline-flex items-center gap-2"
          role="tablist"
          aria-label="Project view"
        >
          <ViewButton
            active={mode === "graph"}
            onClick={() => setMode("graph")}
            icon={<GraphIcon />}
            label="Graph"
          />
          <ViewButton
            active={mode === "timeline"}
            onClick={() => setMode("timeline")}
            icon={<TimelineIcon />}
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
        "inline-flex h-8 items-center gap-1.5 rounded-full border border-border px-3.5 text-[13px] font-medium transition-colors",
        "outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        active
          ? "bg-neutral-100 text-foreground"
          : "bg-background text-foreground/80 hover:bg-neutral-50 hover:text-foreground",
      )}
    >
      <span className="text-foreground/70">{icon}</span>
      {label}
    </button>
  );
}

/** 2×3 board/grid — matches Linear’s Board pill. */
function GraphIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden
      className="size-3.5"
    >
      <rect x="1.5" y="1.5" width="5" height="3.25" rx="0.75" />
      <rect x="9.5" y="1.5" width="5" height="3.25" rx="0.75" />
      <rect x="1.5" y="6.375" width="5" height="3.25" rx="0.75" />
      <rect x="9.5" y="6.375" width="5" height="3.25" rx="0.75" />
      <rect x="1.5" y="11.25" width="5" height="3.25" rx="0.75" />
      <rect x="9.5" y="11.25" width="5" height="3.25" rx="0.75" />
    </svg>
  );
}

/** Vertical spine with bars — matches Linear’s Timeline pill. */
function TimelineIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden
      className="size-3.5"
    >
      <path
        d="M8 1.5v13"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M8 3.5h4.5M8 8h3M8 12.5h5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
