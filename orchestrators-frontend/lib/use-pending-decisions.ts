"use client";

import { useEffect, useState } from "react";
import { apiBaseUrl } from "@/lib/api";
import { projectFetch } from "@/lib/project-scope";
import { useProjectId } from "@/lib/use-project";

const POLL_MS = 8000;

/** Number of decisions in the current project that wait for a choice. */
export function usePendingDecisions(): number {
  const projectId = useProjectId();
  const [count, setCount] = useState(0);
  useEffect(() => {
    let stopped = false;
    const load = () =>
      projectFetch(`${apiBaseUrl()}/api/taskgraph/decisions?state=pending`, { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : []))
        .then((list: unknown[]) => !stopped && setCount(Array.isArray(list) ? list.length : 0))
        .catch(() => undefined);
    void load();
    const id = window.setInterval(load, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [projectId]);
  return count;
}
