import { apiBaseUrl } from "@/lib/api";
import type { Task, TaskActivity, TaskEdge, TaskKind, TaskStatus } from "@/lib/fixtures";

export type GraphProject = {
  name: string;
  autonomy?: string;
};

export type ApiTask = {
  id: string;
  title: string;
  kind: TaskKind;
  status: TaskStatus;
  description?: string;
  parentId?: string | null;
  blockedBy?: string[];
  relatedTo?: string[];
  startDate?: string | null;
  endDate?: string | null;
  progress?: number;
  activity?: Array<{
    id: string;
    text: string;
    at: string;
    actor?: { type: string; id: string };
    op?: string;
    reason?: string | null;
  }>;
  blocks?: string[];
  children?: string[];
};

export type TaskGraphSnapshot = {
  project: GraphProject;
  seq: number;
  tasks: Task[];
  edges: TaskEdge[];
  pendingDecisions: string[];
  pendingChangesets: string[];
};

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

/** Map API task into the UI Task shape (dates default when agent omitted them). */
export function mapApiTask(raw: ApiTask): Task {
  const start = raw.startDate || todayIso();
  const end = raw.endDate || start;
  const activity: TaskActivity[] = (raw.activity || []).map((a) => ({
    id: a.id,
    text: a.text,
    at: a.at,
  }));
  return {
    id: raw.id,
    title: raw.title,
    kind: raw.kind,
    status: raw.status,
    description: raw.description || "",
    blockedBy: raw.blockedBy || [],
    relatedTo: raw.relatedTo || [],
    activity,
    comments: [],
    startDate: start,
    endDate: end,
    progress: raw.progress ?? 0,
  };
}

export async function fetchTaskGraph(): Promise<TaskGraphSnapshot> {
  const response = await fetch(`${apiBaseUrl()}/api/taskgraph/graph`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text() || "Could not load task graph");
  }
  const data = await response.json();
  const tasks = ((data.tasks || []) as ApiTask[]).map(mapApiTask);
  const edges: TaskEdge[] = (data.edges || []).map(
    (e: { id: string; source: string; target: string }) => ({
      id: e.id,
      source: e.source,
      target: e.target,
    }),
  );
  return {
    project: data.project || { name: "Untitled project" },
    seq: data.seq ?? 0,
    tasks,
    edges,
    pendingDecisions: data.pendingDecisions || [],
    pendingChangesets: data.pendingChangesets || [],
  };
}

export async function fetchTask(id: string): Promise<Task | null> {
  const response = await fetch(`${apiBaseUrl()}/api/taskgraph/tasks/${encodeURIComponent(id)}`, {
    cache: "no-store",
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(await response.text() || "Could not load task");
  }
  return mapApiTask(await response.json());
}

/** Simple layered layout from blockedBy edges (top → bottom). */
export function layoutGraph(
  tasks: Task[],
  edges: TaskEdge[],
): Record<string, { x: number; y: number }> {
  const ids = new Set(tasks.map((t) => t.id));
  const preds = new Map<string, string[]>();
  const succs = new Map<string, string[]>();
  for (const id of ids) {
    preds.set(id, []);
    succs.set(id, []);
  }
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) continue;
    preds.get(e.target)!.push(e.source);
    succs.get(e.source)!.push(e.target);
  }
  for (const t of tasks) {
    for (const b of t.blockedBy) {
      if (!ids.has(b)) continue;
      if (!preds.get(t.id)!.includes(b)) preds.get(t.id)!.push(b);
      if (!succs.get(b)!.includes(t.id)) succs.get(b)!.push(t.id);
    }
  }

  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  function walk(id: string): number {
    if (depth.has(id)) return depth.get(id)!;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const d =
      preds.get(id)!.length === 0
        ? 0
        : 1 + Math.max(...preds.get(id)!.map(walk), 0);
    visiting.delete(id);
    depth.set(id, d);
    return d;
  }
  for (const id of ids) walk(id);

  const layers = new Map<number, string[]>();
  for (const [id, d] of depth) {
    const list = layers.get(d) || [];
    list.push(id);
    layers.set(d, list);
  }

  const COL = 300;
  const ROW = 140;
  const out: Record<string, { x: number; y: number }> = {};
  const maxLayer = Math.max(0, ...layers.keys());
  for (let d = 0; d <= maxLayer; d++) {
    const row = (layers.get(d) || []).sort();
    const width = (row.length - 1) * COL;
    row.forEach((id, i) => {
      out[id] = {
        x: row.length === 1 ? 0 : -width / 2 + i * COL,
        y: d * ROW,
      };
    });
  }
  return out;
}
