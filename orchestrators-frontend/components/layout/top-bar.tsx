"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { PROJECT_NAME, getTask } from "@/lib/fixtures";

export function TopBar() {
  const pathname = usePathname();

  const taskMatch = pathname.match(/^\/tasks\/([^/]+)/);
  const task = taskMatch ? getTask(taskMatch[1]) : undefined;

  return (
    <header className="flex h-11 shrink-0 items-center border-b border-border px-4">
      <div className="flex min-w-0 items-center gap-1.5 text-[13px]">
        <Link
          href="/project"
          className="truncate font-medium text-foreground transition-opacity hover:opacity-70"
        >
          {PROJECT_NAME}
        </Link>
        {taskMatch ? (
          <>
            <span className="text-muted-foreground/60">/</span>
            <span className="truncate text-muted-foreground">
              {task?.title ?? "Unknown issue"}
            </span>
          </>
        ) : null}
        {pathname.startsWith("/memory") ? (
          <>
            <span className="text-muted-foreground/60">/</span>
            <span className="text-muted-foreground">Memory</span>
          </>
        ) : null}
      </div>
    </header>
  );
}
