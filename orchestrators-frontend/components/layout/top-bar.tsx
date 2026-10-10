"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { fetchTask, fetchTaskGraph } from "@/lib/taskgraph-api";

export function TopBar() {
  const pathname = usePathname();
  const [projectName, setProjectName] = useState("Project");
  const [taskTitle, setTaskTitle] = useState<string | null>(null);

  // Agent page owns its own header chrome.
  const isAgent = pathname.startsWith("/agent");
  const taskMatch = pathname.match(/^\/tasks\/([^/]+)/);
  const taskId = taskMatch?.[1];

  useEffect(() => {
    if (isAgent) return;
    let cancelled = false;
    void (async () => {
      try {
        const graph = await fetchTaskGraph();
        if (!cancelled) setProjectName(graph.project.name || "Project");
      } catch {
        if (!cancelled) setProjectName("Project");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isAgent, pathname]);

  useEffect(() => {
    if (!taskId) {
      setTaskTitle(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const task = await fetchTask(taskId);
        if (!cancelled) setTaskTitle(task?.title ?? null);
      } catch {
        if (!cancelled) setTaskTitle(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  if (isAgent) {
    return null;
  }

  return (
    <header className="flex h-11 shrink-0 items-center border-b border-border px-4">
      <div className="flex min-w-0 items-center gap-1.5 text-[13px]">
        <Link
          href="/project"
          className="truncate font-medium text-foreground transition-opacity hover:opacity-70"
        >
          {projectName}
        </Link>
        {taskId ? (
          <>
            <span className="text-muted-foreground/60">/</span>
            <span className="truncate text-muted-foreground">
              {taskTitle ?? "Unknown issue"}
            </span>
          </>
        ) : null}
        {pathname.startsWith("/issues") ? (
          <>
            <span className="text-muted-foreground/60">/</span>
            <span className="text-muted-foreground">Issues</span>
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
