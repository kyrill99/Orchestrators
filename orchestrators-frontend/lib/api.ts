import { projectFetch, withProjectQuery } from "@/lib/project-scope";

const DEFAULT_API_URL = "http://localhost:8000";

export function apiBaseUrl(): string {
  // Empty (production build) means same-origin /api, routed by oauth2-proxy.
  return (
    process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? DEFAULT_API_URL
  );
}

export type GradiumToken = {
  token: string;
  expires_at?: string | null;
  voice_id: string;
  asr_url: string;
  tts_url: string;
};

export async function fetchGradiumToken(): Promise<GradiumToken> {
  const response = await projectFetch(`${apiBaseUrl()}/api/voice/token`, {
    method: "POST",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Could not get Gradium token");
  }
  return response.json();
}

/** "voice" asks for short, speakable replies with less thinking time. */
export type AgentMode = "text" | "voice";

export type Me = { email: string; name: string };

export type Participant = { email: string; name: string };

export type ConversationMessage = {
  id: number;
  conversation_id: string;
  role: "user" | "agent" | "error" | "tool";
  author_email: string | null;
  author_name: string | null;
  content: string;
  mode: AgentMode;
  /** Unix seconds. */
  created_at: number;
  reactions?: MessageReactions;
};

export type MessageReactions = {
  up: number;
  down: number;
  voters: { email: string; name: string; value: number }[];
};

export type ConversationSummary = {
  id: string;
  title: string;
  created_by: string;
  created_by_name: string;
  created_at: number;
  updated_at: number;
  mode: AgentMode;
  message_count: number;
  participants: Participant[];
  last_message: ConversationMessage | null;
  /** The agent is answering right now. */
  thinking: boolean;
  /** Project the conversation belongs to (missing = "default"). */
  project?: string;
};

export type ConversationDetail = ConversationSummary & {
  messages: ConversationMessage[];
};

export type PostMessageResult = {
  message: ConversationMessage | null;
  reply: ConversationMessage;
};

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await projectFetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text).detail;
      // Task graph errors are {code, message, opIndex}.
      detail = (typeof parsed === "object" && parsed?.message) || parsed || text;
    } catch {
      // plain-text error body
    }
    throw new ApiError(
      detail || `${path} failed (${response.status})`,
      response.status,
    );
  }
  return response.json();
}

// ---- task graph: agent runs (see orchestrators-backend/taskgraph/API.md) ----

export type Actor = { type: "human" | "agent"; id: string };

/**
 * The GUI has no login yet, so human actions are recorded as this placeholder.
 * Replace with the signed-in user once auth exposes one.
 */
export const GUI_ACTOR: Actor = { type: "human", id: "owner" };

export type AgentRun = {
  id: string;
  taskId: string;
  agent: Actor;
  status: "running" | "succeeded" | "failed" | "stopped" | "lost";
  /** "record": nothing was launched, the run is only tracked. */
  runner: string;
  attempt: number;
  startedBy: Actor;
  startedAt: string;
};

export type TaskRunState =
  | { kind: "offline" }
  | { kind: "missing" }
  | { kind: "live"; status: string; activeRun: AgentRun | null };

/** The task's live status and active run, if the task is in the backend graph. */
export async function fetchTaskRun(taskId: string): Promise<TaskRunState> {
  let response: Response;
  try {
    response = await projectFetch(
      `${apiBaseUrl()}/api/taskgraph/tasks/${encodeURIComponent(taskId)}`,
    );
  } catch {
    return { kind: "offline" };
  }
  if (response.status === 404) return { kind: "missing" };
  if (!response.ok) return { kind: "offline" };
  const task = await response.json();
  return { kind: "live", status: task.status, activeRun: task.activeRun };
}

export async function controlTaskRun(
  taskId: string,
  action: "start" | "stop" | "restart",
): Promise<AgentRun> {
  const response = await projectFetch(
    `${apiBaseUrl()}/api/taskgraph/tasks/${encodeURIComponent(taskId)}/run/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: GUI_ACTOR, reason: `${action} from the GUI` }),
    },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail?.message ?? `Could not ${action} the run`);
  }
  return response.json();
}

/** Runs in progress across the graph (empty if the backend is unreachable). */
export async function fetchActiveRuns(): Promise<AgentRun[]> {
  try {
    const response = await projectFetch(`${apiBaseUrl()}/api/taskgraph/graph`);
    if (!response.ok) return [];
    return (await response.json()).activeRuns ?? [];
  } catch {
    return [];
  }
}

export function fetchMe(): Promise<Me> {
  return request("/api/me");
}

export function listConversations(): Promise<ConversationSummary[]> {
  return request("/api/conversations");
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return request(`/api/conversations/${encodeURIComponent(id)}`);
}

export function createConversation(
  mode: AgentMode = "text",
): Promise<ConversationSummary> {
  return request("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

/**
 * Post to a shared conversation and wait for the agent's reply. Everyone
 * watching sees both live; `hidden` sends a stage direction that is not shown.
 */
export function postConversationMessage(
  id: string,
  content: string,
  mode: AgentMode = "text",
  hidden = false,
): Promise<PostMessageResult> {
  return request(`/api/conversations/${encodeURIComponent(id)}/messages`, {
    method: "POST",
    body: JSON.stringify({ content, mode, hidden }),
  });
}

/** Upvote (1), downvote (-1) or clear (0) a message; the update arrives over SSE too. */
export function reactToMessage(
  cid: string,
  mid: string | number,
  value: 1 | -1 | 0,
): Promise<ConversationMessage> {
  return request(
    `/api/conversations/${encodeURIComponent(cid)}/messages/${encodeURIComponent(String(mid))}/reactions`,
    { method: "POST", body: JSON.stringify({ value }) },
  );
}

/** Server-Sent Events: conversation, message, thinking, resync. */
export function conversationStreamUrl(): string {
  return withProjectQuery(`${apiBaseUrl()}/api/conversations/stream`);
}

// ---- Agent runs: steps, scratchpad, engine ----------------------------------

/** A run with how it ended (GET /tasks/{id}/runs). */
export type RunRecord = AgentRun & {
  endedAt?: string | null;
  note?: string | null;
  instructions?: string | null;
};

export type RunStepKind =
  | "thinking"
  | "message"
  | "tool_call"
  | "tool_result"
  | "error"
  | "status";

export type RunStep = {
  runId: string;
  seq: number;
  taskId: string;
  at: string;
  kind: RunStepKind;
  payload: {
    text?: string;
    name?: string;
    args?: unknown;
    result?: string;
    callId?: string | null;
    isError?: boolean;
    builtin?: boolean;
    state?: string;
    note?: string | null;
    message?: string;
    [key: string]: unknown;
  };
};

export type RunStepsPage = {
  run: RunRecord;
  active: boolean;
  steps: RunStep[];
  next: number;
};

/** Every run of a task, oldest first. */
export function fetchTaskRuns(taskId: string): Promise<RunRecord[]> {
  return request(`/api/taskgraph/tasks/${encodeURIComponent(taskId)}/runs`);
}

/** Steps of a run after `after` (cursor for live polling). */
export function fetchRunSteps(runId: string, after = 0): Promise<RunStepsPage> {
  return request(
    `/api/taskgraph/runs/${encodeURIComponent(runId)}/steps?after=${after}`,
  );
}

export type Scratchpad = {
  taskId: string;
  content: string;
  updatedAt: string | null;
  updatedBy: string | null;
};

export function fetchScratchpad(taskId: string): Promise<Scratchpad> {
  return request(`/api/taskgraph/tasks/${encodeURIComponent(taskId)}/scratchpad`);
}

export function saveScratchpad(taskId: string, content: string): Promise<Scratchpad> {
  return request(
    `/api/taskgraph/tasks/${encodeURIComponent(taskId)}/scratchpad`,
    { method: "PUT", body: JSON.stringify({ content }) },
  );
}

export type EngineStatus = {
  state: "running" | "stopped";
  running: boolean;
  updatedAt: string | null;
  updatedBy: string | null;
  reason: string | null;
  /** "gemini" runs agents; "record" cannot run the engine. */
  runner: string;
  maxRuns: number;
  activeRuns: AgentRun[];
  queue: { id: string; title: string }[];
  /** Why the engine starts nothing (older backends omit it). */
  waiting?: EngineWaiting;
};

export type EngineWaitingReason =
  | "ask-all"
  | "run-limit"
  | "review"
  | "decision"
  | "proposal"
  | "assigned-to-person"
  | "needs-restart"
  | "blocked";

export type EngineWaiting = {
  /** The main cause, or null when nothing waits. */
  reason: EngineWaitingReason | null;
  /** One line for people, e.g. "Waiting for your review: ...". */
  summary: string;
  /** The task to open first. */
  focus: {
    id: string;
    title: string;
    kind: "review" | "decision" | "assigned" | "restart";
  } | null;
  review: { id: string; title: string }[];
  decisions: { id: string; title: string; question: string }[];
  proposals: { id: string; reason: string | null }[];
  blocked: {
    id: string;
    title: string;
    blockedBy: { id: string; title: string; status: string }[];
  }[];
  /** Ready tasks the engine leaves alone because a person has them. */
  assignedToPeople: { id: string; title: string; assignee: string }[];
  needsRestart: { id: string; title: string; lastRun: string }[];
  autonomy: string;
  atRunLimit: boolean;
};

export function fetchEngine(): Promise<EngineStatus> {
  return request("/api/engine");
}

export function setEngine(
  action: "start" | "stop",
  options: { stopRuns?: boolean } = {},
): Promise<EngineStatus> {
  return request(
    `/api/engine/${action}`,
    {
      method: "POST",
      body: JSON.stringify({
        reason: `${action} from the GUI`,
        stopRuns: options.stopRuns ?? false,
      }),
    },
  );
}

// ---- task artifacts: files agents save on a task (orchestrators-backend/artifacts.py) ----

export type TaskArtifactFile = {
  id: string;
  taskId: string;
  runId: string | null;
  filename: string;
  kind: "html" | "markdown" | "json" | "csv" | "text" | "svg";
  contentType: string;
  size: number;
  title: string;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  /** Rendered inline and sandboxed (CSP sandbox, opaque origin); relative to apiBaseUrl(). */
  viewUrl: string;
  downloadUrl: string;
};

/** A task's artifacts in the current project, oldest first. */
export function fetchTaskArtifacts(taskId: string): Promise<TaskArtifactFile[]> {
  return request(`/api/artifacts?task_id=${encodeURIComponent(taskId)}`);
}

/** An artifact in the project-wide list, with its place among the versions of (task, filename). */
export type ProjectArtifactFile = TaskArtifactFile & {
  /** 1-based, ordered by createdAt within (taskId, filename). */
  version: number;
  versions: number;
  latest: boolean;
};

/** Every artifact of the current project, oldest first. */
export function fetchProjectArtifacts(): Promise<ProjectArtifactFile[]> {
  return request(`/api/artifacts`);
}
