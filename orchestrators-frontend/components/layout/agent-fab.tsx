"use client";

import { usePathname } from "next/navigation";
import { MousePointer2 } from "lucide-react";
import { useAgentDrawer } from "@/components/layout/agent-drawer-context";
import { cn } from "@/lib/utils";

export function AgentFab() {
  const pathname = usePathname();
  const { open, toggle } = useAgentDrawer();

  // Full Agent page is open — FAB stays as a quick affordance for the drawer.
  return (
    <button
      type="button"
      onClick={toggle}
      className={cn(
        "fixed right-4 bottom-4 z-40 inline-flex h-9 items-center gap-2 rounded-full border border-border bg-background px-3.5 text-[13px] font-medium text-foreground shadow-[0_2px_8px_rgba(0,0,0,0.08)] transition-[box-shadow,transform,background-color] hover:bg-accent",
        open && "bg-accent shadow-[0_2px_12px_rgba(0,0,0,0.12)]",
        pathname.startsWith("/agent") && !open && "opacity-90",
      )}
      aria-expanded={open}
      aria-label="Open agent"
    >
      <MousePointer2 className="size-3.5" strokeWidth={1.75} />
      Agent
    </button>
  );
}
