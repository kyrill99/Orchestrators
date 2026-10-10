import { useCallback, useSyncExternalStore } from "react";
import { apiBaseUrl, GUI_ACTOR, type Actor } from "@/lib/api";
import { projectFetch } from "@/lib/project-scope";

export type AssigneeId =
  | "lorenz"
  | "kyrill"
  | "vanya"
  | "sergei"
  | "agent"
  | "unassigned";

export type Assignee = {
  id: AssigneeId;
  /** Short name shown in the Properties row. */
  name: string;
  /** Role line shown in the picker (humans only). */
  role?: string;
  kind: "human" | "agent" | "unassigned";
  /** Tailwind background for initials avatar. */
  color?: string;
};

export const ASSIGNEES: Assignee[] = [
  {
    id: "lorenz",
    name: "Lorenz",
    role: "AI expert",
    kind: "human",
    color: "bg-indigo-500",
  },
  {
    id: "kyrill",
    name: "Kyrill",
    role: "Design expert",
    kind: "human",
    color: "bg-rose-500",
  },
  {
    id: "vanya",
    name: "Vanya",
    role: "PM expert",
    kind: "human",
    color: "bg-amber-500",
  },
  {
    id: "sergei",
    name: "Sergei",
    role: "Expert in infrastructure",
    kind: "human",
    color: "bg-emerald-500",
  },
  {
    id: "agent",
    name: "Agent",
    kind: "agent",
  },
  {
    id: "unassigned",
    name: "Unassigned",
    kind: "unassigned",
  },
];

const byId = Object.fromEntries(ASSIGNEES.map((a) => [a.id, a])) as Record<
  AssigneeId,
  Assignee
>;

export function getAssignee(id: AssigneeId): Assignee {
  return byId[id] ?? byId.unassigned;
}

export function assigneeLabel(assignee: Assignee): string {
  if (assignee.role) return `${assignee.name} — ${assignee.role}`;
  return assignee.name;
}

/** Picker id for a backend assignee: any agent shows as "Agent", a person by
 * picker id (or email local part), anyone else as unassigned. */
export function assigneeFromActor(actor: Actor | null | undefined): AssigneeId {
  if (!actor) return "unassigned";
  if (actor.type === "agent") return "agent";
  const id = actor.id.trim().toLowerCase();
  const local = id.split("@")[0];
  const match = ASSIGNEES.find(
    (a) => a.kind === "human" && (a.id === id || a.id === local),
  );
  return match ? match.id : "unassigned";
}

/** Backend assignee for a picker id; "unassigned" is null. */
export function actorForAssignee(id: AssigneeId): Actor | null {
  const a = getAssignee(id);
  if (a.kind === "unassigned") return null;
  return { type: a.kind, id: a.id };
}

/*
 * The task's backend `assignee` is the source of truth. localStorage keeps the
 * last value seen (so graph, timeline and task page read it synchronously, and
 * it still shows when the backend is unreachable). Fetches of the graph or a
 * task call syncAssigneeFromBackend; the picker calls saveAssignee, which
 * writes through to the backend.
 */
const storageKey = (taskId: string) => `orchestrators:assignee:${taskId}`;

/** Local picks win over backend reads for a moment, so a poll that started
 * before the write landed does not flip the picker back. */
const WRITE_GRACE_MS = 5000;
const localWrites = new Map<string, number>();

export function loadAssignee(taskId: string): AssigneeId {
  if (typeof window === "undefined") return "unassigned";
  try {
    const raw = window.localStorage.getItem(storageKey(taskId));
    if (raw && raw in byId) return raw as AssigneeId;
  } catch {
    /* ignore */
  }
  return "unassigned";
}

export const ASSIGNEE_CHANGED_EVENT = "orchestrators:assignee-changed";

function storeAssignee(taskId: string, id: AssigneeId): void {
  try {
    window.localStorage.setItem(storageKey(taskId), id);
    window.dispatchEvent(
      new CustomEvent(ASSIGNEE_CHANGED_EVENT, {
        detail: { taskId, assigneeId: id },
      }),
    );
  } catch {
    /* ignore */
  }
}

/** Record the backend's assignee for a task (from a graph or task fetch). */
export function syncAssigneeFromBackend(
  taskId: string,
  actor: Actor | null | undefined,
): void {
  if (typeof window === "undefined") return;
  const wrote = localWrites.get(taskId);
  if (wrote !== undefined && Date.now() - wrote < WRITE_GRACE_MS) return;
  const next = assigneeFromActor(actor);
  if (loadAssignee(taskId) !== next) storeAssignee(taskId, next);
}

/** Assign the task in the backend (task.assign op as the GUI actor). */
export async function persistAssignee(taskId: string, id: AssigneeId): Promise<void> {
  const response = await projectFetch(`${apiBaseUrl()}/api/taskgraph/changes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      actor: GUI_ACTOR,
      reason: "Assigned from the GUI",
      ops: [{ op: "task.assign", taskId, assignee: actorForAssignee(id) }],
    }),
  });
  if (!response.ok) {
    throw new Error((await response.text()) || "Could not assign the task");
  }
}

/** The picker's change: show it at once, then write it to the backend. */
export function saveAssignee(taskId: string, id: AssigneeId): void {
  localWrites.set(taskId, Date.now());
  storeAssignee(taskId, id);
  persistAssignee(taskId, id)
    .catch((err) => console.warn("assignee not saved to the backend", err))
    .finally(() => localWrites.set(taskId, Date.now()));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(ASSIGNEE_CHANGED_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(ASSIGNEE_CHANGED_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** A task's assignee, kept current as the picker or the backend changes it. */
export function useAssignee(taskId: string): AssigneeId {
  const snapshot = useCallback(() => loadAssignee(taskId), [taskId]);
  return useSyncExternalStore(subscribe, snapshot, () => "unassigned");
}
