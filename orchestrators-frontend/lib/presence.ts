"use client";

import { useEffect, useSyncExternalStore } from "react";
import { apiBaseUrl, fetchMe } from "@/lib/api";

/**
 * Ephemeral teammate presence (see orchestrators-backend/presence.py).
 *
 * One module-level poller per browser tab: while any component is subscribed
 * it POSTs a heartbeat ({where, typing, conversation_id}) every POLL_MS and
 * gets everyone's entries back. Typing is sent immediately and cleared after
 * TYPING_IDLE_MS without keystrokes.
 */

export type PresenceEntry = {
  email: string;
  name: string;
  /** "agent", "task:<id>", ... */
  where: string;
  typing: boolean;
  conversation_id: string | null;
  updated_at: number;
};

type Mine = { where: string; typing: boolean; conversationId: string | null };

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
  const others = entries.filter((e) => e.email !== myEmail);
  snapshot = { others, conversationId: mine.conversationId };
  listeners.forEach((listener) => listener());
}

async function send(): Promise<void> {
  if (typeof document !== "undefined" && document.hidden) return;
  try {
    const response = await fetch(`${apiBaseUrl()}/api/presence`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        where: mine.where,
        typing: mine.typing,
        conversation_id: mine.conversationId,
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
    next.conversationId !== mine.conversationId;
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
    setMine({ where, typing: false, conversationId });
    return () => {
      if (mine.where === where) {
        setMine({ where: "", typing: false, conversationId: null });
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
