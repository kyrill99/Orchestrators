export type TaskStatus = "open" | "running" | "in-review" | "closed";

export type TaskKind = "start" | "task" | "decision" | "end";

export type TaskActivity = {
  id: string;
  text: string;
  at: string;
};

export type CommentAuthorKind = "human" | "agent";

export type TaskComment = {
  id: string;
  author: string;
  authorKind: CommentAuthorKind;
  body: string;
  at: string;
};

export type Task = {
  id: string;
  title: string;
  kind: TaskKind;
  status: TaskStatus;
  description: string;
  blockedBy: string[];
  relatedTo: string[];
  activity: TaskActivity[];
  comments: TaskComment[];
  /** Inclusive schedule bounds for the Timeline Gantt (ISO date YYYY-MM-DD). */
  startDate: string;
  endDate: string;
  /** 0–1 filled portion of the bar (completed work). */
  progress: number;
};

export type TaskEdge = {
  id: string;
  source: string;
  target: string;
};

export type ChatMessage = {
  id: string;
  role: "agent" | "user";
  content: string;
};

export type MemoryFile = {
  id: string;
  name: string;
  path: string;
  content: string;
  /** Set when the file comes from the backend (`GET /api/memory/tree`). */
  scope?: "project" | "user" | "global";
  kind?: string;
  confidence?: number;
  uses?: number;
};

export type MemoryFolder = {
  id: string;
  name: string;
  /** From the backend: "This project · strategy", "Personal · …", "All projects · …". */
  label?: string;
  files: MemoryFile[];
};

export const PROJECT_NAME = "Project";

/** @deprecated Graph data comes from GET /api/taskgraph/graph */
export const TIMELINE_TODAY = new Date().toISOString().slice(0, 10);

export const tasks: Task[] = [];

export const edges: TaskEdge[] = [];


export const chatMessages: ChatMessage[] = [];

export type RecentChat = {
  id: string;
  title: string;
  age: string;
};

export const recentChats: RecentChat[] = [];

export type ProgressHealth = "on-track" | "at-risk" | "blocked";

export type ProgressMention = {
  id: string;
  label: string;
  kind: "task" | "decision" | "memory";
};

export type ProgressUpdate = {
  id: string;
  health: ProgressHealth;
  author: string;
  age: string;
  /** Segments of body text; mentions render as chips. */
  body: Array<{ type: "text"; value: string } | { type: "mention"; id: string }>;
  mentions: ProgressMention[];
};

/** @deprecated Progress is derived from the live task graph. */
export const progressUpdates: ProgressUpdate[] = [];

export const progressHealthLabel: Record<ProgressHealth, string> = {
  "on-track": "On track",
  "at-risk": "At risk",
  blocked: "Blocked",
};

export const memoryTree: MemoryFolder[] = [
  {
    id: "folder-strategy",
    name: "strategy",
    files: [
      {
        id: "file-mission",
        name: "mission.md",
        path: "strategy/mission.md",
        content: `# Mission

Orchestration layer for agentic software and research projects.

Understand the owner's strategy and project type. Agents handle small decisions;
the owner stays informed, not involved, via overview, timeline, and decision log.
`,
      },
      {
        id: "file-success",
        name: "success-criteria.md",
        path: "strategy/success-criteria.md",
        content: `# Success criteria

- Compare chunking approaches on recall, latency, and coherence
- Prefer opinionated agent defaults when strategy is clear
- Surface only strategy-level decisions to Lorenz
`,
      },
    ],
  },
  {
    id: "folder-research",
    name: "agents/research",
    files: [
      {
        id: "file-lit",
        name: "literature-notes.md",
        path: "agents/research/literature-notes.md",
        content: `# Literature notes

- Fixed windows: cheap baseline
- Semantic splits: better boundaries, embedding cost
- Hierarchical: summaries for long-context retrieval
`,
      },
    ],
  },
  {
    id: "folder-proto",
    name: "agents/prototype",
    files: [
      {
        id: "file-bench",
        name: "benchmark-plan.md",
        path: "agents/prototype/benchmark-plan.md",
        content: `# Benchmark plan

Dataset: internal docs corpus (sample 200).
Metrics: chunk coherence, retrieval recall@k, p95 latency.
`,
      },
    ],
  },
];

export function getTask(id: string): Task | undefined {
  return tasks.find((task) => task.id === id);
}

export function getMemoryFile(id: string): MemoryFile | undefined {
  for (const folder of memoryTree) {
    const file = folder.files.find((f) => f.id === id);
    if (file) return file;
  }
  return undefined;
}

export const statusLabel: Record<TaskStatus, string> = {
  open: "Todo",
  running: "In Progress",
  "in-review": "In Review",
  closed: "Done",
};
