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

export type AgentChatResponse = {
  reply: string;
  interaction_id: string;
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

export async function sendAgentMessage(
  message: string,
  previousInteractionId?: string | null,
  mode: AgentMode = "text",
): Promise<AgentChatResponse> {
  const response = await fetch(`${apiBaseUrl()}/api/agent/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      previous_interaction_id: previousInteractionId || undefined,
      mode,
    }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Agent chat failed");
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
