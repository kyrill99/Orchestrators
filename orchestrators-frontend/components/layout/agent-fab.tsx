"use client";

import { History, MousePointer2 } from "lucide-react";
import { useAgentDrawer } from "@/components/layout/agent-drawer-context";
import { cn } from "@/lib/utils";

/** Shell chrome control — sits in the grey frame, not a floating FAB. */
export function AgentFab() {
  const { open, toggle } = useAgentDrawer();

  return (
    <div className="flex items-center gap-0.5">
      <button
        type="button"
        onClick={toggle}
        className={cn(
          "inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-black/5 hover:text-foreground",
          open && "bg-black/5 text-foreground",
        )}
        aria-expanded={open}
        aria-label="Open agent"
      >
        <MousePointer2 className="size-3.5" strokeWidth={1.75} />
        Agent
      </button>
      <button
        type="button"
        className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-black/5 hover:text-foreground"
        aria-label="Agent history"
        disabled
      >
        <History className="size-3.5" strokeWidth={1.75} />
      </button>
    </div>
  );
}
