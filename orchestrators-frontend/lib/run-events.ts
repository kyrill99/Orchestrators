"use client";

import { apiBaseUrl } from "@/lib/api";

/**
 * Live run events from GET /api/events?channel=runs (backend events.py):
 * "step" (a new run step) and "scratchpad" (a scratchpad changed). One shared
 * EventSource per tab; components treat events as a nudge to fetch now and
 * keep slower polling as the fallback.
 */
export type RunEvent =
  | { type: "step"; data: { runId: string; seq: number; taskId: string } }
  | { type: "scratchpad"; data: { taskId: string; updatedAt: string; updatedBy: string } }
  | { type: "resync"; data: null };

type Listener = (event: RunEvent) => void;

const listeners = new Set<Listener>();
let source: EventSource | null = null;

function open() {
  if (source || typeof window === "undefined") return;
  source = new EventSource(`${apiBaseUrl()}/api/events?channel=runs`);
  for (const type of ["step", "scratchpad", "resync"] as const) {
    source.addEventListener(type, (e) => {
      let data = null;
      try {
        data = JSON.parse((e as MessageEvent).data);
      } catch {
        // resync carries no JSON body
      }
      for (const listener of listeners) listener({ type, data } as RunEvent);
    });
  }
}

/** Subscribe to run events; returns the unsubscribe function. */
export function subscribeRunEvents(listener: Listener): () => void {
  listeners.add(listener);
  open();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && source) {
      source.close();
      source = null;
    }
  };
}
