"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchTaskGraph,
  type TaskGraphSnapshot,
} from "@/lib/taskgraph-api";

const POLL_MS = 4000;

export function useTaskGraph() {
  const [data, setData] = useState<TaskGraphSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try {
      const next = await fetchTaskGraph();
      setData(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load graph");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    const id = window.setInterval(() => void reload(), POLL_MS);
    return () => window.clearInterval(id);
  }, [reload]);

  return { data, error, loading, reload };
}
