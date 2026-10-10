import { apiBaseUrl } from "@/lib/api";
import { applyPresence, type PresenceEntry } from "@/lib/presence";
import { getCurrentProjectId, withProjectQuery } from "@/lib/project-scope";

/** Rooms are per project: "<project>/<location>" on the wire. */
function scoped(location: string): string {
  return location ? `${getCurrentProjectId()}/${location}` : "";
}

function unscoped(room: string): string {
  return room.slice(room.indexOf("/") + 1);
}

/**
 * The browser side of orchestrators-backend/multiplayer.py: one WebSocket per
 * tab for what presence heartbeats (lib/presence.ts) are too slow for: live
 * cursors, follow-mode viewports, and broker events ("graph", "presence")
 * pushed the moment they happen.
 *
 * If WebSockets fail twice, it falls back to one SSE stream (/api/events) for
 * the same broker channels: avatars, soft locks and live data keep working,
 * cursors and viewport mirroring do not.
 */

export type Mode = "connecting" | "ws" | "sse";

export type Cursor = {
  session: string;
  email: string;
  name: string;
  location: string;
  x: number;
  y: number;
  /** performance.now() when the frame arrived. */
  at: number;
};

export type Viewport = {
  session: string;
  email: string;
  location: string;
  x: number;
  y: number;
  zoom: number;
};

export type Snapshot = {
  mode: Mode;
  /** Email of the person we are following, if any. */
  following: string | null;
};

type ChannelListener = (type: string, data: unknown) => void;

const HEARTBEAT_MS = 20_000;
const FRAME_MS = 50; // ~20 Hz
/** SSE fallback: event type -> channel. */
const SSE_TYPES: Record<string, string> = { event: "graph", presence: "presence" };

export class MultiplayerClient {
  private snapshot: Snapshot = { mode: "connecting", following: null };
  private readonly listeners = new Set<() => void>();
  private readonly channels = new Map<string, Set<ChannelListener>>();
  private readonly viewportListeners = new Set<(v: Viewport) => void>();
  readonly cursors = new Map<string, Cursor>();

  /** Rooms joined by mounted surfaces; the last one is current. */
  private rooms: string[] = [];
  private ws: WebSocket | null = null;
  private source: EventSource | null = null;
  private wsFailures = 0;
  private retryMs = 1000;
  private heartbeat: number | undefined;
  private reconnect: number | undefined;
  private stopped = true;

  private pending = new Map<string, Record<string, unknown>>();
  private lastFrame = 0;
  private frameTimer: number | undefined;
  private frameRaf: number | undefined;

  // -- store ---------------------------------------------------------------

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = () => this.snapshot;

  private set(patch: Partial<Snapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const l of this.listeners) l();
  }

  onViewport(listener: (v: Viewport) => void) {
    this.viewportListeners.add(listener);
    return () => {
      this.viewportListeners.delete(listener);
    };
  }

  /** Broker events on `channel`; also called with ("resync", {}) on (re)connect. */
  on(channel: string, listener: ChannelListener) {
    let set = this.channels.get(channel);
    if (!set) {
      set = new Set();
      this.channels.set(channel, set);
      this.resubscribe();
    }
    set.add(listener);
    return () => {
      set.delete(listener);
      if (set.size === 0 && this.channels.get(channel) === set) {
        this.channels.delete(channel);
        this.resubscribe();
      }
    };
  }

  private emit(channel: string, type: string, data: unknown) {
    if (channel === "presence" && type === "presence") {
      applyPresence(data as PresenceEntry[]);
    }
    for (const l of this.channels.get(channel) ?? []) l(type, data);
  }

  private resyncAll() {
    for (const channel of this.channels.keys()) this.emit(channel, "resync", {});
  }

  // -- lifecycle -----------------------------------------------------------

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    // Presence updates are always wanted (they feed lib/presence.ts).
    this.channels.set("presence", this.channels.get("presence") ?? new Set());
    this.connect();
    this.heartbeat = window.setInterval(() => this.send({ t: "ping" }), HEARTBEAT_MS);
    window.addEventListener("blur", this.hideCursor);
    document.addEventListener("visibilitychange", this.onVisibility);
  }

  stop() {
    this.stopped = true;
    window.clearInterval(this.heartbeat);
    window.clearTimeout(this.reconnect);
    window.removeEventListener("blur", this.hideCursor);
    document.removeEventListener("visibilitychange", this.onVisibility);
    const ws = this.ws;
    this.ws = null;
    ws?.close();
    this.source?.close();
    this.source = null;
  }

  private connect() {
    if (this.stopped) return;
    if (this.wsFailures >= 2 || typeof WebSocket === "undefined") {
      this.connectSse();
      return;
    }
    let opened = false;
    const base = apiBaseUrl() || window.location.origin;
    const ws = new WebSocket(
      withProjectQuery(`${base.replace(/^http/, "ws")}/api/multiplayer/ws`),
    );
    this.ws = ws;
    ws.onopen = () => {
      opened = true;
      this.wsFailures = 0;
      this.retryMs = 1000;
      this.set({ mode: "ws" });
      this.resubscribe();
      this.send({ t: "join", location: scoped(this.room()) });
      this.resyncAll();
    };
    ws.onmessage = (event) => this.receive(JSON.parse(event.data));
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.cursors.clear();
      if (!opened) this.wsFailures += 1;
      this.set({ mode: "connecting" });
      window.clearTimeout(this.reconnect);
      this.reconnect = window.setTimeout(() => this.connect(), opened ? this.retryMs : 500);
      this.retryMs = Math.min(this.retryMs * 2, 15_000);
    };
  }

  private connectSse() {
    this.source?.close();
    const query = [...this.channels.keys()]
      .map((c) => `channel=${encodeURIComponent(c)}`)
      .join("&");
    const source = new EventSource(withProjectQuery(`${apiBaseUrl()}/api/events?${query}`));
    this.source = source;
    source.onopen = () => {
      this.set({ mode: "sse" });
      this.resyncAll();
    };
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED && !this.stopped) {
        window.clearTimeout(this.reconnect);
        this.reconnect = window.setTimeout(() => this.connectSse(), 3000);
      }
    };
    source.addEventListener("resync", () => this.resyncAll());
    for (const [type, channel] of Object.entries(SSE_TYPES)) {
      source.addEventListener(type, (event) => {
        this.emit(channel, type, JSON.parse((event as MessageEvent).data));
      });
    }
  }

  private send(message: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
      return true;
    }
    return false;
  }

  private resubscribe() {
    if (this.source) {
      // EventSource channels are fixed per connection: reopen with the new set.
      this.connectSse();
      return;
    }
    this.send({ t: "subscribe", channels: [...this.channels.keys()] });
  }

  private receive(message: Record<string, unknown>) {
    switch (message.t) {
      case "cursor": {
        const c = message as unknown as Cursor & { hidden?: boolean };
        c.location = unscoped(c.location);
        if (c.hidden || typeof c.x !== "number" || typeof c.y !== "number") {
          this.cursors.delete(c.session);
        } else {
          this.cursors.set(c.session, { ...c, at: performance.now() });
        }
        break;
      }
      case "leave":
        this.cursors.delete(message.session as string);
        break;
      case "viewport":
        if (message.email === this.snapshot.following) {
          const v = message as unknown as Viewport;
          v.location = unscoped(v.location);
          for (const l of this.viewportListeners) l(v);
        }
        break;
      case "event":
        if (message.type === "resync") this.resyncAll();
        else this.emit(message.channel as string, message.type as string, message.data);
        break;
    }
  }

  // -- rooms, cursors, viewports, follow -------------------------------------

  private room() {
    return this.rooms[this.rooms.length - 1] ?? "";
  }

  /** Join a cursor room while a surface is mounted; returns leave(). */
  join(location: string) {
    const before = this.room();
    this.rooms.push(location);
    if (this.room() !== before) this.send({ t: "join", location: scoped(this.room()) });
    return () => {
      const at = this.rooms.lastIndexOf(location);
      if (at >= 0) this.rooms.splice(at, 1);
      this.send({ t: "join", location: scoped(this.room()) });
    };
  }

  private onVisibility = () => {
    if (document.visibilityState === "hidden") this.hideCursor();
  };

  /** Queue a lossy frame; sent at most every FRAME_MS, batched to animation frames. */
  private frame(kind: "cursor" | "viewport", message: Record<string, unknown>) {
    if (this.snapshot.mode !== "ws") return;
    this.pending.set(kind, { t: kind, ...message });
    if (this.frameRaf !== undefined || this.frameTimer !== undefined) return;
    const wait = Math.max(0, FRAME_MS - (performance.now() - this.lastFrame));
    this.frameTimer = window.setTimeout(() => {
      this.frameTimer = undefined;
      this.frameRaf = requestAnimationFrame(() => {
        this.frameRaf = undefined;
        this.lastFrame = performance.now();
        for (const m of this.pending.values()) this.send(m);
        this.pending.clear();
      });
    }, wait);
  }

  sendCursor(location: string, x: number, y: number) {
    this.frame("cursor", { location: scoped(location), x, y });
  }

  hideCursor = () => {
    const location = this.room();
    if (location) this.frame("cursor", { location: scoped(location), hidden: true });
  };

  sendViewport(location: string, viewport: { x: number; y: number; zoom: number }) {
    this.frame("viewport", { location: scoped(location), ...viewport });
  }

  follow(email: string | null) {
    this.set({ following: email });
  }
}
