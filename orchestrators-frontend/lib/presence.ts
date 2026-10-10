"use client";

import { useEffect, useMemo, useSyncExternalStore } from "react";
import { apiBaseUrl, fetchMe } from "@/lib/api";
import {
  DEFAULT_PROJECT_ID,
  getCurrentProjectId,
  projectFetch,
} from "@/lib/project-scope";

/**
 * Ephemeral teammate presence (see orchestrators-backend/presence.py).
 *
 * One module-level poller per browser tab: while any component is subscribed
 * it POSTs a heartbeat ({where, typing, conversation_id}) every POLL_MS and
 * gets everyone's entries back. Typing is sent immediately and cleared after
 * TYPING_IDLE_MS without keystrokes.
 *
 * Multiplayer extras (see components/multiplayer): the selected graph node,
 * the field being edited (a soft lock) and the route, for follow mode. With
 * the multiplayer WebSocket up, changes from others also arrive instantly
 * through applyPresence(); the poll stays as heartbeat and fallback.
 */

export type PresenceEntry = {
  email: string;
  name: string;
  /** "agent", "task:<id>", ... */
  where: string;
  typing: boolean;
  conversation_id: string | null;
  updated_at: number;
  /** Project id the teammate has open (missing = "default"). */
  project?: string | null;
  /** Task id selected on a graph. */
  selection?: string | null;
  /** Field being edited at `where` (soft lock), e.g. "description". */
  editing?: string | null;
  /** Page path, for follow mode. */
  route?: string | null;
};

type Mine = {
  where: string;
  typing: boolean;
  conversationId: string | null;
  selection?: string | null;
  editing?: string | null;
  route?: string | null;
};

type Snapshot = {
  /** Other people's entries (never this browser's user). */
  others: PresenceEntry[];
  /** Conversation this tab is looking at in the agent chat. */
  conversationId: string | null;
};

const POLL_MS = 2500;
const TYPING_IDLE_MS = 2000;
const EMPTY: Snapshot = { others: [], conversationId: null };

let mine: Mine = { where: "", typing: false, conversationId: null };
let entries: PresenceEntry[] = [];
let myEmail: string | null = null;
let snapshot: Snapshot = EMPTY;
let subscribers = 0;
let pollTimer: number | undefined;
let typingTimer: number | undefined;
let meRequested = false;
const listeners = new Set<() => void>();

function rebuild() {
  const projectId = getCurrentProjectId();
  const others = entries.filter(
    (e) =>
      e.email !== myEmail && (e.project || DEFAULT_PROJECT_ID) === projectId,
  );
  snapshot = { others, conversationId: mine.conversationId };
  listeners.forEach((listener) => listener());
}

async function send(): Promise<void> {
  if (typeof document !== "undefined" && document.hidden) return;
  try {
    const response = await projectFetch(`${apiBaseUrl()}/api/presence`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        where: mine.where,
        typing: mine.typing,
        conversation_id: mine.conversationId,
        selection: mine.selection ?? null,
        editing: mine.editing ?? null,
        route: mine.route ?? null,
      }),
    });
    if (!response.ok) return;
    const data: PresenceEntry[] = await response.json();
    if (Array.isArray(data)) {
      entries = data;
      rebuild();
    }
  } catch {
    // Offline or backend restarting: try again on the next tick.
  }
}

function start() {
  if (!meRequested) {
    meRequested = true;
    fetchMe().then(
      (me) => {
        myEmail = me.email;
        rebuild();
      },
      () => {
        meRequested = false;
      },
    );
  }
  void send();
  pollTimer = window.setInterval(() => void send(), POLL_MS);
}

function stop() {
  window.clearInterval(pollTimer);
  pollTimer = undefined;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  subscribers += 1;
  if (subscribers === 1) start();
  return () => {
    listeners.delete(listener);
    subscribers -= 1;
    if (subscribers === 0) stop();
  };
}

function getSnapshot(): Snapshot {
  return snapshot;
}

function getServerSnapshot(): Snapshot {
  return EMPTY;
}

function setMine(next: Mine) {
  const changed =
    next.where !== mine.where ||
    next.typing !== mine.typing ||
    next.conversationId !== mine.conversationId ||
    (next.selection ?? null) !== (mine.selection ?? null) ||
    (next.editing ?? null) !== (mine.editing ?? null) ||
    (next.route ?? null) !== (mine.route ?? null);
  mine = next;
  if (changed) {
    rebuild();
    void send();
  }
}

/** Call on every keystroke in a chat / comment box. */
export function notifyTyping() {
  window.clearTimeout(typingTimer);
  typingTimer = window.setTimeout(stopTyping, TYPING_IDLE_MS);
  if (!mine.typing) setMine({ ...mine, typing: true });
}

/** Call after sending (or when the draft is cleared). */
export function stopTyping() {
  window.clearTimeout(typingTimer);
  if (mine.typing) setMine({ ...mine, typing: false });
}

/** Everyone else's presence entries, live. */
export function usePresenceSnapshot(): Snapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/**
 * Announce where this tab is ("agent", "task:<id>") while the component is
 * mounted, and return the other people there.
 */
export function useAnnouncePresence(
  where: string,
  conversationId: string | null = null,
): PresenceEntry[] {
  const snap = usePresenceSnapshot();
  useEffect(() => {
    setMine({ ...mine, where, typing: false, conversationId, editing: null });
    return () => {
      if (mine.where === where) {
        setMine({ ...mine, where: "", typing: false, conversationId: null, editing: null });
      }
    };
  }, [where, conversationId]);
  return snap.others.filter((e) => e.where === where);
}

/** Other people at a place, without announcing yourself (graph nodes). */
export function usePresenceAt(where: string): PresenceEntry[] {
  const snap = usePresenceSnapshot();
  return snap.others.filter((e) => e.where === where);
}

// ---- display helpers ----

const KNOWN_NAMES: Record<string, string> = {
  "heilerlorenz@gmail.com": "Lorenz",
  "kyrill.toulev.bul@gmail.com": "Kyrill",
  "sergeilupashin@gmail.com": "Sergei",
  "vanyabrucker@gmail.com": "Vanya",
};

const COLORS = [
  "#5E6AD2",
  "#E5484D",
  "#30A46C",
  "#F76B15",
  "#8E4EC6",
  "#0090FF",
  "#D6409F",
  "#AB6400",
];

export function presenceName(entry: { email: string; name: string }): string {
  const known = KNOWN_NAMES[entry.email];
  if (known) return known;
  const base = entry.name || entry.email.split("@")[0] || entry.email;
  return base ? base[0].toUpperCase() + base.slice(1) : "Someone";
}

export function presenceColor(email: string): string {
  let hash = 0;
  for (let i = 0; i < email.length; i += 1) {
    hash = (hash * 31 + email.charCodeAt(i)) | 0;
  }
  return COLORS[Math.abs(hash) % COLORS.length];
}

export function presenceInitial(entry: { email: string; name: string }): string {
  return presenceName(entry).slice(0, 1).toUpperCase();
}

/** "Lorenz is typing…", "Lorenz and Vanya are typing…" or "". */
export function typingText(people: PresenceEntry[]): string {
  const names = people.filter((p) => p.typing).map((p) => presenceName(p));
  if (names.length === 0) return "";
  if (names.length === 1) return `${names[0]} is typing…`;
  if (names.length === 2) return `${names[0]} and ${names[1]} are typing…`;
  return `${names.length} people are typing…`;
}

// ---- multiplayer extras ----

/** Everyone's entries, pushed by the multiplayer WebSocket (instant updates). */
export function applyPresence(next: PresenceEntry[]) {
  if (!Array.isArray(next)) return;
  entries = next;
  rebuild();
}

/** The graph node this tab has selected (others see an outline in your color). */
export function setSelection(selection: string | null) {
  setMine({ ...mine, selection });
}

/** Page path, so others can follow you. */
export function setRoute(route: string) {
  setMine({ ...mine, route });
}

/** The followed person's entry, or null. */
export function usePresenceOf(email: string | null): PresenceEntry | null {
  const snap = usePresenceSnapshot();
  return email ? (snap.others.find((e) => e.email === email) ?? null) : null;
}

/**
 * Soft lock for an editable field at `where` (announce `where` too, e.g. with
 * useAnnouncePresence). While `active` (your editor is open), others see
 * "<you> is editing <field>". Returns someone else editing it now, to show a
 * notice; when they stop (they saved), re-fetch to show their changes.
 */
export function useEditLock(
  where: string,
  field: string,
  active: boolean,
): PresenceEntry | null {
  const people = usePresenceAt(where);
  useEffect(() => {
    if (!active) return;
    setMine({ ...mine, editing: field });
    return () => {
      if (mine.editing === field) setMine({ ...mine, editing: null });
    };
  }, [active, field]);
  return people.find((p) => p.editing === field) ?? null;
}

export type TaskPresence = PresenceEntry & {
  /** Has the task page open. */
  open: boolean;
  /** Selected it on a graph. */
  selected: boolean;
};

/** taskId -> others with the task open or selected (graph nodes, rows). */
export function useTaskPresence(): Map<string, TaskPresence[]> {
  const { others } = usePresenceSnapshot();
  return useMemo(() => {
    const map = new Map<string, TaskPresence[]>();
    const add = (id: string, e: PresenceEntry, open: boolean, selected: boolean) => {
      const list = map.get(id) ?? [];
      const existing = list.find((x) => x.email === e.email);
      if (existing) {
        existing.open ||= open;
        existing.selected ||= selected;
      } else {
        list.push({ ...e, open, selected });
      }
      map.set(id, list);
    };
    for (const e of others) {
      if (e.where.startsWith("task:")) add(e.where.slice(5), e, true, false);
      if (e.selection) add(e.selection, e, false, true);
    }
    return map;
  }, [others]);
}

/** "Lorenz is editing description" for a task's open editors, or "". */
export function editingText(people: PresenceEntry[]): string {
  const p = people.find((x) => x.editing);
  return p ? `${presenceName(p)} is editing ${p.editing}` : "";
}
