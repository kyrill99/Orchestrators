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
  const response = await fetch(`${apiBaseUrl()}/api/voice/token`, {
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
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      detail = JSON.parse(text).detail ?? text;
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
    response = await fetch(
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
  const response = await fetch(
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
    const response = await fetch(`${apiBaseUrl()}/api/taskgraph/graph`);
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

/** Server-Sent Events: conversation, message, thinking, resync. */
export function conversationStreamUrl(): string {
  return `${apiBaseUrl()}/api/conversations/stream`;
}
