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

const storageKey = (taskId: string) => `orchestrators:assignee:${taskId}`;

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

export function saveAssignee(taskId: string, id: AssigneeId): void {
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
