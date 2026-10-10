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
  files: MemoryFile[];
};

export const PROJECT_NAME = "Chunking evaluation";

/** “Today” marker on the Timeline Gantt (demo fixture). */
export const TIMELINE_TODAY = "2026-10-10";

export const tasks: Task[] = [
  {
    id: "start",
    title: "START",
    kind: "start",
    status: "closed",
    description: "Project kickoff — strategy interview begins.",
    blockedBy: [],
    relatedTo: ["research"],
    startDate: "2026-09-29",
    endDate: "2026-10-02",
    progress: 1,
    activity: [
      {
        id: "a-start-1",
        text: "Agent opened strategy interview with Lorenz",
        at: "2026-10-08T09:00:00Z",
      },
    ],
    comments: [
      {
        id: "c-start-1",
        author: "orchestrator-agent",
        authorKind: "agent",
        body: "Starting strategy interview. I’ll only ask about intent and success — not implementation choices.",
        at: "2026-10-08T09:01:00Z",
      },
      {
        id: "c-start-2",
        author: "lorenz",
        authorKind: "human",
        body: "Sounds good. Goal is evaluating our chunking algorithm.",
        at: "2026-10-08T09:05:00Z",
      },
    ],
  },
  {
    id: "research",
    title: "Relevant Work Research",
    kind: "task",
    status: "closed",
    description:
      "Survey prior art on chunking algorithms, baselines, and evaluation metrics for the current codebase.",
    blockedBy: ["start"],
    relatedTo: ["proto-1", "proto-2", "proto-3"],
    startDate: "2026-10-01",
    endDate: "2026-10-08",
    progress: 1,
    activity: [
      {
        id: "a-research-1",
        text: "assigned to research agent",
        at: "2026-10-08T09:12:00Z",
      },
      {
        id: "a-research-2",
        text: "agent moved to closed — memo attached",
        at: "2026-10-08T11:40:00Z",
      },
    ],
    comments: [
      {
        id: "c-research-1",
        author: "research-agent",
        authorKind: "agent",
        body: "Surveyed fixed windows, semantic splits, and hierarchical chunking. Memo in memory under agents/research.",
        at: "2026-10-08T11:20:00Z",
      },
      {
        id: "c-research-2",
        author: "lorenz",
        authorKind: "human",
        body: "Prefer we weight coherence over raw latency unless strategy says otherwise — leave the call to agents.",
        at: "2026-10-08T11:35:00Z",
      },
      {
        id: "c-research-3",
        author: "research-agent",
        authorKind: "agent",
        body: "Noted in strategy memory. Spawning three prototype tracks.",
        at: "2026-10-08T11:42:00Z",
      },
    ],
  },
  {
    id: "proto-1",
    title: "Prototype Approach 1",
    kind: "task",
    status: "in-review",
    description:
      "Fixed-size token windows with overlap. Fast baseline for latency and recall.",
    blockedBy: ["research"],
    relatedTo: ["decision"],
    startDate: "2026-10-06",
    endDate: "2026-10-14",
    progress: 0.85,
    activity: [
      {
        id: "a-p1-1",
        text: "assigned to prototype agent",
        at: "2026-10-08T12:00:00Z",
      },
      {
        id: "a-p1-2",
        text: "agent moved to in-review",
        at: "2026-10-09T14:20:00Z",
      },
    ],
    comments: [
      {
        id: "c-p1-1",
        author: "prototype-agent",
        authorKind: "agent",
        body: "Fixed-window baseline is ready for review. Strong latency, weaker coherence vs semantic approach.",
        at: "2026-10-09T14:22:00Z",
      },
      {
        id: "c-p1-2",
        author: "lorenz",
        authorKind: "human",
        body: "Don’t ask me A vs B — if coherence is the success criterion, factor that into the decision node yourself.",
        at: "2026-10-09T15:10:00Z",
      },
    ],
  },
  {
    id: "proto-2",
    title: "Prototype Approach 2",
    kind: "task",
    status: "running",
    description:
      "Semantic boundary detection using embeddings. Trade recall for coherence.",
    blockedBy: ["research"],
    relatedTo: ["decision"],
    startDate: "2026-10-07",
    endDate: "2026-10-18",
    progress: 0.45,
    activity: [
      {
        id: "a-p2-1",
        text: "assigned to prototype agent",
        at: "2026-10-08T12:00:00Z",
      },
      {
        id: "a-p2-2",
        text: "agent moved to running",
        at: "2026-10-09T10:05:00Z",
      },
    ],
    comments: [
      {
        id: "c-p2-1",
        author: "prototype-agent",
        authorKind: "agent",
        body: "Semantic boundary run in progress. Early recall@k looks promising; will post coherence when the batch finishes.",
        at: "2026-10-09T10:10:00Z",
      },
    ],
  },
  {
    id: "proto-3",
    title: "Prototype Approach 3",
    kind: "task",
    status: "open",
    description:
      "Hierarchical chunking with parent summaries. Heavier, better for long docs.",
    blockedBy: ["research"],
    relatedTo: ["decision"],
    startDate: "2026-10-08",
    endDate: "2026-10-20",
    progress: 0.15,
    activity: [
      {
        id: "a-p3-1",
        text: "created from research memo",
        at: "2026-10-08T12:00:00Z",
      },
    ],
    comments: [],
  },
  {
    id: "decision",
    title: "Decision: which approach",
    kind: "decision",
    status: "open",
    description:
      "Compare prototypes against strategy success criteria. Escalate only if strategy context is missing — not A vs B implementation trivia.",
    blockedBy: ["proto-1", "proto-2", "proto-3"],
    relatedTo: ["implement"],
    startDate: "2026-10-16",
    endDate: "2026-10-22",
    progress: 0,
    activity: [
      {
        id: "a-dec-1",
        text: "waiting on prototype results",
        at: "2026-10-09T15:00:00Z",
      },
    ],
    comments: [
      {
        id: "c-dec-1",
        author: "orchestrator-agent",
        authorKind: "agent",
        body: "I’ll pick an approach once prototypes report against success criteria. Escalating only if strategy weights are unclear.",
        at: "2026-10-09T15:05:00Z",
      },
      {
        id: "c-dec-2",
        author: "lorenz",
        authorKind: "human",
        body: "Success is clear: coherence first, then latency. No need to ping me for the pick.",
        at: "2026-10-09T16:00:00Z",
      },
    ],
  },
  {
    id: "implement",
    title: "Implement & Polish",
    kind: "task",
    status: "open",
    description:
      "Productize the chosen approach, add eval harness, and polish for demo.",
    blockedBy: ["decision"],
    relatedTo: ["deploy"],
    startDate: "2026-10-20",
    endDate: "2026-10-28",
    progress: 0,
    activity: [],
    comments: [],
  },
  {
    id: "deploy",
    title: "Deploy",
    kind: "end",
    status: "open",
    description: "Ship evaluation report and runnable prototype.",
    blockedBy: ["implement"],
    relatedTo: [],
    startDate: "2026-10-27",
    endDate: "2026-11-02",
    progress: 0,
    activity: [],
    comments: [],
  },
];

export const edges: TaskEdge[] = [
  { id: "e-start-research", source: "start", target: "research" },
  { id: "e-research-p1", source: "research", target: "proto-1" },
  { id: "e-research-p2", source: "research", target: "proto-2" },
  { id: "e-research-p3", source: "research", target: "proto-3" },
  { id: "e-p1-decision", source: "proto-1", target: "decision" },
  { id: "e-p2-decision", source: "proto-2", target: "decision" },
  { id: "e-p3-decision", source: "proto-3", target: "decision" },
  { id: "e-decision-implement", source: "decision", target: "implement" },
  { id: "e-implement-deploy", source: "implement", target: "deploy" },
];

export const chatMessages: ChatMessage[] = [
  {
    id: "m1",
    role: "agent",
    content:
      "Hi Lorenz, let’s get started defining the objective of our project. Can you tell me what your end goal is in one sentence?",
  },
  {
    id: "m2",
    role: "user",
    content: "Cool, I want to evaluate our chunking algorithm.",
  },
  {
    id: "m3",
    role: "agent",
    content:
      "Got it — evaluating the chunking algorithm. What does success look like for you? For example: better recall, lower latency, clearer chunk boundaries, or something else?",
  },
];

export type RecentChat = {
  id: string;
  title: string;
  age: string;
};

export const recentChats: RecentChat[] = [
  {
    id: "chunking-interview",
    title: "Chunking evaluation — objective interview",
    age: "2d",
  },
];

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

/** Newest first — Progress panel beside Graph / Timeline. */
export const progressUpdates: ProgressUpdate[] = [
  {
    id: "u1",
    health: "on-track",
    author: "research-agent",
    age: "43min ago",
    body: [
      {
        type: "text",
        value: "Closed literature pass and handed off three prototype tracks. Strategy still centers on evaluating ",
      },
      { type: "mention", id: "m-chunk" },
      {
        type: "text",
        value: " against recall and coherence — no implementation choice escalated.",
      },
    ],
    mentions: [
      { id: "m-chunk", label: "Chunking evaluation", kind: "memory" },
    ],
  },
  {
    id: "u2",
    health: "on-track",
    author: "prototype-agent",
    age: "2h ago",
    body: [
      { type: "mention", id: "m-p2" },
      {
        type: "text",
        value:
          " is running semantic boundary detection. Early latency looks fine; waiting on coherence scores before ",
      },
      { type: "mention", id: "m-dec" },
      { type: "text", value: "." },
    ],
    mentions: [
      { id: "m-p2", label: "Prototype Approach 2", kind: "task" },
      { id: "m-dec", label: "Decision: which approach", kind: "decision" },
    ],
  },
  {
    id: "u3",
    health: "at-risk",
    author: "prototype-agent",
    age: "5h ago",
    body: [
      { type: "mention", id: "m-p1" },
      {
        type: "text",
        value:
          " moved to in-review. Fixed windows beat baseline latency but trail on coherence — may need strategy call if we weight latency higher.",
      },
    ],
    mentions: [
      { id: "m-p1", label: "Prototype Approach 1", kind: "task" },
    ],
  },
  {
    id: "u4",
    health: "on-track",
    author: "lorenz",
    age: "2d ago",
    body: [
      {
        type: "text",
        value:
          "Objective set: evaluate our chunking algorithm. Success criteria pulled into memory; agents may decide approach details.",
      },
    ],
    mentions: [],
  },
];

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
