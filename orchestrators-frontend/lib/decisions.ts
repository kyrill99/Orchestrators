import { apiBaseUrl, GUI_ACTOR, type Actor } from "@/lib/api";
import { projectFetch } from "@/lib/project-scope";

/** An earlier answer, kept when a decision is changed. */
export type DecisionRecord = {
  chosen: string;
  rationale?: string | null;
  resolvedBy?: Actor | null;
  resolvedAt?: string | null;
  auto?: boolean;
};

/**
 * A task's decision (orchestrators-backend/taskgraph/models.py DecisionInfo).
 * Decision tasks get theirs from a decision-prep run: question, options, the
 * recommended option and why. `auto`: the agent decided while the engine ran.
 */
export type TaskDecision = {
  question: string;
  options: string[];
  context?: string | null;
  recommended?: string | null;
  recommendation?: string | null;
  requestedBy?: Actor | null;
  requestedAt?: string | null;
  feedback?: string | null;
  feedbackBy?: Actor | null;
  feedbackAt?: string | null;
  state: "pending" | "resolved";
  chosen?: string | null;
  rationale?: string | null;
  resolvedBy?: Actor | null;
  resolvedAt?: string | null;
  auto?: boolean;
  custom?: boolean;
  history?: DecisionRecord[];
};

/** One entry of GET /api/taskgraph/decisions (the decision log). */
export type DecisionLogEntry = TaskDecision & { taskId: string; title: string };

/** "Agent (automatically)" or the person's id. */
export function decidedBy(d: { resolvedBy?: Actor | null; auto?: boolean }): string {
  if (d.auto) return "Agent (automatically)";
  if (!d.resolvedBy) return "someone";
  return d.resolvedBy.type === "agent" ? `agent ${d.resolvedBy.id}` : d.resolvedBy.id;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await projectFetch(`${apiBaseUrl()}/api/taskgraph${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail?.message ?? `Request failed (${res.status})`);
  }
  return res.json();
}

export type DecideResult = { started?: string[]; affected?: string[] };

/** Answer a decision (an option or your own answer), or change an earlier answer. */
export function decide(taskId: string, chosen: string, rationale: string): Promise<DecideResult> {
  return post(`/tasks/${encodeURIComponent(taskId)}/decide`, {
    actor: GUI_ACTOR,
    chosen,
    rationale: rationale.trim() || null,
  });
}

/** Send input back on a prepared decision: the agent prepares it again. */
export function sendDecisionInput(taskId: string, note: string): Promise<DecideResult> {
  return post(`/tasks/${encodeURIComponent(taskId)}/decision/feedback`, {
    actor: GUI_ACTOR,
    note,
  });
}

export async function fetchDecisions(): Promise<DecisionLogEntry[]> {
  const res = await projectFetch(`${apiBaseUrl()}/api/taskgraph/decisions`, { cache: "no-store" });
  if (!res.ok) throw new Error((await res.text()) || "Could not load decisions");
  return res.json();
}
